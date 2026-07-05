"""
Usage metering service — tracks call minutes and SMS per tenant per billing
period and enforces plan-tier limits.

Option A model: the platform owns all Vapi / Twilio credentials. Each tenant
pays a subscription that includes a quota of call minutes and SMS messages.
This service is the enforcement layer.
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.tenant import Tenant, PlanTier

logger = logging.getLogger(__name__)


# ── Plan limits ──────────────────────────────────────────────────────────────
# Keyed by PlanTier value. call_minutes and sms are per billing period (month).

PLAN_LIMITS: dict[str, dict[str, int | float]] = {
    PlanTier.STARTER.value: {
        "call_minutes": 150,
        "sms": 300,
    },
    PlanTier.PROFESSIONAL.value: {
        "call_minutes": 500,
        "sms": 1000,
    },
    PlanTier.ENTERPRISE.value: {
        "call_minutes": 99999,  # effectively unlimited
        "sms": 99999,
    },
}


def get_plan_limits(plan: str) -> dict[str, int | float]:
    """Return the quota limits for a plan tier."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[PlanTier.STARTER.value])


# ── Usage recording ─────────────────────────────────────────────────────────


async def record_call_minutes(tenant_id: uuid.UUID, minutes: float) -> None:
    """Add call minutes to the tenant's current-period usage."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            logger.warning("[Usage] record_call_minutes: tenant %s not found", tenant_id)
            return

        _maybe_reset_period(tenant)
        tenant.call_minutes_used = (tenant.call_minutes_used or 0) + minutes
        tenant.updated_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("[Usage] +%.1f min for %s (total %.1f)",
                    minutes, tenant.slug, tenant.call_minutes_used)


async def record_sms(tenant_id: uuid.UUID, count: int = 1) -> None:
    """Increment SMS count for the tenant's current period."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            logger.warning("[Usage] record_sms: tenant %s not found", tenant_id)
            return

        _maybe_reset_period(tenant)
        tenant.sms_sent = (tenant.sms_sent or 0) + count
        tenant.updated_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("[Usage] +%d SMS for %s (total %d)",
                    count, tenant.slug, tenant.sms_sent)


# ── Usage checking ──────────────────────────────────────────────────────────


async def check_call_quota(tenant_id: uuid.UUID) -> dict[str, Any]:
    """
    Check whether tenant has remaining call minutes.
    Returns {"allowed": bool, "used": float, "limit": int, "remaining": float}.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return {"allowed": False, "used": 0, "limit": 0, "remaining": 0}

        _maybe_reset_period(tenant)
        await session.commit()

        plan = tenant.plan.value if tenant.plan else PlanTier.STARTER.value
        limits = get_plan_limits(plan)
        used = tenant.call_minutes_used or 0
        limit = limits["call_minutes"]
        return {
            "allowed": used < limit,
            "used": round(used, 1),
            "limit": limit,
            "remaining": round(max(0, limit - used), 1),
        }


async def check_sms_quota(tenant_id: uuid.UUID) -> dict[str, Any]:
    """
    Check whether tenant has remaining SMS quota.
    Returns {"allowed": bool, "used": int, "limit": int, "remaining": int}.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return {"allowed": False, "used": 0, "limit": 0, "remaining": 0}

        _maybe_reset_period(tenant)
        await session.commit()

        plan = tenant.plan.value if tenant.plan else PlanTier.STARTER.value
        limits = get_plan_limits(plan)
        used = tenant.sms_sent or 0
        limit = limits["sms"]
        return {
            "allowed": used < limit,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }


async def get_usage_summary(tenant_id: uuid.UUID) -> dict[str, Any]:
    """Return a full usage summary for the dashboard."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return {}

        _maybe_reset_period(tenant)
        await session.commit()

        plan = tenant.plan.value if tenant.plan else PlanTier.STARTER.value
        limits = get_plan_limits(plan)
        call_used = tenant.call_minutes_used or 0
        sms_used = tenant.sms_sent or 0

        return {
            "plan": plan,
            "period_start": (tenant.current_period_start or datetime.now(timezone.utc)).isoformat(),
            "calls": {
                "used": round(call_used, 1),
                "limit": limits["call_minutes"],
                "remaining": round(max(0, limits["call_minutes"] - call_used), 1),
                "percent": round(min(100, (call_used / limits["call_minutes"]) * 100), 1) if limits["call_minutes"] else 0,
            },
            "sms": {
                "used": sms_used,
                "limit": limits["sms"],
                "remaining": max(0, limits["sms"] - sms_used),
                "percent": round(min(100, (sms_used / limits["sms"]) * 100), 1) if limits["sms"] else 0,
            },
        }


# ── Period management ───────────────────────────────────────────────────────


def _maybe_reset_period(tenant: Tenant) -> None:
    """
    If current_period_start is more than ~30 days ago (or unset), reset
    the counters for a new billing period. Called inline on every usage
    read/write so no external cron is required.
    """
    now = datetime.now(timezone.utc)
    period_start = tenant.current_period_start

    if period_start is None or (now - period_start) > timedelta(days=30):
        tenant.current_period_start = now
        tenant.call_minutes_used = 0.0
        tenant.sms_sent = 0
        logger.info("[Usage] New billing period started for %s", tenant.slug)
