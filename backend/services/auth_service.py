"""
Authentication service — password hashing, JWT issuance, and user resolution.

Uses bcrypt for password hashing and PyJWT for stateless session tokens.

JWT payload:
  {
    "sub": "<tenant_uuid>",
    "email": "<owner_email>",
    "is_admin": bool,
    "exp": <unix_timestamp>,
    "iat": <unix_timestamp>,
  }
"""
from __future__ import annotations


import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from backend.database import async_session
from backend.defaults import DEFAULT_TIMEZONE
from backend.models.tenant import Tenant, TenantStatus

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# In production, load from environment. We default to a dev-only secret.
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-in-production-min-32-chars")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

bearer_scheme = HTTPBearer(auto_error=False)


# ── Password hashing ─────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Hash a plain-text password using bcrypt."""
    if not plain:
        raise ValueError("Password cannot be empty")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Compare a plain-text password against a stored bcrypt hash."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as exc:
        logger.warning("[Auth] Password verification error: %s", exc)
        return False


# ── JWT ──────────────────────────────────────────────────────────────────────


def create_access_token(tenant_id: uuid.UUID, email: str, is_admin: bool) -> str:
    """Create a signed JWT for the given user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(tenant_id),
        "email": email,
        "is_admin": is_admin,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRY_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Decode and verify a JWT. Returns payload or None on failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.info("[Auth] Token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("[Auth] Invalid token: %s", exc)
        return None


# ── FastAPI dependencies ─────────────────────────────────────────────────────


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tenant:
    """
    FastAPI dependency that resolves the JWT-authenticated user.
    Raises 401 if missing/invalid token, 403 if account isn't usable.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        tenant_uuid = uuid.UUID(payload.get("sub", ""))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Malformed token.")

    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_uuid))
        tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=401, detail="User not found.")

    # Block disabled accounts unless admin
    if not tenant.is_admin and tenant.status in (
        TenantStatus.SUSPENDED,
        TenantStatus.DEACTIVATED,
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Account is {tenant.status.value.lower()}. Contact support.",
        )

    return tenant


async def require_admin(
    user: Tenant = Depends(get_current_user),
) -> Tenant:
    """FastAPI dependency that requires the current user to be an admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[Tenant]:
    """Like get_current_user, but returns None instead of raising on missing/invalid token."""
    if not credentials or not credentials.credentials:
        return None
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    try:
        tenant_uuid = uuid.UUID(payload.get("sub", ""))
    except (ValueError, AttributeError):
        return None
    async with async_session() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_uuid))
        return result.scalar_one_or_none()


# ── Admin seeding ────────────────────────────────────────────────────────────


async def ensure_admin_exists() -> None:
    """
    Ensure at least one admin user exists. Reads ADMIN_EMAIL and ADMIN_PASSWORD
    from environment on first startup. If no admin exists and env vars are set,
    creates one. Skips admin creation if env vars are missing.
    """
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        logger.warning(
            "[Auth] ADMIN_EMAIL and/or ADMIN_PASSWORD not set in environment. "
            "Skipping admin seeding. Set both in your .env file to create an admin user."
        )
        return

    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.is_admin == True).limit(1)  # noqa: E712
        )
        existing = result.scalar_one_or_none()
        if existing:
            logger.info("[Auth] Admin user exists: %s", existing.owner_email)
            return

        # Check if a tenant with admin_email already exists
        result = await session.execute(
            select(Tenant).where(Tenant.owner_email == admin_email)
        )
        existing_by_email = result.scalar_one_or_none()
        if existing_by_email:
            existing_by_email.is_admin = True
            existing_by_email.password_hash = hash_password(admin_password)
            existing_by_email.status = TenantStatus.ACTIVE
            await session.commit()
            logger.info("[Auth] Promoted existing tenant to admin: %s", admin_email)
            return

        # Create a fresh admin tenant
        admin = Tenant(
            slug="admin",
            business_name="System Administrator",
            business_type="fitness_studio",
            owner_name="Admin",
            owner_email=admin_email,
            password_hash=hash_password(admin_password),
            is_admin=True,
            status=TenantStatus.ACTIVE,
            timezone=DEFAULT_TIMEZONE,
        )
        session.add(admin)
        await session.commit()
        logger.info("[Auth] Created admin user: %s", admin_email)
