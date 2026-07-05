"""
Tenant admin API routes — onboarding, approval, and management.

POST   /api/tenants                 → submit onboarding request (PENDING)
GET    /api/tenants                 → list all tenants (admin)
GET    /api/tenants/{id}            → get single tenant detail
PUT    /api/tenants/{id}            → update tenant config
POST   /api/tenants/{id}/approve    → approve PENDING → ACTIVE
POST   /api/tenants/{id}/suspend    → suspend an active tenant
POST   /api/tenants/{id}/reactivate → reactivate a suspended tenant
DELETE /api/tenants/{id}            → deactivate a tenant (soft delete)
DELETE /api/tenants/{id}/purge      → permanently delete tenant + all data (admin)
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.defaults import DEFAULT_TIMEZONE
from backend.services import tenant_service, auth_service
from backend.services.usage_service import get_usage_summary, get_plan_limits
from backend.models.tenant import Tenant, TenantStatus, BusinessType, PlanTier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenants", tags=["Tenants"])


# ── Request / Response schemas ───────────────────────────────────────────────


class TenantOnboardRequest(BaseModel):
    """Payload for a new client onboarding request."""
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9_-]+$")
    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: str = Field(default="custom")
    business_address: str = Field(..., min_length=5)
    owner_name: str = Field(..., min_length=2, max_length=255)
    owner_email: str = Field(..., max_length=255)
    owner_phone: str = Field(..., min_length=5)
    timezone: str = Field(..., min_length=1)
    plan: str = Field(default="starter")


class TenantUpdateRequest(BaseModel):
    """Partial update for tenant configuration."""
    business_name: Optional[str] = None
    business_type: Optional[str] = None
    business_phone: Optional[str] = None
    business_address: Optional[str] = None
    business_website: Optional[str] = None
    google_maps_url: Optional[str] = None
    timezone: Optional[str] = None
    agent_name: Optional[str] = None
    greeting_message: Optional[str] = None
    system_prompt_override: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_phone_number: Optional[str] = None
    escalation_phone: Optional[str] = None
    escalation_transfer_number: Optional[str] = None
    appointment_types: Optional[list[dict[str, Any]]] = None
    business_hours: Optional[dict[str, Any]] = None
    # List of {"date": "YYYY-MM-DD", "name": "Christmas Day"} entries.
    # Admin manages closures here — service layer normalises & dedupes.
    holidays: Optional[list[dict[str, Any]]] = None
    knowledge_base: Optional[dict[str, Any]] = None
    emergency_guidance: Optional[str] = None
    demo_mode: Optional[bool] = None
    plan: Optional[str] = None
    # Agent on/off — owner or admin can toggle
    agent_active: Optional[bool] = None
    # Feature flags (per-tenant toggles — admin only)
    feature_twilio_enabled: Optional[bool] = None


class TenantOut(BaseModel):
    """Response schema for a tenant."""
    id: str
    slug: str
    business_name: str
    business_type: Optional[str]
    business_address: Optional[str]
    google_maps_url: Optional[str]
    owner_name: str
    owner_email: str
    owner_phone: Optional[str]
    timezone: str
    plan: Optional[str]
    status: str
    agent_active: bool
    demo_mode: bool
    agent_name: Optional[str]
    greeting_message: Optional[str]
    # Integration status (show whether configured, don't expose keys)
    twilio_configured: bool
    # Per-tenant Twilio phone number (assigned by admin under Option A)
    twilio_phone_number: Optional[str]
    google_calendar_connected: bool
    google_calendar_email: Optional[str]
    # Feature flags — raw per-tenant toggles (admin-editable)
    feature_twilio_enabled: Optional[bool]
    # Feature flags — effective state (global AND per-tenant)
    twilio_enabled: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


def _tenant_to_out(t: Tenant) -> TenantOut:
    """Convert a Tenant ORM object to a TenantOut response."""
    return TenantOut(
        id=str(t.id),
        slug=t.slug,
        business_name=t.business_name,
        business_type=t.business_type.value if t.business_type else None,
        business_address=t.business_address or "",
        google_maps_url=t.google_maps_url or "",
        owner_name=t.owner_name,
        owner_email=t.owner_email,
        owner_phone=t.owner_phone,
        timezone=t.timezone or DEFAULT_TIMEZONE,
        plan=t.plan.value if t.plan else None,
        status=t.status.value if t.status else "PENDING",
        agent_active=bool(t.agent_active) if t.agent_active is not None else True,
        demo_mode=t.demo_mode if t.demo_mode is not None else True,
        agent_name=t.agent_name,
        greeting_message=t.greeting_message,
        # Twilio is "configured" if the platform has credentials AND a phone
        # number is assigned to this tenant.
        twilio_configured=bool(
            (t.twilio_account_sid or settings.TWILIO_ACCOUNT_SID) and
            (t.twilio_auth_token or settings.TWILIO_AUTH_TOKEN) and
            (t.twilio_phone_number or settings.TWILIO_PHONE_NUMBER)
        ),
        twilio_phone_number=t.twilio_phone_number or "",
        google_calendar_connected=bool(t.google_calendar_connected),
        google_calendar_email=t.google_calendar_email or "",
        # Feature flags — raw per-tenant toggles
        feature_twilio_enabled=t.feature_twilio_enabled,
        # Feature flags — effective = global AND per-tenant
        twilio_enabled=settings.FEATURE_TWILIO_ENABLED and (t.feature_twilio_enabled if t.feature_twilio_enabled is not None else True),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("", response_model=TenantOut, status_code=201)
async def onboard_tenant(req: TenantOnboardRequest):
    """
    Submit a new client onboarding request.
    Creates a tenant in PENDING status — requires admin approval.
    """
    logger.info("[Tenants] Onboarding request: slug=%s, business=%s, owner=%s",
                req.slug, req.business_name, req.owner_email)

    # Check for slug collision
    existing = await tenant_service.resolve_by_slug(req.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Slug '{req.slug}' is already taken.")

    tenant = await tenant_service.create_tenant(req.model_dump())
    logger.info("[Tenants] Created PENDING tenant: %s (%s)", tenant.slug, tenant.business_name)
    return _tenant_to_out(tenant)


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, ACTIVE, SUSPENDED, etc."),
    admin: Tenant = Depends(auth_service.require_admin),
):
    """List all tenants, optionally filtered by status. (Admin only)"""
    logger.info("[Tenants] Listing tenants (status=%s)", status)
    status_filter = None
    if status:
        try:
            status_filter = TenantStatus(status.upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    tenants = await tenant_service.list_tenants(status=status_filter)
    return [_tenant_to_out(t) for t in tenants]


# ── Usage / billing endpoints ───────────────────────────────────────────────
# NOTE: These MUST be defined before /{tenant_id} routes, otherwise FastAPI
# matches "usage" and "plans" as a tenant_id path parameter.


@router.get("/usage")
async def get_my_usage(user=Depends(auth_service.get_current_user)):
    """Return current billing-period usage for the authenticated tenant."""
    tenant_id = getattr(user, "id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated with this account.")
    summary = await get_usage_summary(tenant_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return summary


@router.get("/plans")
async def list_plans():
    """Return all available plans and their limits (public endpoint)."""
    from backend.services.usage_service import PLAN_LIMITS
    return {
        plan: {
            "call_minutes": limits["call_minutes"],
            "sms": limits["sms"],
        }
        for plan, limits in PLAN_LIMITS.items()
    }


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Get a single tenant by ID. Tenants can only fetch their own; admins can fetch any."""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    if not current_user.is_admin and current_user.id != uid:
        raise HTTPException(status_code=403, detail="You can only access your own tenant.")

    tenant = await tenant_service.get_tenant(uid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return _tenant_to_out(tenant)


@router.put("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str,
    req: TenantUpdateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Update tenant configuration fields.
    Tenants can update their own config; admins can update any.
    """
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    if not current_user.is_admin and current_user.id != uid:
        raise HTTPException(status_code=403, detail="You can only update your own tenant.")

    # Only include explicitly set fields
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Server-side slug generation + deduplication for appointment types
    if "appointment_types" in update_data and isinstance(update_data["appointment_types"], list):
        from backend.routes.dashboard import _normalise_appointment_types
        update_data["appointment_types"] = _normalise_appointment_types(update_data["appointment_types"])

    tenant = await tenant_service.update_tenant(uid, update_data)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    logger.info("[Tenants] Updated tenant %s: %s", tenant.slug, list(update_data.keys()))
    return _tenant_to_out(tenant)


@router.post("/{tenant_id}/approve", response_model=TenantOut)
async def approve_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """
    Admin approves a PENDING tenant → ACTIVE.
    Only works for PENDING tenants. (Admin only)
    """
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    tenant = await tenant_service.approve_tenant(uid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    if tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: tenant is in {tenant.status.value} state.",
        )

    logger.info("[Tenants] Approved tenant %s → ACTIVE", tenant.slug)
    return _tenant_to_out(tenant)


@router.post("/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Suspend an active tenant (billing issue, abuse, etc.). (Admin only)"""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    tenant = await tenant_service.get_tenant(uid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    if tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Can only suspend ACTIVE tenants (current: {tenant.status.value}).",
        )

    updated = await tenant_service.update_tenant(uid, {"status": "SUSPENDED"})
    # Manually set status since update_tenant doesn't handle status transitions
    from backend.database import async_session
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == uid))
        t = result.scalar_one_or_none()
        if t:
            t.status = TenantStatus.SUSPENDED
            await session.commit()
            await session.refresh(t)
            tenant_service.invalidate_cache(str(uid))
            logger.info("[Tenants] Suspended tenant %s", t.slug)
            return _tenant_to_out(t)

    raise HTTPException(status_code=500, detail="Failed to suspend tenant.")


@router.post("/{tenant_id}/reactivate", response_model=TenantOut)
async def reactivate_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Reactivate a suspended or deactivated tenant. (Admin only)"""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    from backend.database import async_session
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == uid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        if tenant.status not in (TenantStatus.SUSPENDED, TenantStatus.DEACTIVATED):
            raise HTTPException(
                status_code=400,
                detail=f"Can only reactivate SUSPENDED or DEACTIVATED tenants (current: {tenant.status.value}).",
            )

        tenant.status = TenantStatus.ACTIVE
        await session.commit()
        await session.refresh(tenant)
        tenant_service.invalidate_cache(str(uid))
        logger.info("[Tenants] Reactivated tenant %s → ACTIVE", tenant.slug)
        return _tenant_to_out(tenant)


@router.delete("/{tenant_id}", response_model=TenantOut)
async def deactivate_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Soft-delete: set tenant status to DEACTIVATED. (Admin only)"""
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    from backend.database import async_session
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == uid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        tenant.status = TenantStatus.DEACTIVATED
        await session.commit()
        await session.refresh(tenant)
        tenant_service.invalidate_cache(str(uid))
        logger.info("[Tenants] Deactivated tenant %s", tenant.slug)
        return _tenant_to_out(tenant)


@router.delete("/{tenant_id}/purge", status_code=200)
async def purge_tenant(
    tenant_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """
    Permanently delete a tenant and ALL associated data (appointments, callers,
    calls, SMS, waitlist, providers, profile change logs). This frees the email
    so the person can re-register. Irreversible. (Admin only)
    """
    try:
        uid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant ID format.")

    # Prevent admin from deleting themselves
    if admin.id == uid:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    from backend.models import (
        Appointment, Call, Caller, Trainer, WaitlistEntry, SMSMessage, ProfileChangeLog,
        SupportTicket,
    )
    from backend.database import async_session

    async with async_session() as session:
        # Verify tenant exists
        result = await session.execute(select(Tenant).where(Tenant.id == uid))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        tenant_name = tenant.business_name
        tenant_email = tenant.owner_email
        tenant_slug = tenant.slug

        # Build caller_id subquery first — Appointment/WaitlistEntry/SMSMessage
        # derive tenant scope through caller_id, so we must delete child rows
        # BEFORE deleting callers (otherwise the FK is gone).
        tenant_caller_ids = select(Caller.id).where(Caller.tenant_id == uid)

        await session.execute(delete(Appointment).where(Appointment.caller_id.in_(tenant_caller_ids)))
        await session.execute(delete(WaitlistEntry).where(WaitlistEntry.caller_id.in_(tenant_caller_ids)))
        await session.execute(delete(SMSMessage).where(SMSMessage.caller_id.in_(tenant_caller_ids)))

        # Now safe to delete callers (child FKs gone)
        await session.execute(delete(Caller).where(Caller.tenant_id == uid))
        await session.execute(delete(Call).where(Call.tenant_id == uid))
        await session.execute(delete(Trainer).where(Trainer.tenant_id == uid))
        await session.execute(delete(ProfileChangeLog).where(ProfileChangeLog.tenant_id == uid))
        await session.execute(delete(SupportTicket).where(SupportTicket.tenant_id == uid))

        # Delete the tenant itself
        await session.execute(delete(Tenant).where(Tenant.id == uid))
        await session.commit()

        tenant_service.invalidate_cache(str(uid))
        logger.info("[Tenants] PURGED tenant %s (%s / %s) — all data permanently deleted",
                    tenant_slug, tenant_name, tenant_email)

    return {
        "detail": f"Tenant '{tenant_name}' ({tenant_email}) permanently deleted.",
        "slug": tenant_slug,
        "email": tenant_email,
    }


