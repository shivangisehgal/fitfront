"""
Tenant service — resolution, caching, and CRUD for multi-tenant routing.

The primary flow:
  1. A request arrives with a tenant slug or tenant_id
  2. We resolve it → Tenant row → TenantContext
  3. TenantContext is threaded through every service call
  4. Each service uses the tenant's credentials, timezone, etc.

Includes an in-memory TTL cache to avoid a DB hit on every request.
"""
from __future__ import annotations


import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session
from backend.defaults import (
    DEFAULT_AGENT_NAME,
    DEFAULT_BUSINESS_HOURS,
    DEFAULT_GREETING,
    DEFAULT_TEST_CLIENT_NAME,
    DEFAULT_TIMEZONE,
)
from backend.models.tenant import Tenant, TenantStatus

logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────────
# Simple in-memory cache with 5-minute TTL. Tenants change rarely;
# avoids a DB round-trip on every request.

_CACHE_TTL = 300  # seconds
_cache: dict[str, tuple[float, "TenantContext"]] = {}  # key → (timestamp, context)


def _cache_get(key: str) -> "TenantContext | None":
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    if entry:
        del _cache[key]
    return None


def _cache_set(key: str, ctx: "TenantContext") -> None:
    _cache[key] = (time.time(), ctx)


def invalidate_cache(tenant_id: str | None = None) -> None:
    """Clear cache for one tenant or all tenants."""
    if tenant_id:
        keys_to_remove = [k for k, (_, ctx) in _cache.items() if str(ctx.tenant_id) == str(tenant_id)]
        for k in keys_to_remove:
            del _cache[k]
    else:
        _cache.clear()


# ── TenantContext (the lightweight object threaded through services) ──────────

@dataclass(frozen=True)
class TenantContext:
    """
    Immutable snapshot of a tenant's config. Created once per request,
    threaded through all service calls. No DB connection needed after creation.
    """
    tenant_id: uuid.UUID
    slug: str

    # Business
    business_name: str
    business_type: str
    business_phone: str
    business_address: str
    timezone: str
    demo_mode: bool

    # Agent
    agent_name: str
    greeting_message: str
    system_prompt_override: str | None
    # Voice — drives the voice config and the in-app preview/picker.
    # Default: 11labs Rachel (matches the seed/default in the Tenant model).
    voice_config: dict[str, Any]

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str

    # Google Calendar OAuth
    google_calendar_refresh_token: str
    google_calendar_email: str
    google_calendar_connected: bool

    # Escalation
    escalation_phone: str
    escalation_transfer_number: str

    # Appointment config
    appointment_types: list[dict[str, Any]]
    business_hours: dict[str, Any] | None
    # One-off holidays: each entry {"date": "YYYY-MM-DD", "name": "..."}
    holidays: list[dict[str, Any]]

    # Knowledge
    knowledge_base: dict[str, Any]
    emergency_guidance: str

    # Reminder & review settings
    reminder_settings: dict[str, Any]
    review_settings: dict[str, Any]

    # Test Agent — unified callers with 1:1 phone→name mapping
    test_callers: list[dict[str, str]]  # [{phone: str, name: str}, ...]

    # Legacy fields (deprecated — prefer test_callers)
    test_caller_phone: str
    test_caller_phones: list[str]
    test_client_name: str
    test_client_names: list[str]

    # Feature flags — effective state (global AND per-tenant)
    feature_twilio_enabled: bool = True

    @property
    def tz_abbreviation(self) -> str:
        """Human-readable timezone abbreviation for SMS templates."""
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime as dt
            tz = ZoneInfo(self.timezone)
            return dt.now(tz).strftime("%Z")  # e.g. "CST", "EST", "PST"
        except Exception:
            return "local time"


def _tenant_to_context(t: Tenant) -> TenantContext:
    """Convert a Tenant ORM object to a frozen TenantContext."""
    return TenantContext(
        tenant_id=t.id,
        slug=t.slug,
        business_name=t.business_name,
        business_type=t.business_type.value if t.business_type else "fitness_studio",
        business_phone=t.business_phone or "",
        business_address=t.business_address or "",
        timezone=t.timezone or DEFAULT_TIMEZONE,
        demo_mode=t.demo_mode if t.demo_mode is not None else True,
        agent_name=t.agent_name or DEFAULT_AGENT_NAME,
        greeting_message=t.greeting_message or DEFAULT_GREETING,
        system_prompt_override=t.system_prompt_override,
        voice_config=t.voice_config or {"provider": "11labs", "voiceId": "21m00Tcm4TlvDq8ikWAM"},
        twilio_account_sid=t.twilio_account_sid or settings.TWILIO_ACCOUNT_SID,
        twilio_auth_token=t.twilio_auth_token or settings.TWILIO_AUTH_TOKEN,
        twilio_phone_number=t.twilio_phone_number or settings.TWILIO_PHONE_NUMBER,
        google_calendar_refresh_token=t.google_calendar_refresh_token or "",
        google_calendar_email=t.google_calendar_email or "",
        google_calendar_connected=t.google_calendar_connected if t.google_calendar_connected is not None else False,
        escalation_phone=t.escalation_phone or "",
        escalation_transfer_number=t.escalation_transfer_number or "",
        appointment_types=t.appointment_types or [],
        business_hours=t.business_hours,
        holidays=t.holidays or [],
        knowledge_base=t.knowledge_base or {},
        emergency_guidance=t.emergency_guidance or "",
        reminder_settings=t.reminder_settings or {"2h_enabled": True, "confirmation_reply_enabled": True},
        review_settings=t.review_settings or {"enabled": False, "google_review_link": "", "delay_hours": 24, "appointment_types": []},
        # Unified test_callers — migrate from legacy if needed
        test_callers=_merge_test_callers(t),
        # Legacy fields (kept for backwards compat)
        test_caller_phone=t.test_caller_phone or "",
        test_caller_phones=t.test_caller_phones or [],
        test_client_name=t.test_client_name or DEFAULT_TEST_CLIENT_NAME,
        test_client_names=t.test_client_names or [DEFAULT_TEST_CLIENT_NAME],
        # Feature flags — effective = global AND per-tenant
        feature_twilio_enabled=settings.FEATURE_TWILIO_ENABLED and (t.feature_twilio_enabled if t.feature_twilio_enabled is not None else True),
    )


def _merge_test_callers(t: Tenant) -> list[dict[str, str]]:
    """
    Build the test_callers list. If the new unified field is populated, use it.
    Otherwise, merge the legacy parallel arrays (phones + names) into pairs.
    """
    if t.test_callers:
        return t.test_callers

    # Merge legacy parallel arrays
    phones = t.test_caller_phones or []
    names = t.test_client_names or []

    # If no phones but we have the single legacy field, use that
    if not phones and t.test_caller_phone:
        phones = [t.test_caller_phone]
    if not names and t.test_client_name:
        names = [t.test_client_name]

    # Pair them up — if arrays are different lengths, use index or default name
    result = []
    for i, phone in enumerate(phones):
        name = names[i] if i < len(names) else f"Test Caller {i + 1}"
        result.append({"phone": phone, "name": name})

    return result


# ── Resolution (the hot path — called on every request) ──────────────────────

async def resolve_by_slug(slug: str) -> TenantContext | None:
    """Resolve a tenant by URL slug."""
    if not slug:
        return None

    cache_key = f"slug:{slug}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        tenant = result.scalar_one_or_none()

    if not tenant:
        return None

    ctx = _tenant_to_context(tenant)
    _cache_set(cache_key, ctx)
    return ctx


async def resolve_by_id(tenant_id: uuid.UUID) -> TenantContext | None:
    """Resolve a tenant by UUID.

    Lazily assigns a test_caller_phone if the tenant doesn't have one yet
    (backfill for tenants created before this feature existed).
    """
    cache_key = f"id:{tenant_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

    if not tenant:
        return None

    # Backfill test_caller_phone for existing tenants
    if not tenant.test_caller_phone:
        test_phone = _generate_test_phone()
        async with async_session() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            t = result.scalar_one_or_none()
            if t:
                t.test_caller_phone = test_phone
                t.updated_at = datetime.now(timezone.utc)
                await session.commit()
                tenant.test_caller_phone = test_phone
                logger.info("[TenantSvc] Backfilled test_caller_phone=%s for tenant %s",
                            test_phone, tenant.slug)

    ctx = _tenant_to_context(tenant)
    _cache_set(cache_key, ctx)
    return ctx


# ── Fallback: resolve from .env (single-tenant backwards compatibility) ──────

async def resolve_default_tenant() -> TenantContext | None:
    """
    Fallback for when no assistant_id is present in the request
    (e.g. direct API testing, legacy single-tenant mode).
    Returns the first ACTIVE tenant, or builds a context from .env settings.
    """
    cache_key = "default"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    async with async_session() as session:
        result = await session.execute(
            select(Tenant)
            .where(Tenant.status == TenantStatus.ACTIVE)
            .order_by(Tenant.created_at.asc())
            .limit(1)
        )
        tenant = result.scalar_one_or_none()

    if tenant:
        ctx = _tenant_to_context(tenant)
        _cache_set(cache_key, ctx)
        return ctx

    # No tenants in DB — build from legacy .env settings
    logger.info("[TenantSvc] No tenants in DB — using legacy .env config")
    ctx = TenantContext(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        slug="default",
        business_name=settings.OFFICE_NAME,
        business_type="fitness_studio",
        business_phone="",
        business_address="",
        timezone=settings.OFFICE_TIMEZONE,
        demo_mode=settings.DEMO_MODE,
        agent_name=DEFAULT_AGENT_NAME,
        greeting_message=DEFAULT_GREETING,
        system_prompt_override=None,
        voice_config={"provider": "11labs", "voiceId": "21m00Tcm4TlvDq8ikWAM"},
        twilio_account_sid=settings.TWILIO_ACCOUNT_SID,
        twilio_auth_token=settings.TWILIO_AUTH_TOKEN,
        twilio_phone_number=settings.TWILIO_PHONE_NUMBER,
        google_calendar_refresh_token="",
        google_calendar_email="",
        google_calendar_connected=False,
        escalation_phone=settings.ESCALATION_PHONE_NUMBER,
        escalation_transfer_number=settings.ESCALATION_TRANSFER_NUMBER,
        appointment_types=[
            {"code": "new_client", "name": "New Client Visit", "duration_minutes": 60, "slot_capacity": 1},
            {"code": "follow_up", "name": "Follow-up", "duration_minutes": 30, "slot_capacity": 2},
            {"code": "emergency", "name": "Emergency / Urgent", "duration_minutes": 30, "slot_capacity": 1},
            {"code": "consultation", "name": "Consultation", "duration_minutes": 45, "slot_capacity": 1},
        ],
        business_hours=DEFAULT_BUSINESS_HOURS,
        holidays=[],
        knowledge_base={},
        emergency_guidance="",
        reminder_settings={"2h_enabled": True, "confirmation_reply_enabled": True},
        review_settings={"enabled": False, "google_review_link": "", "delay_hours": 24, "appointment_types": []},
        test_callers=[],
        test_caller_phone="",
        test_caller_phones=[],
        test_client_name=DEFAULT_TEST_CLIENT_NAME,
        test_client_names=[DEFAULT_TEST_CLIENT_NAME],
        feature_twilio_enabled=settings.FEATURE_TWILIO_ENABLED,
    )
    _cache_set(cache_key, ctx)
    return ctx


# ── CRUD (admin operations) ──────────────────────────────────────────────────

def _generate_test_phone() -> str:
    """Generate a unique dummy phone for Test Agent chat caller recognition.

    Uses the 555-XXXX range reserved for fictional use in US numbering plans.
    The result looks like +15551000 through +15559999 — clearly a test number.
    With 9000 possible values, collisions are extremely unlikely.
    """
    import random
    suffix = random.randint(1000, 9999)
    return f"+1555{suffix}"


_DEFAULT_BUSINESS_HOURS = DEFAULT_BUSINESS_HOURS


def _slugify(name: str) -> str:
    """Turn a business name into a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9\s-]", "", name.lower()).strip()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:100] or "tenant"


async def _unique_slug(session: AsyncSession, base_slug: str) -> str:
    """Return *base_slug* if it's free, otherwise append -1, -2, … until unique."""
    candidate = base_slug
    suffix = 0
    while True:
        result = await session.execute(
            select(Tenant.id).where(Tenant.slug == candidate)
        )
        if result.scalar_one_or_none() is None:
            return candidate
        suffix += 1
        candidate = f"{base_slug}-{suffix}"


async def _resolve_google_maps_short_link(url: str) -> str:
    """Follow maps.app.goo.gl / goo.gl/maps short-link redirects to get the
    expanded URL containing coordinates or place data. Returns the original
    URL unchanged if it's already expanded or if resolution fails."""
    if not url:
        return url
    lower = url.lower()
    # Only attempt resolution for short links
    if "goo.gl" not in lower:
        return url
    try:
        import httpx
        # verify=False: this is just a redirect-follow to expand a Google
        # Maps short link — not sensitive. Avoids corporate proxy SSL errors.
        async with httpx.AsyncClient(follow_redirects=True, timeout=10, verify=False) as client:
            resp = await client.get(url)
            resolved = str(resp.url)
            if resolved and len(resolved) > len(url):
                logger.info("[TenantSvc] Resolved Google Maps link → %s", resolved[:200])
                return resolved
    except Exception as exc:
        logger.warning("[TenantSvc] Could not resolve Google Maps short link: %s", exc)
    return url


async def create_tenant(data: dict[str, Any]) -> Tenant:
    """
    Create a new tenant in PENDING status (requires admin approval).
    Auto-assigns a test caller phone and default business hours.
    Slug is auto-generated from business_name if not provided, with
    collision resolution via -1, -2, … suffixes.
    """
    # Generate slug from business_name, resolving duplicates
    base_slug = data.get("slug") or _slugify(data["business_name"])
    if not base_slug or len(base_slug) < 2:
        base_slug = "tenant"

    # Resolve Google Maps short links (goo.gl) to expanded URLs
    maps_url = data.get("google_maps_url") or ""
    maps_url = await _resolve_google_maps_short_link(maps_url)

    async with async_session() as session:
        slug = await _unique_slug(session, base_slug)

        tenant = Tenant(
            slug=slug,
            business_name=data["business_name"],
            business_type=data.get("business_type", "fitness_studio"),
            business_phone=data.get("owner_phone", ""),
            business_address=data.get("business_address", ""),
            google_maps_url=maps_url,
            owner_name=data["owner_name"],
            owner_email=data["owner_email"],
            owner_phone=data.get("owner_phone"),
            escalation_phone=data.get("escalation_phone"),
            timezone=data.get("timezone", DEFAULT_TIMEZONE),
            plan=data.get("plan", "starter"),
            status=TenantStatus.PENDING,
            test_caller_phone=_generate_test_phone(),
            test_client_names=[DEFAULT_TEST_CLIENT_NAME],
            test_client_name=DEFAULT_TEST_CLIENT_NAME,
            business_hours=_DEFAULT_BUSINESS_HOURS,
        )
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        logger.info("[TenantSvc] Created tenant %s (PENDING): %s (test_phone=%s)",
                    tenant.slug, tenant.business_name, tenant.test_caller_phone)
        return tenant


async def approve_tenant(tenant_id: uuid.UUID) -> Tenant | None:
    """Admin approves a PENDING tenant → ACTIVE."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return None
        if tenant.status != TenantStatus.PENDING:
            logger.warning("[TenantSvc] Cannot approve tenant %s — status is %s", tenant.slug, tenant.status)
            return tenant
        tenant.status = TenantStatus.ACTIVE
        tenant.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(tenant)
        invalidate_cache(str(tenant.id))
        logger.info("[TenantSvc] Approved tenant %s → ACTIVE", tenant.slug)
        return tenant


async def update_tenant(tenant_id: uuid.UUID, data: dict[str, Any]) -> Tenant | None:
    """Update tenant configuration fields."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return None

        # Only update provided fields
        allowed_fields = {
            "business_name", "business_type", "business_phone", "business_address",
            "business_website", "google_maps_url", "timezone", "agent_name", "greeting_message",
            "system_prompt_override", "voice_config", "end_call_phrases",
            "twilio_account_sid", "twilio_auth_token",
            "twilio_phone_number", "escalation_phone", "escalation_transfer_number",
            "appointment_types", "business_hours", "holidays", "knowledge_base",
            "emergency_guidance", "demo_mode", "plan",
            "reminder_settings", "review_settings",
            "feature_twilio_enabled",
            "agent_active",
        }
        # Normalize holidays — strip duplicates by date, drop blanks, sort ASC
        if "holidays" in data and isinstance(data["holidays"], list):
            data["holidays"] = _normalize_holidays(data["holidays"])
        for field_name, value in data.items():
            if field_name in allowed_fields:
                setattr(tenant, field_name, value)

        tenant.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(tenant)
        invalidate_cache(str(tenant.id))
        logger.info("[TenantSvc] Updated tenant %s", tenant.slug)
        return tenant


async def list_tenants(status: TenantStatus | None = None) -> list[Tenant]:
    """List all tenants, optionally filtered by status."""
    async with async_session() as session:
        query = select(Tenant).order_by(Tenant.created_at.desc())
        if status:
            query = query.where(Tenant.status == status)
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_tenant(tenant_id: uuid.UUID) -> Tenant | None:
    """Get a single tenant by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()


# ── Holiday helpers ──────────────────────────────────────────────────────────


def _normalize_holidays(raw: list[Any]) -> list[dict[str, str]]:
    """
    Clean up a holidays list before saving:
      - Drop entries without a YYYY-MM-DD date.
      - Trim names; default empty name to 'Holiday'.
      - De-duplicate by date (last entry wins so admin edits replace old ones).
      - Sort by date ascending so the UI/agent see a stable order.
    """
    from datetime import date as _date_cls, datetime as _dt
    deduped: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        date_str = (item.get("date") or "").strip()
        if not date_str:
            continue
        try:
            _dt.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        name = (item.get("name") or "").strip() or "Holiday"
        deduped[date_str] = {"date": date_str, "name": name}
    return sorted(deduped.values(), key=lambda h: h["date"])


def find_holiday(tenant_ctx: Any | None, date_str: str) -> dict[str, str] | None:
    """
    Return the holiday entry matching the given YYYY-MM-DD date, or None.
    Tolerates malformed dates / missing tenant_ctx — callers shouldn't have
    to guard against either.
    """
    if not tenant_ctx or not date_str:
        return None
    holidays = getattr(tenant_ctx, "holidays", None) or []
    for h in holidays:
        if isinstance(h, dict) and h.get("date") == date_str:
            return {"date": h["date"], "name": h.get("name") or "Holiday"}
    return None


def upcoming_holidays(tenant_ctx: Any | None, limit: int = 8) -> list[dict[str, str]]:
    """
    Return upcoming holidays (today + future) for the tenant, capped at
    `limit`. Used by the system prompt and get_office_info so the AI agent
    can mention closures proactively.
    """
    if not tenant_ctx:
        return []
    holidays = getattr(tenant_ctx, "holidays", None) or []
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    tz_name = getattr(tenant_ctx, "timezone", None) or DEFAULT_TIMEZONE
    try:
        tz = _ZI(tz_name)
    except Exception:
        tz = _ZI(DEFAULT_TIMEZONE)
    today_local = _dt.now(tz).date()
    upcoming: list[dict[str, str]] = []
    for h in holidays:
        if not isinstance(h, dict) or not h.get("date"):
            continue
        try:
            d = _dt.strptime(h["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d >= today_local:
            upcoming.append({"date": h["date"], "name": h.get("name") or "Holiday"})
    upcoming.sort(key=lambda x: x["date"])
    return upcoming[:limit]
