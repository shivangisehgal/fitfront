"""
SMS conversation history API routes.

GET /api/sms/conversations  -> list unique caller phone conversations
GET /api/sms/messages       -> list messages (optionally filtered by client_phone)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_, case, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.tenant import Tenant
from backend.models.caller import Caller
from backend.models.sms_message import SMSMessage, SMSDirection
from backend.services import auth_service
from backend.services.tenant_service import _tenant_to_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sms", tags=["SMS"])


@router.get("/conversations")
async def list_conversations(
    search: str = Query("", description="Search by caller name or phone number"),
    include_test: bool = Query(False, description="Include test/demo SMS data"),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    List unique SMS conversations grouped by caller phone number.
    Returns: client_phone, caller_name, message_count, last_message_at,
             last_message_body, last_direction
    """
    logger.info("[SMS] Listing conversations for tenant=%s include_test=%s search=%r",
                current_user.slug, include_test, search)
    async with async_session() as session:
        # Base filters via JOIN — tenant scoped through caller
        is_test_filter = (
            Caller.is_test == False  # noqa: E712
            if not include_test
            else Caller.is_test == True  # noqa: E712
        )

        # Group by caller_id to get one row per conversation thread
        stmt = (
            select(
                SMSMessage.caller_id,
                Caller.phone.label("caller_phone"),
                Caller.name.label("caller_name"),
                func.count(SMSMessage.id).label("message_count"),
                func.max(SMSMessage.created_at).label("last_message_at"),
            )
            .join(Caller, SMSMessage.caller_id == Caller.id)
            .where(and_(Caller.tenant_id == current_user.id, is_test_filter))
            .group_by(SMSMessage.caller_id, Caller.phone, Caller.name)
            .order_by(desc(func.max(SMSMessage.created_at)))
        )
        result = await session.execute(stmt)
        rows = result.all()

        # Apply search filter (by name or phone) in Python
        search_term = search.strip().lower() if search else ""

        conversations = []
        for row in rows:
            if search_term:
                phone_match = search_term in (row.caller_phone or "").lower()
                name_match = row.caller_name and search_term in row.caller_name.lower()
                if not phone_match and not name_match:
                    continue

            # Fetch the last message for preview
            last_msg_stmt = (
                select(SMSMessage)
                .where(and_(
                    SMSMessage.caller_id == row.caller_id,
                    is_test_filter,
                ))
                .order_by(desc(SMSMessage.created_at))
                .limit(1)
            )
            last_msg_result = await session.execute(last_msg_stmt)
            last_msg = last_msg_result.scalar_one_or_none()

            conversations.append({
                "client_phone": row.caller_phone,
                "caller_id": str(row.caller_id) if row.caller_id else None,
                "caller_name": row.caller_name,
                "message_count": row.message_count,
                "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                "last_message_body": (last_msg.body[:100] + "..." if len(last_msg.body) > 100 else last_msg.body) if last_msg else "",
                "last_direction": last_msg.direction.value if last_msg else None,
            })

        return conversations


@router.get("/messages")
async def list_messages(
    client_phone: str = Query(..., description="Caller phone number to filter by"),
    include_test: bool = Query(False, description="Include test/demo SMS data"),
    limit: int = Query(100, ge=1, le=500),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List SMS messages for a specific caller phone, ordered oldest first."""
    logger.info("[SMS] Listing messages for tenant=%s caller=%s include_test=%s", current_user.slug, client_phone, include_test)
    async with async_session() as session:
        is_test_filter = (
            Caller.is_test == False  # noqa: E712
            if not include_test
            else Caller.is_test == True  # noqa: E712
        )
        # Find caller by phone suffix match within this tenant
        from backend.services.caller_service import _phone_digits_tail, _phone_col_clean
        phone_tail = _phone_digits_tail(client_phone)
        caller_result = await session.execute(
            select(Caller).where(
                and_(
                    Caller.tenant_id == current_user.id,
                    _phone_col_clean(Caller.phone).endswith(phone_tail),
                )
            ).limit(1)
        )
        caller_rec = caller_result.scalar_one_or_none()

        if not caller_rec:
            return []

        msg_filters = [
            SMSMessage.caller_id == caller_rec.id,
            is_test_filter,
        ]

        stmt = (
            select(SMSMessage)
            .join(Caller, SMSMessage.caller_id == Caller.id)
            .where(and_(*msg_filters))
            .order_by(SMSMessage.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = result.scalars().all()

        return [
            {
                "id": str(msg.id),
                "direction": msg.direction.value,
                "from_number": msg.from_number,
                "to_number": msg.to_number,
                "body": msg.body,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
                "sender_type": getattr(msg, "sender_type", "agent") or "agent",
            }
            for msg in messages
        ]


# ── Send custom message ───────────────────────────────────────────────────────


class SendMessageRequest(BaseModel):
    to: str
    message: str


@router.post("/send")
async def send_message(
    payload: SendMessageRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Send a custom outbound SMS from the admin dashboard.
    Saves with sender_type='admin' so the UI can distinguish it from AI-sent messages.
    Test phone numbers (+1555...) are guarded — no real Twilio call is made.
    """
    to = payload.to.strip()
    message = payload.message.strip()
    if not to:
        raise HTTPException(status_code=400, detail="Recipient phone number is required")
    if not message:
        raise HTTPException(status_code=400, detail="Message body is required")

    is_test = to.startswith("+1555")
    tenant_ctx = _tenant_to_context(current_user)

    from backend.services import sms_service
    ok = sms_service.send_custom_sms(
        to=to,
        message=message,
        tenant_ctx=tenant_ctx,
        is_test=is_test,
        sender_type="admin",
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to send message — check Twilio credentials")

    logger.info("[SMS] Admin manual send from tenant=%s to=%s is_test=%s", current_user.slug, to, is_test)
    return {"success": True, "to": to, "is_test": is_test}
