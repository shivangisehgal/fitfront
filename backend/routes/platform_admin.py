"""
Platform-level admin routes — global configuration and rich tenant management.

All routes require admin authentication.

Endpoints:
  GET  /api/admin/platform/config              — read global platform settings
  PUT  /api/admin/platform/config              — update global platform settings (Bolna AI)
  GET  /api/admin/tenants/{id}                 — rich tenant integration + status detail
  GET  /api/admin/tenants/{id}/usage           — tenant usage summary
  POST /api/admin/tenants/{id}/integrations    — update tenant Twilio + agent flags
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.database import async_session
from backend.models.platform_config import PlatformConfig
from backend.models.tenant import Tenant
from backend.services import auth_service, bolna_service, tenant_service
from backend.services.usage_service import get_usage_summary
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mask_secret(secret: str | None) -> str:
    """Show only the last 4 chars of a secret for display confirmation."""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "•" * len(secret)
    return "•" * (len(secret) - 4) + secret[-4:]


async def _get_platform_config() -> dict[str, str]:
    """Return all platform_config rows as a dict."""
    async with async_session() as session:
        rows = (await session.execute(select(PlatformConfig))).scalars().all()
        return {row.key: (row.value or "") for row in rows}


async def _set_platform_config(key: str, value: str) -> None:
    """Upsert a single platform_config key."""
    async with async_session() as session:
        existing = (
            await session.execute(select(PlatformConfig).where(PlatformConfig.key == key))
        ).scalar_one_or_none()

        if existing:
            existing.value = value
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session.add(PlatformConfig(key=key, value=value))

        await session.commit()


# ── Platform config endpoints ─────────────────────────────────────────────────


@router.get("/platform/config")
async def get_platform_config(
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """Return global platform settings (Bolna AI credentials, masked)."""
    cfg = await _get_platform_config()
    api_key = cfg.get("bolna_api_key", "")
    agent_id = cfg.get("bolna_agent_id", "")

    return {
        "bolna_api_key_masked": _mask_secret(api_key),
        "bolna_agent_id": agent_id,
        "bolna_configured": bool(api_key and agent_id),
    }


class PlatformConfigUpdate(BaseModel):
    bolna_api_key: Optional[str] = None   # omit or send masked value to preserve
    bolna_agent_id: Optional[str] = None


@router.put("/platform/config")
async def update_platform_config(
    body: PlatformConfigUpdate,
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """Update global platform settings. Skip any masked (•-containing) values."""
    updated = []

    if body.bolna_api_key is not None:
        if "•" not in body.bolna_api_key:  # skip masked round-trip values
            await _set_platform_config("bolna_api_key", body.bolna_api_key.strip())
            updated.append("bolna_api_key")

    if body.bolna_agent_id is not None:
        await _set_platform_config("bolna_agent_id", body.bolna_agent_id.strip())
        updated.append("bolna_agent_id")

    logger.info("[PlatformAdmin] %s updated platform config: %s", admin.slug, updated)

    cfg = await _get_platform_config()
    api_key = cfg.get("bolna_api_key", "")
    agent_id = cfg.get("bolna_agent_id", "")
    return {
        "status": "saved",
        "updated_fields": updated,
        "bolna_api_key_masked": _mask_secret(api_key),
        "bolna_agent_id": agent_id,
        "bolna_configured": bool(api_key and agent_id),
    }


# ── Bolna connection test ─────────────────────────────────────────────────────


@router.get("/platform/bolna/test")
async def test_bolna_connection(
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """
    Validate saved Bolna credentials against the live API.

    Calls GET /agent/{agent_id} — read-only, no calls placed, no billing.
    Returns {"ok": bool, "message": str, "agent_name"?: str}.
    """
    cfg = await _get_platform_config()
    api_key = cfg.get("bolna_api_key", "")
    agent_id = cfg.get("bolna_agent_id", "")

    if not api_key or not agent_id:
        return {"ok": False, "message": "Bolna credentials not configured — save API key and Agent ID first."}

    result = await bolna_service.test_connection(api_key=api_key, agent_id=agent_id)
    logger.info("[PlatformAdmin] Bolna connection test by %s: ok=%s", admin.slug, result.get("ok"))
    return result


# ── Tenant management endpoints ───────────────────────────────────────────────


@router.get("/tenants/{tenant_id}")
async def admin_get_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """
    Rich tenant integration + status detail for the admin panel.

    Returns integration status, feature flags, plan info, and contact details.
    """
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID.")

    tenant = await tenant_service.get_tenant(uid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    return {
        "tenant_id": str(tenant.id),
        "slug": tenant.slug,
        "business_name": tenant.business_name,
        "business_type": tenant.business_type.value if tenant.business_type else None,
        "owner_name": tenant.owner_name,
        "owner_email": tenant.owner_email,
        "owner_phone": tenant.owner_phone or "",
        "business_phone": tenant.business_phone or "",
        "business_address": tenant.business_address or "",
        "business_website": tenant.business_website or "",
        "google_maps_url": tenant.google_maps_url or "",
        "timezone": tenant.timezone or "America/Chicago",
        "plan": tenant.plan.value if tenant.plan else "starter",
        "status": tenant.status.value if tenant.status else "PENDING",
        "demo_mode": bool(tenant.demo_mode),
        # Integration status
        "twilio_configured": bool(tenant.twilio_phone_number),
        "twilio_phone_number": tenant.twilio_phone_number or "",
        "google_calendar_connected": bool(tenant.google_calendar_connected),
        "google_calendar_email": tenant.google_calendar_email or "",
        # Agent / feature flags
        "agent_active": bool(tenant.agent_active) if tenant.agent_active is not None else True,
        "feature_twilio_enabled": bool(tenant.feature_twilio_enabled) if tenant.feature_twilio_enabled is not None else True,
        # Timestamps
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        "last_login_at": tenant.last_login_at.isoformat() if tenant.last_login_at else None,
    }


@router.get("/tenants/{tenant_id}/usage")
async def admin_get_tenant_usage(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """Return the current billing-period usage summary for a tenant."""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID.")

    summary = await get_usage_summary(uid)
    if not summary:
        raise HTTPException(status_code=404, detail="Tenant not found or no usage data.")

    return summary


class TenantIntegrationsUpdate(BaseModel):
    twilio_phone_number: Optional[str] = None
    feature_twilio_enabled: Optional[bool] = None
    agent_active: Optional[bool] = None


@router.post("/tenants/{tenant_id}/integrations")
async def admin_update_tenant_integrations(
    tenant_id: str,
    body: TenantIntegrationsUpdate,
    admin: Tenant = Depends(auth_service.require_admin),
) -> dict:
    """Update a tenant's Twilio phone number, feature flags, and agent status."""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID.")

    update_fields: dict = {}
    if body.twilio_phone_number is not None:
        update_fields["twilio_phone_number"] = body.twilio_phone_number.strip() or None
    if body.feature_twilio_enabled is not None:
        update_fields["feature_twilio_enabled"] = body.feature_twilio_enabled
    if body.agent_active is not None:
        update_fields["agent_active"] = body.agent_active

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update.")

    tenant = await tenant_service.update_tenant(uid, update_fields)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    logger.info("[PlatformAdmin] %s updated integrations for %s: %s",
                admin.slug, tenant.slug, list(update_fields.keys()))

    return {
        "status": "saved",
        "tenant_slug": tenant.slug,
        "twilio_phone_number": tenant.twilio_phone_number or "",
        "feature_twilio_enabled": bool(tenant.feature_twilio_enabled),
        "agent_active": bool(tenant.agent_active),
    }
