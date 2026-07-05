"""
Authentication API — login, logout, registration with password, and current user info.

POST  /api/auth/login            → email + password → JWT
POST  /api/auth/register         → register a new tenant (PENDING) with password
GET   /api/auth/me               → current authenticated user
PATCH /api/auth/profile          → update own profile (name only, email immutable)
POST  /api/auth/change-password  → change own password
GET   /api/auth/profile-changes  → change history (admin sees all, tenant sees own)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

# Lightweight email regex — covers the common cases without requiring the
# blocked email-validator package. (Good enough for login/registration.)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _validate_email(value: str) -> str:
    if not isinstance(value, str) or not _EMAIL_RE.match(value):
        raise ValueError("invalid email address")
    return value.lower().strip()

from backend.config import settings
from backend.database import async_session
from backend.defaults import DEFAULT_TIMEZONE
from backend.models.tenant import Tenant, TenantStatus
from backend.services import auth_service, tenant_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _validate_email(v)


class RegisterRequest(BaseModel):
    """New tenant registration — extends the onboarding payload with a password."""
    # Slug is auto-generated from business_name; not shown in UI.
    slug: Optional[str] = Field(None, max_length=100, pattern=r"^[a-z0-9_-]+$")
    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: str = "custom"
    business_address: str = Field(..., min_length=5)
    owner_name: str = Field(..., min_length=2, max_length=255)
    owner_email: str
    owner_phone: str = Field(..., min_length=5)
    # Required at signup so the agent always has a real human to fall back on
    # in an emergency / escalation scenario. Emergency guidance text is
    # collected later from the Agent Config page.
    escalation_phone: str = Field(..., min_length=5, max_length=20)
    # Required: a Google Maps link to the office location. We use this for
    # admin approval review (iframe preview) and to send callers a verified
    # map link inside SMS / email templates.
    google_maps_url: str = Field(..., min_length=10, max_length=2048)
    timezone: str = Field(..., min_length=1)
    plan: str = "starter"
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("google_maps_url")
    @classmethod
    def _v_google_maps_url(cls, v: str) -> str:
        v = (v or "").strip()
        # Accept https://maps.app.goo.gl/…, https://goo.gl/maps/…,
        # https://www.google.com/maps/…, or any URL containing 'google'+'map'.
        # We're not validating the address — just guarding obvious garbage.
        if not v.startswith(("http://", "https://")):
            raise ValueError("google_maps_url must be a full URL starting with http(s)://")
        lower = v.lower()
        if not (("google" in lower and "map" in lower) or "goo.gl/maps" in lower or "maps.app.goo.gl" in lower):
            raise ValueError("google_maps_url must be a Google Maps link")
        return v

    @field_validator("owner_email")
    @classmethod
    def _v_owner_email(cls, v: str) -> str:
        return _validate_email(v)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    business_name: str
    slug: str
    is_admin: bool
    status: str
    business_type: Optional[str]
    timezone: str
    plan: Optional[str]
    # Integration setup status
    twilio_configured: bool


def _user_to_dict(t: Tenant) -> dict:
    return {
        "id": str(t.id),
        "email": t.owner_email,
        "name": t.owner_name,
        "business_name": t.business_name,
        "slug": t.slug,
        "is_admin": bool(t.is_admin),
        "status": t.status.value if t.status else "PENDING",
        "business_type": t.business_type.value if t.business_type else None,
        "timezone": t.timezone or DEFAULT_TIMEZONE,
        "plan": t.plan.value if t.plan else None,
        "twilio_configured": bool(t.twilio_account_sid and t.twilio_auth_token),
        # Feature flags — effective state (global AND per-tenant)
        "twilio_enabled": settings.FEATURE_TWILIO_ENABLED and (t.feature_twilio_enabled if t.feature_twilio_enabled is not None else True),
    }


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Authenticate with email + password, return JWT."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.owner_email == req.email.lower())
        )
        user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        logger.info("[Auth] Failed login attempt — no user/no password: %s", req.email)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not auth_service.verify_password(req.password, user.password_hash):
        logger.info("[Auth] Failed login attempt — wrong password: %s", req.email)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # Block deactivated/suspended (admins always allowed)
    if not user.is_admin and user.status in (TenantStatus.SUSPENDED, TenantStatus.DEACTIVATED):
        raise HTTPException(
            status_code=403,
            detail=f"Account is {user.status.value.lower()}. Contact support.",
        )

    # PENDING tenants can log in to see their pending status, but with limited access
    # The frontend will show them a "waiting for approval" screen
    token = auth_service.create_access_token(
        tenant_id=user.id, email=user.owner_email, is_admin=bool(user.is_admin)
    )

    # Update last_login_at
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == user.id))
        u = result.scalar_one_or_none()
        if u:
            u.last_login_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info("[Auth] Login success: %s (admin=%s)", user.owner_email, user.is_admin)
    return TokenResponse(access_token=token, user=_user_to_dict(user))


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest):
    """
    Register a new tenant in PENDING status, with a password.
    Returns a JWT — the user can log in immediately to track approval status.

    Slug is auto-generated from business_name; duplicate slugs are resolved
    by appending -1, -2, … in create_tenant.
    """
    email = req.owner_email.lower()
    logger.info("[Auth] Registration: business=%s email=%s", req.business_name, email)

    # Check email collision
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.owner_email == email)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Create tenant in PENDING status (slug dedup handled inside create_tenant)
    payload = req.model_dump(exclude={"password"})
    payload["owner_email"] = email
    tenant = await tenant_service.create_tenant(payload)

    # Set password and persist
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tenant.id))
        t = result.scalar_one_or_none()
        if t:
            t.password_hash = auth_service.hash_password(req.password)
            await session.commit()
            await session.refresh(t)
            tenant = t

    token = auth_service.create_access_token(
        tenant_id=tenant.id, email=tenant.owner_email, is_admin=False
    )
    logger.info("[Auth] Registered tenant %s (PENDING)", tenant.slug)
    return TokenResponse(access_token=token, user=_user_to_dict(tenant))


@router.get("/me", response_model=UserResponse)
async def me(current_user: Tenant = Depends(auth_service.get_current_user)):
    """Return the currently authenticated user's profile."""
    return UserResponse(**_user_to_dict(current_user))


class ProfileUpdateRequest(BaseModel):
    owner_name: Optional[str] = Field(None, min_length=2, max_length=255)
    business_name: Optional[str] = Field(None, min_length=2, max_length=255)


@router.patch("/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Update own profile. owner_name and business_name are editable — email is immutable.
    All changes are logged for admin audit trail.
    """
    from backend.models.profile_change_log import ProfileChangeLog

    updated = []
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == current_user.id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        if req.owner_name is not None and req.owner_name.strip() != user.owner_name:
            old_name = user.owner_name
            user.owner_name = req.owner_name.strip()
            updated.append("owner_name")
            session.add(ProfileChangeLog(
                tenant_id=user.id,
                field_name="owner_name",
                old_value=old_name,
                new_value=user.owner_name,
                changed_by=user.owner_email,
            ))

        if req.business_name is not None and req.business_name.strip() != user.business_name:
            old_biz = user.business_name
            user.business_name = req.business_name.strip()
            updated.append("business_name")
            session.add(ProfileChangeLog(
                tenant_id=user.id,
                field_name="business_name",
                old_value=old_biz,
                new_value=user.business_name,
                changed_by=user.owner_email,
            ))

        if not updated:
            raise HTTPException(status_code=400, detail="Nothing to update.")

        await session.commit()
        await session.refresh(user)

    logger.info("[Auth] Profile updated for %s: %s", current_user.owner_email, updated)
    return {"status": "profile_updated", "changes": updated, "user": _user_to_dict(user)}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Change the current user's password."""
    from backend.models.profile_change_log import ProfileChangeLog

    if not current_user.password_hash or not auth_service.verify_password(
        req.current_password, current_user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == current_user.id))
        u = result.scalar_one_or_none()
        if u:
            u.password_hash = auth_service.hash_password(req.new_password)
            # Log password change (no values stored for security)
            session.add(ProfileChangeLog(
                tenant_id=u.id,
                field_name="password",
                old_value=None,
                new_value=None,
                changed_by=u.owner_email,
            ))
            await session.commit()

    logger.info("[Auth] Password changed for %s", current_user.owner_email)
    return {"status": "password_updated"}


@router.get("/profile-changes")
async def get_profile_changes(
    tenant_id: Optional[str] = None,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Get profile change history.
    - Tenants see their own change history.
    - Admins can see any tenant's changes (pass tenant_id), or all changes.
    """
    from backend.models.profile_change_log import ProfileChangeLog
    from sqlalchemy import desc

    async with async_session() as session:
        filters = []
        if current_user.is_admin and tenant_id:
            from uuid import UUID
            filters.append(ProfileChangeLog.tenant_id == UUID(tenant_id))
        elif not current_user.is_admin:
            filters.append(ProfileChangeLog.tenant_id == current_user.id)
        # Admin with no tenant_id filter → see all changes

        query = select(ProfileChangeLog).order_by(desc(ProfileChangeLog.created_at)).limit(50)
        if filters:
            query = query.where(*filters)

        result = await session.execute(query)
        logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "tenant_id": str(log.tenant_id),
            "field_name": log.field_name,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "changed_by": log.changed_by,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
