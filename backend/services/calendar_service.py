"""
Calendar service — slot availability, booking, rescheduling, cancellation.

Architecture: the Postgres-backed native scheduler is the **source of truth**
for availability and bookings. Google Calendar is an optional sync layer —
when connected, we create/update/delete calendar events as reminders for the
caller and staff, but the DB always drives slot availability.

Routing (checked in priority order):
  1. Native scheduler — Postgres-backed, uses business_hours + appointment_types
  2. Demo mode        — fake slots/bookings for testing (when demo_mode=True)

Google Calendar sync (fire-and-forget, best-effort):
  - After booking  → create a GCal event as a reminder
  - After reschedule → update the GCal event
  - After cancel   → delete the GCal event

Multi-tenant: every function accepts a TenantContext so it uses the correct
credentials, timezone, and event type mappings for the calling tenant.
"""
from __future__ import annotations


import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from backend.config import settings
from backend.defaults import DEFAULT_APPOINTMENT_DURATION_MINUTES, DEFAULT_TIMEZONE, slugify_appointment_type
from backend.services import native_scheduling
from backend.services import google_calendar as gcal

logger = logging.getLogger(__name__)

# ── Slot availability cache ─────────────────────────────────────────────────
# During a single booking conversation the LLM often asks for the same date
# range 2-3 times (e.g. confirm → re-check → book). A short TTL avoids
# redundant Google Calendar API calls without risking stale data.
_SLOT_CACHE_TTL = 30  # seconds
_slot_cache: dict[str, tuple[float, list[str]]] = {}


def _slot_cache_key(
    date_from: str,
    date_to: str,
    tenant_slug: str,
    appointment_type_key: str,
    provider_id: str | None,
) -> str:
    return f"{tenant_slug}:{date_from}:{date_to}:{appointment_type_key}:{provider_id or ''}"


def invalidate_slot_cache(tenant_slug: str | None = None) -> None:
    """Clear cached slots — call after a booking/cancel/reschedule."""
    if tenant_slug:
        keys = [k for k in _slot_cache if k.startswith(f"{tenant_slug}:")]
        for k in keys:
            del _slot_cache[k]
    else:
        _slot_cache.clear()


def _is_demo(tenant_ctx: Any | None) -> bool:
    if tenant_ctx:
        return tenant_ctx.demo_mode
    return settings.DEMO_MODE


# ── Availability ──────────────────────────────────────────────────────────────


def _resolve_appointment_config(
    appointment_type_key: str,
    tenant_ctx: Any | None,
) -> tuple[int, int]:
    """
    Resolve ``duration_minutes`` and ``slot_capacity`` from the tenant's
    appointment_types config for the given appointment type key (e.g.
    "consultation", "follow_up").

    Resolution order (tenant config is source of truth):
      1. Exact match on appointment type code → use its duration
      2. No code match → fall back to the FIRST configured type
      3. No tenant context or no appointment_types → last-resort default (60, 1)

    The inner ``.get("duration_minutes", DEFAULT)`` calls are pure data-integrity
    safety nets — they only fire if the tenant's JSON is malformed (missing the
    ``duration_minutes`` key). In normal operation, the tenant's config
    always drives the value.
    """
    duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
    max_conc = 1
    if not tenant_ctx or not tenant_ctx.appointment_types:
        return duration, max_conc

    # Try to match by code — slugify both sides so "Follow-up", "follow_up",
    # and "FOLLOW UP" all resolve to the same canonical code.
    key = slugify_appointment_type(appointment_type_key)
    for at in tenant_ctx.appointment_types:
        if slugify_appointment_type(at.get("code", "")) == key:
            duration = at.get("duration_minutes", DEFAULT_APPOINTMENT_DURATION_MINUTES)
            max_conc = at.get("slot_capacity", 1)
            return duration, max_conc

    # No key match — fall back to the first configured type
    first = tenant_ctx.appointment_types[0]
    duration = first.get("duration_minutes", DEFAULT_APPOINTMENT_DURATION_MINUTES)
    max_conc = first.get("slot_capacity", 1)
    return duration, max_conc


async def _resolve_provider_overrides(
    provider_id: str | None,
    tenant_ctx: Any | None,
) -> tuple[str | None, dict | None]:
    """
    If a provider_id is given, load the provider and return any overrides.

    Returns:
        (calendar_id or None, business_hours_override or None)
    """
    if not provider_id or not tenant_ctx:
        return None, None
    try:
        from backend.services import provider_service
        import uuid as _uuid
        provider = await provider_service.get_provider(_uuid.UUID(provider_id))
        if provider and str(provider.get("tenant_id", "")) == str(tenant_ctx.tenant_id):
            return provider.get("calendar_id"), provider.get("business_hours_override")
    except Exception as exc:
        logger.warning("[Calendar] Provider lookup failed for %s: %s", provider_id, exc)
    return None, None


async def get_available_slots(
    date_from: str,
    date_to: str,
    tenant_ctx: Any | None = None,
    appointment_type_key: str = "",
    provider_id: str | None = None,
    *,
    event_type_id: str = "",  # deprecated, kept for call-site compat
    exclude_booking_uid: str | None = None,
) -> list[str]:
    """
    Fetch available time slots.

    Routes to Google Calendar, native scheduler, or demo mode
    depending on the tenant's configuration.

    Args:
        date_from: ISO date string (YYYY-MM-DD) — start of window.
        date_to: ISO date string (YYYY-MM-DD) — end of window.
        tenant_ctx: TenantContext for multi-tenant routing.
        appointment_type_key: Raw appointment type key (e.g. "consultation")
            used to resolve duration and slot_capacity from tenant config.
        provider_id: Optional provider UUID — uses their calendar_id and
            business hours override if set.
        exclude_booking_uid: If provided, excludes this booking from the
            overlap check. Used during rescheduling so the caller's own
            appointment doesn't block the new time they want to move to.

    Returns:
        List of available slot ISO datetime strings.
    """
    # ── Check slot cache first ─────────────────────────────────────────
    # Skip cache when excluding a booking (reschedule) — must be fresh
    slug = tenant_ctx.slug if tenant_ctx else "default"
    tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
    cache_key = _slot_cache_key(date_from, date_to, slug, appointment_type_key, provider_id)
    cached = _slot_cache.get(cache_key) if not exclude_booking_uid else None
    if cached:
        ts, slots = cached
        if (time.time() - ts) < _SLOT_CACHE_TTL:
            logger.info("[Calendar][%s] Slot cache HIT (%d slots, %.0fs old)",
                        slug, len(slots), time.time() - ts)
            return slots
        del _slot_cache[cache_key]

    # Resolve provider-specific overrides (calendar_id, business hours)
    prov_calendar_id, prov_hours = await _resolve_provider_overrides(provider_id, tenant_ctx)
    effective_hours = prov_hours or (tenant_ctx.business_hours if tenant_ctx else None)

    def _cache_and_return(result: list[str]) -> list[str]:
        """Store result in short-TTL cache before returning."""
        _slot_cache[cache_key] = (time.time(), result)
        return result

    # ── Priority 1: Native scheduling (DB is source of truth) ───────────
    if tenant_ctx:
        duration, max_conc = _resolve_appointment_config(appointment_type_key, tenant_ctx)
        logger.info("[Calendar][%s] Using native scheduler (type=%s, duration=%d, slot_capacity=%d)",
                    tenant_ctx.slug, appointment_type_key, duration, max_conc)

        # Use provider-aware slots which handles per-provider slot capacity
        provider_uuid = None
        if provider_id:
            try:
                import uuid as uuid_mod
                provider_uuid = uuid_mod.UUID(provider_id)
            except (ValueError, TypeError):
                pass

        result = await native_scheduling.get_provider_aware_slots(
            date_str=date_from,
            duration_minutes=duration,
            tenant_id=tenant_ctx.tenant_id,
            business_hours=effective_hours,
            tz_name=tz,
            slot_capacity=max_conc,
            provider_id=provider_uuid,
            exclude_booking_uid=exclude_booking_uid,
            appointment_type=appointment_type_key,
        )
        # Extract just the time strings for backwards compatibility
        slot_times = [s["time"] for s in result.get("slots", [])]
        return _cache_and_return(slot_times)

    # ── Priority 3: Demo mode fallback (no real calendar configured) ────
    if _is_demo(tenant_ctx):
        demo_slots = _demo_slots(date_from)
        logger.info("[Calendar DEMO] Returning %d fake slots for %s", len(demo_slots), date_from)
        return _cache_and_return(demo_slots)

    logger.warning("[Calendar] No calendar configured and not in demo mode — returning empty slots")
    return []


# ── Booking ───────────────────────────────────────────────────────────────────


async def book_appointment(
    caller_info: dict[str, Any],
    start_time: str,
    tenant_ctx: Any | None = None,
    appointment_type_key: str = "",
    provider_id: Any | None = None,
    *,
    event_type_id: str = "",  # deprecated, kept for call-site compat
    is_test: bool = False,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """
    Create a booking.

    Routes to Google Calendar, native scheduler, or demo mode.

    Args:
        caller_info: Dict with name, email, phone, dob.
        start_time: ISO datetime string for the slot.
        tenant_ctx: TenantContext for multi-tenant routing.
        appointment_type_key: Raw appointment type key (e.g. "consultation")
            used to resolve duration from tenant config.
        provider_id: Optional provider UUID (or string). For native bookings
            this is persisted on the Appointment row and protected by a
            unique partial index on (tenant_id, provider_id, scheduled_at).
            For Google Calendar tenants the provider name is embedded into
            the event title/description for visibility.

    Returns:
        Dict with id, uid, status on success; None on failure.
        Returns {"status": "CONFLICT"} if a native booking lost the
        unique-index race (slot was just taken by someone else).
    """
    slug = tenant_ctx.slug if tenant_ctx else "default"
    tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE

    # Invalidate slot cache — the booking changes availability
    invalidate_slot_cache(slug)

    # Normalize provider_id to UUID where possible (callers may pass a string)
    import uuid as _uuid
    provider_uuid: _uuid.UUID | None = None
    if provider_id:
        try:
            provider_uuid = (
                provider_id if isinstance(provider_id, _uuid.UUID) else _uuid.UUID(str(provider_id))
            )
        except (ValueError, TypeError):
            logger.warning("[Calendar][%s] Ignoring invalid provider_id: %r", slug, provider_id)
            provider_uuid = None

    # ── Priority 1: Native scheduling (DB is source of truth) ───────────
    if tenant_ctx:
        duration, _ = _resolve_appointment_config(appointment_type_key, tenant_ctx)
        appt_type_key = appointment_type_key or "consultation"
        logger.info(
            "[Calendar][%s] Using native booking (type=%s, duration=%d, provider=%s)",
            slug, appt_type_key, duration, provider_uuid,
        )

        result = await native_scheduling.create_native_booking(
            tenant_id=tenant_ctx.tenant_id,
            caller_info=caller_info,
            appointment_type=appt_type_key,
            start_time=start_time,
            duration_minutes=duration,
            provider_id=provider_uuid,
            is_test=is_test,
            notes=notes,
        )

        # ── Google Calendar sync (fire-and-forget, non-blocking) ────────
        if result and tenant_ctx.google_calendar_connected and tenant_ctx.google_calendar_refresh_token:
            asyncio.create_task(
                _gcal_sync_booking(
                    slug=slug,
                    refresh_token=tenant_ctx.google_calendar_refresh_token,
                    caller_info=caller_info,
                    start_time=start_time,
                    duration_minutes=duration,
                    timezone=tz,
                    provider_uuid=provider_uuid,
                )
            )

        return result

    # ── Priority 3: Demo mode fallback (no real calendar configured) ────
    if _is_demo(tenant_ctx):
        result = _demo_booking(caller_info, start_time)
        logger.info("[Calendar DEMO] Booking created: %s", result)
        return result

    logger.warning("[Calendar] No calendar configured and not in demo mode — booking failed")
    return None


# ── Reschedule ────────────────────────────────────────────────────────────────


async def reschedule_appointment(
    booking_uid: str,
    new_start_time: str,
    reason: str = "",
    tenant_ctx: Any | None = None,
    provider_id: str | None = None,
) -> dict[str, Any] | None:
    """Reschedule an existing booking, optionally changing the provider."""
    slug = tenant_ctx.slug if tenant_ctx else "default"
    tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE

    # Invalidate slot cache — reschedule changes availability
    invalidate_slot_cache(slug)

    # ── Priority 1: Native scheduling (DB is source of truth) ───────────
    if tenant_ctx:
        logger.info("[Calendar][%s] Using native reschedule", slug)
        result = await native_scheduling.reschedule_native_booking(booking_uid, new_start_time, provider_id=provider_id)

        # ── Google Calendar sync (fire-and-forget, non-blocking) ────────
        if result and tenant_ctx.google_calendar_connected and tenant_ctx.google_calendar_refresh_token:
            gcal_event_id = booking_uid if booking_uid.startswith("gcal-") else None
            if gcal_event_id:
                duration_for_gcal, _ = _resolve_appointment_config("", tenant_ctx)
                asyncio.create_task(
                    _gcal_sync_reschedule(
                        slug=slug,
                        refresh_token=tenant_ctx.google_calendar_refresh_token,
                        event_id=gcal_event_id,
                        new_start_time=new_start_time,
                        duration_minutes=duration_for_gcal,
                        timezone=tz,
                    )
                )

        return result

    # ── Demo mode fallback ──────────────────────────────────────────────
    if _is_demo(tenant_ctx):
        result = {"uid": booking_uid, "new_start": new_start_time, "status": "RESCHEDULED"}
        logger.info("[Calendar DEMO] Rescheduled booking %s → %s", booking_uid, new_start_time)
        return result

    logger.warning("[Calendar] No calendar configured and not in demo mode — reschedule failed")
    return None


# ── Cancellation ──────────────────────────────────────────────────────────────


async def cancel_appointment(
    booking_uid: str,
    reason: str = "",
    tenant_ctx: Any | None = None,
) -> bool:
    """Cancel a booking. Returns True on success."""
    slug = tenant_ctx.slug if tenant_ctx else "default"

    # Invalidate slot cache — cancellation frees up availability
    invalidate_slot_cache(slug)

    # ── Priority 1: Native scheduling (DB is source of truth) ───────────
    if tenant_ctx:
        logger.info("[Calendar][%s] Using native cancel", slug)
        success = await native_scheduling.cancel_native_booking(booking_uid, reason)

        # ── Google Calendar sync (fire-and-forget, non-blocking) ────────
        if success and tenant_ctx.google_calendar_connected and tenant_ctx.google_calendar_refresh_token:
            gcal_event_id = booking_uid if booking_uid.startswith("gcal-") else None
            if gcal_event_id:
                asyncio.create_task(
                    _gcal_sync_cancel(
                        slug=slug,
                        refresh_token=tenant_ctx.google_calendar_refresh_token,
                        event_id=gcal_event_id,
                    )
                )

        return success

    # ── Demo mode fallback ──────────────────────────────────────────────
    if _is_demo(tenant_ctx):
        logger.info("[Calendar DEMO] Cancelled booking %s — reason: %s", booking_uid, reason)
        return True

    logger.warning("[Calendar] No calendar configured and not in demo mode — cancel failed")
    return False


# ── Fire-and-forget GCal sync helpers ──────────────────────────────────────────
# These run as background tasks via asyncio.create_task() so the booking /
# reschedule / cancel response is returned immediately without waiting for
# the Google Calendar API round-trip.


async def _gcal_sync_booking(
    slug: str,
    refresh_token: str,
    caller_info: dict[str, Any],
    start_time: str,
    duration_minutes: int,
    timezone: str,
    provider_uuid: Any | None = None,
) -> None:
    """Background task: create a GCal event after a native booking."""
    try:
        provider_name: str | None = None
        if provider_uuid:
            try:
                from backend.services import provider_service
                prov = await provider_service.get_provider(provider_uuid)
                if prov:
                    provider_name = prov.get("name")
            except Exception:
                pass
        gcal_event = await gcal.book_appointment(
            refresh_token=refresh_token,
            caller_info=caller_info,
            start_time=start_time,
            duration_minutes=duration_minutes,
            timezone=timezone,
            provider_name=provider_name,
        )
        if gcal_event:
            gcal_id = gcal_event.get("id", "")
            logger.info("[Calendar][%s] ✓ GCal reminder created in background (event=%s)", slug, gcal_id)
    except Exception as exc:
        logger.warning("[Calendar][%s] GCal background sync failed (booking still saved): %s", slug, exc)


async def _gcal_sync_reschedule(
    slug: str,
    refresh_token: str,
    event_id: str,
    new_start_time: str,
    duration_minutes: int,
    timezone: str,
) -> None:
    """Background task: update a GCal event after a native reschedule."""
    try:
        await gcal.reschedule_appointment(
            refresh_token=refresh_token,
            event_id=event_id,
            new_start_time=new_start_time,
            duration_minutes=duration_minutes,
            timezone=timezone,
        )
        logger.info("[Calendar][%s] ✓ GCal event rescheduled in background", slug)
    except Exception as exc:
        logger.warning("[Calendar][%s] GCal background reschedule sync failed: %s", slug, exc)


async def _gcal_sync_cancel(
    slug: str,
    refresh_token: str,
    event_id: str,
) -> None:
    """Background task: delete a GCal event after a native cancellation."""
    try:
        await gcal.cancel_appointment(
            refresh_token=refresh_token,
            event_id=event_id,
        )
        logger.info("[Calendar][%s] ✓ GCal event cancelled in background", slug)
    except Exception as exc:
        logger.warning("[Calendar][%s] GCal background cancel sync failed: %s", slug, exc)


# ── Demo helpers (used when demo_mode=True) ───────────────────────────────────


def _demo_slots(date_from: str) -> list[str]:
    """Return fake slots for demo purposes."""
    from datetime import timedelta

    base = datetime.fromisoformat(date_from)
    slots = []
    for hour in [9, 10, 11, 13, 14, 15, 16]:
        slot_dt = base.replace(hour=hour, minute=0, second=0)
        slots.append(slot_dt.isoformat())
    return slots


def _demo_booking(caller_info: dict, start_time: str) -> dict[str, Any]:
    """Return a fake booking response for demo purposes."""
    import uuid

    return {
        "id": str(uuid.uuid4()),
        "uid": f"demo-{uuid.uuid4().hex[:12]}",
        "status": "ACCEPTED",
    }
