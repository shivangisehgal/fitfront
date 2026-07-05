"""
Bolna AI routes.

Endpoints:
  POST /api/bolna/call          — direct Bolna-only outbound call
  GET  /api/bolna/logs          — fetch Bolna execution logs
  POST /api/calls/outbound      — trigger outbound call via Bolna
  GET  /api/bolna/caller-lookup — inbound pre-call hook (called by Bolna, no auth)
  POST /api/bolna/webhook       — call-completion webhook (called by Bolna, no auth)
                                  Bolna IP: 13.203.39.153
"""
from __future__ import annotations


import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from backend.config import settings
from backend.database import async_session
from backend.models.call import Call, CallOutcome
from backend.models.caller import Caller
from backend.models.platform_config import PlatformConfig
from backend.models.tenant import Tenant
from backend.services import auth_service
from backend.services import bolna_service
from backend.services.tenant_service import resolve_default_tenant
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Calling"])


class CallRequest(BaseModel):
    phone: str
    name: str = "Student"
    # Optional appointment context — injected as Bolna user_data variables
    # Use the same key names as {placeholders} in your Bolna agent prompt
    context: dict | None = None  # e.g. {"date": "Mon Jun 24", "time": "3:00 PM", "faculty": "Prof. Smith"}


async def _get_bolna_credentials() -> tuple[str, str]:
    """
    Resolve global Bolna credentials.

    Priority:
      1. platform_config DB table (admin-editable at runtime)
      2. Environment variable fallbacks (BOLNA_API_KEY / BOLNA_AGENT_ID)

    Returns (api_key, agent_id).  Either may be empty string if not set.
    """
    api_key = settings.BOLNA_API_KEY
    agent_id = settings.BOLNA_AGENT_ID

    try:
        async with async_session() as session:
            result = await session.execute(
                select(PlatformConfig).where(
                    PlatformConfig.key.in_(["bolna_api_key", "bolna_agent_id"])
                )
            )
            rows = result.scalars().all()
            for row in rows:
                if row.key == "bolna_api_key" and row.value:
                    api_key = row.value
                elif row.key == "bolna_agent_id" and row.value:
                    agent_id = row.value
    except Exception as exc:
        logger.warning("[Bolna] Could not read platform_config: %s — using env fallback", exc)

    return api_key, agent_id


# ── Direct Bolna endpoint ──────────────────────────────────────────────────────


@router.post("/api/bolna/call")
async def bolna_call(
    body: CallRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
) -> dict[str, Any]:
    """Trigger an outbound Bolna AI call using global platform credentials."""
    api_key, agent_id = await _get_bolna_credentials()

    if not api_key or not agent_id:
        raise Exception("Bolna not configured — admin must set API key and Agent ID in Platform Settings.")

    user_data = {"name": body.name, **(body.context or {})}
    return await bolna_service.call_contact(
        api_key=api_key,
        agent_id=agent_id,
        phone=body.phone,
        name=body.name,
        user_data=user_data,
    )


@router.get("/api/bolna/logs")
async def bolna_logs(
    current_user: Tenant = Depends(auth_service.get_current_user),
) -> list[dict]:
    """Fetch recent Bolna call execution logs using global platform credentials."""
    api_key, agent_id = await _get_bolna_credentials()

    if not api_key:
        return []

    return await bolna_service.get_call_logs(
        api_key=api_key,
        agent_id=agent_id,
    )


# ── Unified outbound calling endpoint ─────────────────────────────────────────


@router.post("/api/calls/outbound")
async def outbound_call(
    body: CallRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
) -> dict[str, Any]:
    """
    Outbound call endpoint via Bolna AI.

    Raises an error with "not configured" in the message so the frontend
    can fall back to a tel: link when credentials are missing.
    """
    api_key, agent_id = await _get_bolna_credentials()

    if not api_key or not agent_id:
        raise Exception(
            "Calling platform not configured — admin must set Bolna credentials in Platform Settings."
        )

    user_data = {"name": body.name, **(body.context or {})}
    logger.info("[Outbound] Using Bolna for %s, context keys: %s", body.phone, list(user_data.keys()))
    data = await bolna_service.call_contact(
        api_key=api_key,
        agent_id=agent_id,
        phone=body.phone,
        name=body.name,
        user_data=user_data,
    )
    return {"platform": "bolna", **data}


# ── Inbound pre-call hook (no auth — called by Bolna before the call starts) ──


@router.get("/api/bolna/caller-lookup")
async def bolna_caller_lookup(
    contact_number: str = Query(..., description="Caller E.164 phone from Bolna"),
    agent_id: str = Query(default="", description="Bolna agent ID"),
    execution_id: str = Query(default="", description="Bolna execution ID"),
) -> dict[str, Any]:
    """
    Bolna calls this endpoint BEFORE the call starts (Inbound Tab → Internal APIs).
    We look up the caller in our DB and return their name + context so Bolna can
    inject the data as prompt variables ({{contact_number}}, {{caller_name}}, etc.).

    No authentication — Bolna doesn't support passing a dynamic Bearer token here.
    The endpoint is read-only and returns only the caller name, not sensitive data.
    """
    logger.info("[Bolna Lookup] incoming caller: %s (agent=%s exec=%s)",
                contact_number, agent_id, execution_id)
    try:
        from backend.services import caller_service
        tenant_ctx = await resolve_default_tenant()
        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
        history = await caller_service.get_caller_history(
            contact_number,
            tenant_id=tenant_id,
            tz_name=tenant_ctx.timezone if tenant_ctx else None,
        )
        if history:
            caller = history["caller"]
            logger.info("[Bolna Lookup] Found caller: %s (%d visits)", caller["name"], caller["visit_count"])
            return {
                "contact_number": contact_number,
                "caller_name": caller["name"],
                "visit_count": str(caller["visit_count"]),
                "is_returning": "yes" if caller["visit_count"] > 0 else "no",
            }
    except Exception as exc:
        logger.warning("[Bolna Lookup] Caller lookup failed: %s", exc)

    # New caller — return minimal data
    return {
        "contact_number": contact_number,
        "caller_name": "there",   # agent says "Hello there" for new callers
        "visit_count": "0",
        "is_returning": "no",
    }


# ── Call-completion webhook (no auth — called by Bolna after the call ends) ───
# Bolna sends from IP 13.203.39.153


@router.post("/api/bolna/webhook")
async def bolna_webhook(request: Request) -> dict[str, str]:
    """
    Receives Bolna call-completion webhooks and logs them as Call records.

    Bolna POSTs the execution object here whenever call status changes
    (queued → in-progress → completed). We only persist on terminal statuses.

    Payload shape mirrors GET /executions/{id}:
      id, agent_id, status, transcript, conversation_duration,
      telephony_data.{from_number, to_number, call_type, duration, recording_url}
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("[Bolna Webhook] Failed to parse body")
        return {"status": "ignored"}

    execution_id = body.get("id", "")
    status = body.get("status", "")

    logger.info("[Bolna Webhook] execution_id=%s status=%s", execution_id, status)

    # Only persist terminal/meaningful statuses
    TERMINAL = {"completed", "failed", "no-answer", "busy", "canceled", "error", "call-disconnected"}
    if status not in TERMINAL:
        return {"status": "acknowledged"}

    try:
        telephony = body.get("telephony_data") or {}
        call_type = telephony.get("call_type", "outbound")

        # For inbound: caller is from_number; for outbound: caller is to_number
        caller_number = (
            telephony.get("from_number") if call_type == "inbound"
            else telephony.get("to_number")
        ) or ""

        duration_raw = telephony.get("duration") or body.get("conversation_duration") or 0
        try:
            duration_seconds = int(float(str(duration_raw)))
        except (ValueError, TypeError):
            duration_seconds = None

        # Map Bolna status → our CallOutcome enum
        outcome_map = {
            "completed": CallOutcome.BOOKED,     # updated below if we can infer better
            "failed": CallOutcome.ABANDONED,
            "no-answer": CallOutcome.ABANDONED,
            "busy": CallOutcome.ABANDONED,
            "canceled": CallOutcome.ABANDONED,
            "error": CallOutcome.ABANDONED,
            "call-disconnected": CallOutcome.ABANDONED,
        }
        outcome = outcome_map.get(status, CallOutcome.INQUIRY)

        # Better outcome inference from transcript
        transcript_text = body.get("transcript") or ""
        if status == "completed":
            tl = transcript_text.lower()
            if any(w in tl for w in ["book", "booked", "appointment", "schedule", "confirmed"]):
                outcome = CallOutcome.BOOKED
            elif any(w in tl for w in ["cancel", "cancelled"]):
                outcome = CallOutcome.CANCELLED
            elif any(w in tl for w in ["transfer", "human", "staff", "callback"]):
                outcome = CallOutcome.ESCALATED
            else:
                outcome = CallOutcome.INQUIRY

        # Resolve tenant
        tenant_ctx = await resolve_default_tenant()
        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None

        async with async_session() as session:
            # Deduplicate: skip if we already have this execution_id
            existing = await session.execute(
                select(Call).where(Call.vapi_call_id == execution_id)
            )
            if existing.scalar_one_or_none():
                logger.info("[Bolna Webhook] Duplicate execution_id=%s — skipping", execution_id)
                return {"status": "duplicate"}

            # Resolve caller_id via phone match (for test-data filtering in dashboard)
            caller_id = None
            if caller_number and tenant_id:
                from backend.services.caller_service import _phone_digits_tail, _phone_col_clean
                from sqlalchemy import and_
                tail = _phone_digits_tail(caller_number)
                caller_q = await session.execute(
                    select(Caller).where(
                        and_(
                            Caller.tenant_id == tenant_id,
                            _phone_col_clean(Caller.phone).endswith(tail),
                        )
                    ).limit(1)
                )
                caller_rec = caller_q.scalar_one_or_none()
                if caller_rec:
                    caller_id = caller_rec.id

            call = Call(
                tenant_id=tenant_id,
                vapi_call_id=execution_id,          # reuse vapi_call_id column for bolna execution_id
                caller_number=caller_number or None,
                caller_id=caller_id,
                duration_seconds=duration_seconds,
                outcome=outcome,
                transcript=[{"role": "full", "content": transcript_text}] if transcript_text else [],
                summary=body.get("extracted_data") and str(body["extracted_data"]) or None,
                ended_at=datetime.now(timezone.utc),
            )
            session.add(call)
            await session.commit()

        logger.info("[Bolna Webhook] Saved Call record: exec=%s caller=%s outcome=%s duration=%ss",
                    execution_id, caller_number, outcome, duration_seconds)
        return {"status": "saved"}

    except Exception as exc:
        logger.error("[Bolna Webhook] Failed to save call: %s", exc, exc_info=True)
        return {"status": "error"}
