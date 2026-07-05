"""
Native scheduling engine — computes availability and creates bookings
using the tenant's business_hours, appointment_types, and existing
Appointment records in Postgres. No external calendar dependency.

This is the platform-managed alternative: tenants just set their hours
and appointment types in Agent Config, and the system handles the rest.

If a tenant has connected Google Calendar, the caller (calendar_service)
should prefer Google Calendar. This module is the fallback for tenants without it.

Usage:
    from backend.services.native_scheduling import (
        get_native_slots,
        create_native_booking,
        cancel_native_booking,
        reschedule_native_booking,
    )
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.defaults import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    DEFAULT_BUSINESS_HOURS,
    DEFAULT_SLOT_INTERVAL_MINUTES,
    DEFAULT_TIMEZONE,
    slugify_appointment_type,
)
from backend.models.appointment import Appointment, AppointmentStatus, BookedVia
from backend.models.caller import Caller

logger = logging.getLogger(__name__)

# ── Day-of-week mapping (Python weekday → business_hours key) ────────────────

_DOW_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Slot interval from centralized defaults
_SLOT_INTERVAL = DEFAULT_SLOT_INTERVAL_MINUTES


# ── Availability ─────────────────────────────────────────────────────────────


async def get_native_slots(
    date_str: str,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    tenant_id: uuid.UUID | None = None,
    business_hours: dict[str, Any] | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
    slot_capacity: int = 1,
    exclude_booking_uid: str | None = None,
    appointment_type: str = "",
) -> list[str]:
    """
    Compute available time slots on `date_str` for an appointment of
    `duration_minutes` length.

    Supports multiple bookings per slot: instead of treating any overlap as a
    conflict, we count how many existing confirmed appointments overlap each
    candidate slot and only mark it unavailable when the count reaches
    ``slot_capacity``.

    Concurrency is **appointment-type scoped**: only existing appointments of
    the same type count toward the ``slot_capacity`` limit. A "cleaning" at
    3:45 PM does not affect whether a "consultation" can start at 4:00 PM —
    they are independent resource pools.

    All arithmetic is done in UTC so that overlap detection against DB
    appointments (stored in UTC) is correct.  The returned ISO strings
    include the tenant's timezone offset so downstream consumers (the LLM,
    ``create_native_booking``) can interpret them unambiguously.

    Algorithm:
      1. Look up business hours for that day of week
      2. Generate a grid of start times (every _SLOT_INTERVAL minutes) in
         the tenant's local timezone, then convert to UTC
      3. Fetch existing CONFIRMED appointments *of the same type* for that
         UTC range + tenant
      4. Count overlaps per slot — block only when count ≥ slot_capacity
      5. Return remaining slots as timezone-aware ISO datetime strings in
         the tenant's local timezone (e.g. "2026-05-06T09:00:00-05:00")

    Args:
        date_str: YYYY-MM-DD
        duration_minutes: how long the appointment is
        tenant_id: scope to this tenant's appointments
        business_hours: tenant's business_hours JSON (from Agent Config)
        tz_name: tenant's timezone string
        slot_capacity: how many overlapping bookings are allowed per slot
            before it's considered full. Default 1 (classic single-booking).
        exclude_booking_uid: if provided, excludes this booking from the
            overlap check. Used during rescheduling so the caller's own
            appointment doesn't block the new time they want to move to.
        appointment_type: when set, only count existing appointments of the
            same type toward the slot capacity limit (case-insensitive).
            Different types are treated as independent resource pools.

    Returns:
        List of ISO datetime strings with timezone offset
        (e.g. ["2026-05-06T09:00:00-05:00", ...])
    """
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("[NativeSched] Invalid date: %s", date_str)
        return []

    # Resolve the tenant's timezone
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo(DEFAULT_TIMEZONE)

    dow_key = _DOW_KEYS[target_date.weekday()]

    # ── Get business hours for this day ──────────────────────────────────
    if not business_hours:
        business_hours = DEFAULT_BUSINESS_HOURS

    day_hours = business_hours.get(dow_key)
    if not day_hours:
        logger.info("[NativeSched] %s (%s) is closed", date_str, dow_key)
        return []

    try:
        open_h, open_m = map(int, day_hours["open"].split(":"))
        close_h, close_m = map(int, day_hours["close"].split(":"))
    except (KeyError, ValueError) as exc:
        logger.warning("[NativeSched] Bad business hours for %s: %s", dow_key, exc)
        return []

    open_time = dtime(open_h, open_m)
    close_time = dtime(close_h, close_m)

    # ── Generate slot grid in local timezone, convert to UTC ─────────────
    # Always use a fixed 15-minute grid (:00, :15, :30, :45) regardless of
    # appointment duration so callers can start at any quarter-hour.
    effective_interval = _SLOT_INTERVAL

    candidate_slots_local: list[datetime] = []
    candidate_slots_utc: list[datetime] = []

    current_local = datetime.combine(target_date, open_time, tzinfo=local_tz)
    latest_start_local = datetime.combine(target_date, close_time, tzinfo=local_tz) - timedelta(minutes=duration_minutes)

    while current_local <= latest_start_local:
        candidate_slots_local.append(current_local)
        candidate_slots_utc.append(current_local.astimezone(timezone.utc))
        current_local += timedelta(minutes=effective_interval)

    if not candidate_slots_utc:
        return []

    # ── Fetch existing appointments for this day + tenant ────────────────
    # Query the UTC range that covers the local business day
    day_start_utc = candidate_slots_utc[0] - timedelta(hours=1)  # small buffer
    day_end_utc = candidate_slots_utc[-1] + timedelta(minutes=duration_minutes, hours=1)

    existing_appointments: list[Appointment] = []
    async with async_session() as session:
        query = (
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Appointment.scheduled_at >= day_start_utc,
                    Appointment.scheduled_at < day_end_utc,
                    Appointment.status == AppointmentStatus.CONFIRMED,
                )
            )
        )
        if tenant_id:
            query = query.where(Caller.tenant_id == tenant_id)
        # Only count appointments of the SAME type toward the slot capacity
        # limit — different types are independent resource pools.
        # Stored values are normalised at booking time via
        # slugify_appointment_type, so a simple lower() match works.
        if appointment_type:
            query = query.where(
                func.lower(Appointment.appointment_type) == slugify_appointment_type(appointment_type)
            )
        # Exclude the appointment being rescheduled so the caller's own
        # booking doesn't block the new slot they want to move to.
        if exclude_booking_uid:
            query = query.where(Appointment.cal_booking_uid != exclude_booking_uid)

        result = await session.execute(query)
        existing_appointments = list(result.scalars().all())

    logger.info("[NativeSched] %s: %d candidates, %d existing appts (type=%s, slot_capacity=%d, exclude=%s)",
                date_str, len(candidate_slots_utc), len(existing_appointments), appointment_type or "all", slot_capacity,
                exclude_booking_uid or "none")

    # ── Count overlaps per slot (all in UTC) ─────────────────────────────
    # Build list of (start_utc, end_utc) for existing appointments.
    # Each appointment's stored duration_minutes is the source of truth
    # (written from tenant config at booking time). The fallback to the
    # caller-supplied duration_minutes only fires for legacy rows with null.
    booked_ranges: list[tuple[datetime, datetime]] = []
    for appt in existing_appointments:
        appt_start = appt.scheduled_at
        # Ensure UTC-aware for comparison
        if appt_start.tzinfo is None:
            appt_start = appt_start.replace(tzinfo=timezone.utc)
        appt_end = appt_start + timedelta(minutes=appt.duration_minutes or duration_minutes)
        booked_ranges.append((appt_start, appt_end))

    available: list[str] = []
    now_local = datetime.now(local_tz)
    for slot_utc, slot_local in zip(candidate_slots_utc, candidate_slots_local):
        # Skip slots that start in the past or less than 5 minutes from now
        if slot_local < now_local + timedelta(minutes=5):
            continue
        slot_end_utc = slot_utc + timedelta(minutes=duration_minutes)
        overlap_count = 0
        for booked_start, booked_end in booked_ranges:
            # Two ranges overlap if one starts before the other ends AND vice versa
            if slot_utc < booked_end and slot_end_utc > booked_start:
                overlap_count += 1
                # Early exit: no need to keep counting past the limit
                if overlap_count >= slot_capacity:
                    break
        if overlap_count < slot_capacity:
            # Return in tenant's local timezone with offset for unambiguous parsing
            available.append(slot_local.isoformat())

    logger.info("[NativeSched] %s: %d available slots (after filtering, slot_capacity=%d)",
                date_str, len(available), slot_capacity)
    return available


# ── Provider-aware availability ─────────────────────────────────────────────


async def get_provider_aware_slots(
    date_str: str,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    tenant_id: uuid.UUID | None = None,
    business_hours: dict[str, Any] | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
    slot_capacity: int = 1,
    provider_id: uuid.UUID | None = None,
    holidays: list[dict[str, Any]] | None = None,
    exclude_booking_uid: str | None = None,
    appointment_type: str = "",
    specialty: str | None = None,
) -> dict[str, Any]:
    """
    Get available slots with provider-level slot capacity tracking.

    Unlike get_native_slots (which checks global slot capacity), this function:
    - Considers each provider's slot_capacity limit separately
    - Returns which providers are available for each slot
    - Supports filtering to a specific provider

    Concurrency is **appointment-type scoped**: only existing appointments of
    the same type count toward each provider's ``slot_capacity`` limit.
    Different appointment types are independent resource pools — a "cleaning"
    does not consume capacity for "consultation" slots.

    Returns:
        {
            "slots": [
                {
                    "time": "2026-05-20T10:00:00-05:00",
                    "available_providers": [
                        {"id": "...", "name": "Dr. Smith", "slots_remaining": 1}
                    ]
                },
                ...
            ],
            "provider_filter": provider_id or None,
        }
    """
    from backend.models.provider import Trainer

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("[NativeSched] Invalid date: %s", date_str)
        return {"slots": [], "provider_filter": None}

    # ── Holiday short-circuit ────────────────────────────────────────────
    # If the requested date is on the tenant's holidays list, the office
    # is closed — no slots regardless of business_hours. Surface the
    # holiday name so callers can tell the user why.
    holiday_match: dict[str, Any] | None = None
    if holidays:
        for h in holidays:
            if isinstance(h, dict) and h.get("date") == date_str:
                holiday_match = {
                    "date": h["date"],
                    "name": (h.get("name") or "Holiday"),
                }
                break
    if holiday_match:
        logger.info(
            "[NativeSched] %s is a holiday (%s) for tenant %s — no slots",
            date_str, holiday_match["name"], tenant_id,
        )
        return {
            "slots": [],
            "provider_filter": str(provider_id) if provider_id else None,
            "holiday": holiday_match,
        }

    # Resolve timezone
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo(DEFAULT_TIMEZONE)

    dow_key = _DOW_KEYS[target_date.weekday()]
    is_demo_class = slugify_appointment_type(appointment_type) == "demo_class"

    # For demo_class, providers' fixed windows override business hours entirely.
    # For all other types, enforce the business-hours grid as usual.
    candidate_slots_local: list[datetime] = []
    candidate_slots_utc: list[datetime] = []

    if not is_demo_class:
        # Default business hours
        if not business_hours:
            business_hours = DEFAULT_BUSINESS_HOURS

        day_hours = business_hours.get(dow_key)
        if not day_hours:
            logger.info("[NativeSched] %s (%s) is closed", date_str, dow_key)
            return {"slots": [], "provider_filter": str(provider_id) if provider_id else None}

        try:
            open_h, open_m = map(int, day_hours["open"].split(":"))
            close_h, close_m = map(int, day_hours["close"].split(":"))
        except (KeyError, ValueError) as exc:
            logger.warning("[NativeSched] Bad business hours for %s: %s", dow_key, exc)
            return {"slots": [], "provider_filter": str(provider_id) if provider_id else None}

        open_time = dtime(open_h, open_m)
        close_time = dtime(close_h, close_m)

        # Generate candidate slots — always use fixed 15-minute grid
        effective_interval = _SLOT_INTERVAL

        current_local = datetime.combine(target_date, open_time, tzinfo=local_tz)
        latest_start_local = datetime.combine(target_date, close_time, tzinfo=local_tz) - timedelta(minutes=duration_minutes)

        while current_local <= latest_start_local:
            candidate_slots_local.append(current_local)
            candidate_slots_utc.append(current_local.astimezone(timezone.utc))
            current_local += timedelta(minutes=effective_interval)

        if not candidate_slots_utc:
            return {"slots": [], "provider_filter": str(provider_id) if provider_id else None}

    # Get providers (filter by subject if specified — for demo class bookings)
    async with async_session() as session:
        provider_query = select(Trainer).where(
            and_(
                Trainer.tenant_id == tenant_id,
                Trainer.is_active == True,
            )
        )
        if provider_id:
            provider_query = provider_query.where(Trainer.id == provider_id)

        provider_result = await session.execute(provider_query)
        providers_raw = list(provider_result.scalars().all())

    # Filter by subject in Python (case-insensitive fuzzy match).
    # Uses a canonical alias map so common variants resolve reliably:
    #   "Maths" → "math", "Mathematics" → "math"  (prevents "maths" in "mathematics" = False)
    #   "Chem" → "chem", "Chemistry" → "chem", etc.
    # Falls back to bidirectional substring for anything not in the map.
    _SUBJECT_ALIASES: dict[str, str] = {
        "math": "math",
        "maths": "math",
        "mathematics": "math",
        "phy": "physics",
        "phys": "physics",
        "physics": "physics",
        "chem": "chemistry",
        "chemistry": "chemistry",
        "bio": "biology",
        "biology": "biology",
        "eng": "english",
        "english": "english",
        "cs": "computer_science",
        "computer science": "computer_science",
        "computer_science": "computer_science",
    }

    def _subjects_match(a: str, b: str) -> bool:
        al, bl = a.strip().lower(), b.strip().lower()
        if al == bl:
            return True
        canon_a = _SUBJECT_ALIASES.get(al, al)
        canon_b = _SUBJECT_ALIASES.get(bl, bl)
        if canon_a == canon_b:
            return True
        # fallback: bidirectional substring (handles unlisted aliases)
        return al in bl or bl in al

    if specialty:
        providers = [
            p for p in providers_raw
            if p.specialty and _subjects_match(specialty, p.specialty)
        ]
        if not providers:
            logger.info(
                "[NativeSched] No providers found for specialty=%r in tenant=%s",
                specialty, tenant_id,
            )
            return {
                "slots": [],
                "provider_filter": str(provider_id) if provider_id else None,
                "specialty_filter": specialty,
            }
    else:
        providers = providers_raw

    # For demo_class: only providers WITH trial_session_slots configured are eligible.
    # Providers without trial_session_slots are excluded entirely — no grid fallback.
    if is_demo_class:
        providers = [p for p in providers if p.trial_session_slots]
        if not providers:
            logger.info(
                "[NativeSched] demo_class: no providers with trial_session_slots in tenant=%s",
                tenant_id,
            )
            return {
                "slots": [],
                "provider_filter": str(provider_id) if provider_id else None,
            }

    if not providers and not is_demo_class:
        # No providers configured — fall back to global slot availability
        logger.info("[NativeSched] No providers found, using global availability (slot_capacity=%d)", slot_capacity)
        simple_slots = await get_native_slots(
            date_str, duration_minutes, tenant_id, business_hours, tz_name,
            slot_capacity=slot_capacity, exclude_booking_uid=exclude_booking_uid,
            appointment_type=appointment_type,
        )
        return {
            "slots": [{"time": s, "available_providers": []} for s in simple_slots],
            "provider_filter": None,
        }

    # ── Demo class: fixed-window path ────────────────────────────────────────
    if is_demo_class:
        return await _get_demo_class_slots(
            target_date=target_date,
            local_tz=local_tz,
            providers=providers,
            tenant_id=tenant_id,
            appointment_type=appointment_type,
            slot_capacity=slot_capacity,
            exclude_booking_uid=exclude_booking_uid,
            provider_id=provider_id,
        )

    # Get existing appointments for this day
    day_start_utc = candidate_slots_utc[0] - timedelta(hours=1)
    day_end_utc = candidate_slots_utc[-1] + timedelta(minutes=duration_minutes, hours=1)

    async with async_session() as session:
        appt_query = (
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == tenant_id,
                    Appointment.scheduled_at >= day_start_utc,
                    Appointment.scheduled_at < day_end_utc,
                    Appointment.status == AppointmentStatus.CONFIRMED,
                )
            )
        )
        # Only count appointments of the SAME type toward the slot capacity
        # limit — different types are independent resource pools.
        if appointment_type:
            appt_query = appt_query.where(
                func.lower(Appointment.appointment_type) == slugify_appointment_type(appointment_type)
            )
        # Exclude the appointment being rescheduled so the caller's own
        # booking doesn't block the new slot they want to move to.
        if exclude_booking_uid:
            appt_query = appt_query.where(Appointment.cal_booking_uid != exclude_booking_uid)

        appt_result = await session.execute(appt_query)
        existing_appointments = list(appt_result.scalars().all())

    # Build lookup: provider_id → list of (start_utc, end_utc)
    provider_bookings: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {p.id: [] for p in providers}

    for appt in existing_appointments:
        if appt.provider_id and appt.provider_id in provider_bookings:
            appt_start = appt.scheduled_at
            if appt_start.tzinfo is None:
                appt_start = appt_start.replace(tzinfo=timezone.utc)
            appt_end = appt_start + timedelta(minutes=appt.duration_minutes or duration_minutes)
            provider_bookings[appt.provider_id].append((appt_start, appt_end))

    # For each slot, check each provider's availability
    now_local = datetime.now(local_tz)
    result_slots = []

    for slot_utc, slot_local in zip(candidate_slots_utc, candidate_slots_local):
        # Skip slots that start in the past or less than 5 minutes from now
        if slot_local < now_local + timedelta(minutes=5):
            continue

        slot_end_utc = slot_utc + timedelta(minutes=duration_minutes)
        available_providers = []

        for provider in providers:
            bookings = provider_bookings.get(provider.id, [])
            overlap_count = 0
            for booked_start, booked_end in bookings:
                if slot_utc < booked_end and slot_end_utc > booked_start:
                    overlap_count += 1

            # Use the stricter of provider's own limit and the appointment
            # type's global limit. This ensures a provider can't exceed the
            # type-level slot capacity even if their personal limit is higher.
            provider_limit = provider.slot_capacity or 1
            max_conc = min(provider_limit, slot_capacity) if slot_capacity > 0 else provider_limit
            if overlap_count < max_conc:
                available_providers.append({
                    "id": str(provider.id),
                    "name": provider.name,
                    "title": provider.title,
                    "specialty": provider.specialty,
                    "slots_remaining": max_conc - overlap_count,
                })

        if available_providers:
            result_slots.append({
                "time": slot_local.isoformat(),
                "available_providers": available_providers,
            })

    logger.info("[NativeSched] %s: %d slots with provider availability (providers=%d, appts=%d)",
                date_str, len(result_slots), len(providers), len(existing_appointments))

    return {
        "slots": result_slots,
        "provider_filter": str(provider_id) if provider_id else None,
    }


# ── Demo class helpers ───────────────────────────────────────────────────────


async def _get_demo_class_slots(
    target_date,
    local_tz,
    providers,
    tenant_id,
    appointment_type,
    slot_capacity,
    exclude_booking_uid,
    provider_id,
) -> dict[str, Any]:
    """
    Generate available demo class slots from each provider's trial_session_slots windows.

    Unlike the 15-min grid, demo classes have fixed start/end times configured per
    provider. Duration = end - start for each window.  Different providers can have
    different windows; slots are merged by (time, duration) with their available
    providers aggregated.
    """
    now_local = datetime.now(local_tz)
    dow_key = _DOW_KEYS[target_date.weekday()]

    # Gather all window boundary times to build a DB query range
    all_starts_local: list[datetime] = []
    all_ends_local: list[datetime] = []
    for p in providers:
        for window in ((p.trial_session_slots or {}).get(dow_key) or []):
            try:
                sh, sm = map(int, window["start"].split(":"))
                eh, em = map(int, window["end"].split(":"))
                all_starts_local.append(datetime.combine(target_date, dtime(sh, sm), tzinfo=local_tz))
                all_ends_local.append(datetime.combine(target_date, dtime(eh, em), tzinfo=local_tz))
            except (KeyError, ValueError, AttributeError, TypeError):
                pass

    if not all_starts_local:
        return {"slots": [], "provider_filter": str(provider_id) if provider_id else None}

    day_start_utc = min(all_starts_local).astimezone(timezone.utc) - timedelta(hours=1)
    day_end_utc = max(all_ends_local).astimezone(timezone.utc) + timedelta(hours=1)

    # Fetch existing confirmed appointments for this day
    async with async_session() as session:
        appt_query = (
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == tenant_id,
                    Appointment.scheduled_at >= day_start_utc,
                    Appointment.scheduled_at < day_end_utc,
                    Appointment.status == AppointmentStatus.CONFIRMED,
                )
            )
        )
        if appointment_type:
            appt_query = appt_query.where(
                func.lower(Appointment.appointment_type) == slugify_appointment_type(appointment_type)
            )
        if exclude_booking_uid:
            appt_query = appt_query.where(Appointment.cal_booking_uid != exclude_booking_uid)
        appt_result = await session.execute(appt_query)
        existing_appointments = list(appt_result.scalars().all())

    # Build provider → list[(start_utc, end_utc)] lookup
    provider_bookings: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {p.id: [] for p in providers}
    for appt in existing_appointments:
        if appt.provider_id and appt.provider_id in provider_bookings:
            appt_start = appt.scheduled_at
            if appt_start.tzinfo is None:
                appt_start = appt_start.replace(tzinfo=timezone.utc)
            appt_end = appt_start + timedelta(minutes=appt.duration_minutes or 30)
            provider_bookings[appt.provider_id].append((appt_start, appt_end))

    # Build slot map: (time_iso, duration_minutes) → slot dict
    slot_map: dict[tuple[str, int], dict] = {}

    for p in providers:
        for window in ((p.trial_session_slots or {}).get(dow_key) or []):
            try:
                w_start = window.get("start", "")
                w_end = window.get("end", "")
                sh, sm = map(int, w_start.split(":"))
                eh, em = map(int, w_end.split(":"))
            except (KeyError, ValueError, AttributeError, TypeError):
                continue

            duration = (eh * 60 + em) - (sh * 60 + sm)
            if duration <= 0:
                continue

            slot_local = datetime.combine(target_date, dtime(sh, sm), tzinfo=local_tz)
            if slot_local < now_local + timedelta(minutes=5):
                continue  # Skip past / imminent slots

            slot_utc = slot_local.astimezone(timezone.utc)
            slot_end_utc = slot_utc + timedelta(minutes=duration)

            # Concurrency check for this provider at this window
            bookings = provider_bookings.get(p.id, [])
            overlap_count = sum(
                1 for bs, be in bookings
                if slot_utc < be and slot_end_utc > bs
            )
            provider_limit = p.slot_capacity or 1
            max_conc = min(provider_limit, slot_capacity) if slot_capacity > 0 else provider_limit

            if overlap_count < max_conc:
                key = (slot_local.isoformat(), duration)
                if key not in slot_map:
                    slot_map[key] = {
                        "time": slot_local.isoformat(),
                        "duration_minutes": duration,
                        "available_providers": [],
                    }
                slot_map[key]["available_providers"].append({
                    "id": str(p.id),
                    "name": p.name,
                    "title": p.title,
                    "specialty": p.specialty,
                    "slots_remaining": max_conc - overlap_count,
                })

    result_slots = sorted(slot_map.values(), key=lambda s: s["time"])

    logger.info(
        "[NativeSched] demo_class %s: %d windows available (providers=%d, appts=%d)",
        target_date, len(result_slots), len(providers), len(existing_appointments),
    )
    return {
        "slots": result_slots,
        "provider_filter": str(provider_id) if provider_id else None,
    }


async def _resolve_demo_duration(
    provider_id: uuid.UUID,
    scheduled_at_utc: datetime,
    tz_name: str = DEFAULT_TIMEZONE,
) -> int | None:
    """
    For a demo_class booking, find which of the provider's trial_session_slots windows
    the scheduled UTC time falls into and return that window's duration (minutes).

    Returns None if the provider has no windows or none match the slot.
    """
    from backend.models.provider import Trainer as _Provider

    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo(DEFAULT_TIMEZONE)

    # Convert UTC booking time to local HH:MM for window matching
    scheduled_local = scheduled_at_utc.astimezone(local_tz)
    slot_hhmm = scheduled_local.strftime("%H:%M")

    async with async_session() as session:
        result = await session.execute(
            select(_Provider).where(_Provider.id == provider_id)
        )
        provider = result.scalar_one_or_none()

    if not provider or not provider.trial_session_slots:
        return None

    dow_key = _DOW_KEYS[scheduled_local.weekday()]
    day_windows = (provider.trial_session_slots or {}).get(dow_key) or []

    for window in day_windows:
        if not isinstance(window, dict):
            continue
        w_start = window.get("start", "")
        w_end = window.get("end", "")
        if w_start == slot_hhmm:
            try:
                sh, sm = map(int, w_start.split(":"))
                eh, em = map(int, w_end.split(":"))
                dur = (eh * 60 + em) - (sh * 60 + sm)
                if dur > 0:
                    return dur
            except (ValueError, AttributeError):
                pass

    logger.warning(
        "[NativeSched] No demo window matched %s for provider=%s (tz=%s)",
        slot_hhmm, provider_id, tz_name,
    )
    return None


# ── Booking ──────────────────────────────────────────────────────────────────


async def create_native_booking(
    tenant_id: uuid.UUID | None,
    caller_info: dict[str, str],
    appointment_type: str,
    start_time: str,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    provider_id: uuid.UUID | None = None,
    is_test: bool = False,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """
    Create a booking directly in the Appointment table.

    Returns dict with id, uid, status on success; None on failure.
    Returns a dict with status="CONFLICT" if the unique index fires (someone
    booked the same provider at the same instant — race lost).
    """
    try:
        scheduled_at = datetime.fromisoformat(start_time)
        # Ensure timezone-aware — if the string has an offset (e.g. from
        # get_native_slots), fromisoformat handles it and we convert to UTC
        # for storage. If naive, assume UTC for backwards-compat.
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        else:
            scheduled_at = scheduled_at.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        logger.error("[NativeSched] Bad start_time: %s — %s", start_time, exc)
        return None

    booking_uid = f"native-{uuid.uuid4().hex[:12]}"

    # Canonicalize the appointment type code so it always matches the slug
    # used in tenant config and SQL overlap queries.
    appointment_type = slugify_appointment_type(appointment_type)

    # Normalise phone so it matches caller lookups (E.164 format)
    from backend.services.caller_service import _normalise_phone, upsert_caller
    norm_phone = _normalise_phone(caller_info.get("phone", ""))

    # Upsert the caller record so caller recognition works on next call
    caller_rec = await upsert_caller(
        name=caller_info.get("name", ""),
        phone=norm_phone,
        dob=caller_info.get("dob", ""),
        email=caller_info.get("email", ""),
        appointment_type=appointment_type,
        tenant_id=tenant_id,
        is_test=is_test,
    )

    # ── Resolve tenant (needed for timezone, demo duration, and slot capacity) ──
    from backend.services.calendar_service import _resolve_appointment_config
    from backend.models.tenant import Tenant as _Tenant

    _tenant_obj = None
    _tz_name = DEFAULT_TIMEZONE
    if tenant_id:
        async with async_session() as _ts:
            _tr = await _ts.execute(select(_Tenant).where(_Tenant.id == tenant_id))
            _tenant_obj = _tr.scalar_one_or_none()
            if _tenant_obj and getattr(_tenant_obj, "timezone", None):
                _tz_name = _tenant_obj.timezone

    # ── Demo class duration override ───────────────────────────────────────────
    # For demo_class bookings the duration is determined by the provider's
    # configured time window (e.g. 8:00–10:00 = 120 min), not tenant config.
    if appointment_type == "demo_class" and provider_id:
        _demo_dur = await _resolve_demo_duration(provider_id, scheduled_at, _tz_name)
        if _demo_dur and _demo_dur > 0:
            duration_minutes = _demo_dur
            logger.info(
                "[NativeSched] Demo class duration resolved: %d min (provider=%s)",
                duration_minutes, provider_id,
            )

    # ── Pre-booking slot capacity check ─────────────────────────────────────────
    # Verify that the slot hasn't filled up since get_available_slots was
    # called. This closes the race window between slot-check and insert.
    _max_conc = 1
    if _tenant_obj:
        _, _max_conc = _resolve_appointment_config(appointment_type, _tenant_obj)

        # Count overlapping confirmed appointments of the same type
        booking_end = scheduled_at + timedelta(minutes=duration_minutes)
        async with async_session() as _check_session:
            _overlap_q = (
                select(Appointment)
                .join(Caller, Appointment.caller_id == Caller.id)
                .where(
                    and_(
                        Appointment.status == AppointmentStatus.CONFIRMED,
                        Appointment.scheduled_at < booking_end,
                    )
                )
            )
            _overlap_q = _overlap_q.where(Caller.tenant_id == tenant_id)
            if appointment_type:
                _overlap_q = _overlap_q.where(
                    func.lower(Appointment.appointment_type) == slugify_appointment_type(appointment_type)
                )
            _overlap_result = await _check_session.execute(_overlap_q)
            _existing = _overlap_result.scalars().all()

            _overlap_count = 0
            for _ex in _existing:
                _ex_start = _ex.scheduled_at
                if _ex_start.tzinfo is None:
                    _ex_start = _ex_start.replace(tzinfo=timezone.utc)
                _ex_end = _ex_start + timedelta(minutes=_ex.duration_minutes or duration_minutes)
                if _ex_start < booking_end and _ex_end > scheduled_at:
                    _overlap_count += 1

            if _overlap_count >= _max_conc:
                logger.warning(
                    "[NativeSched] Pre-booking check FAILED — slot at %s is full "
                    "(%d/%d slots for type=%s)",
                    start_time, _overlap_count, _max_conc, appointment_type,
                )
                return {"status": "CONFLICT", "reason": "slot_taken"}

    # Local import to avoid a hard dependency on SQLAlchemy at import time
    from sqlalchemy.exc import IntegrityError

    try:
        async with async_session() as session:
            appt = Appointment(
                caller_id=caller_rec.id if caller_rec else None,
                cal_booking_uid=booking_uid,
                client_name=caller_info.get("name", ""),
                client_phone=norm_phone,
                client_email=caller_info.get("email", ""),
                date_of_birth=caller_info.get("dob", ""),
                appointment_type=appointment_type,
                scheduled_at=scheduled_at,
                duration_minutes=duration_minutes,
                status=AppointmentStatus.CONFIRMED,
                booked_via=BookedVia.AI,
                provider_id=provider_id,
                notes=notes,
            )
            session.add(appt)
            await session.flush()

            # Record initial status in audit trail
            from backend.models.status_history import AppointmentStatusHistory
            history_entry = AppointmentStatusHistory(
                appointment_id=appt.id,
                old_status=None,
                new_status=AppointmentStatus.CONFIRMED.value,
                changed_by="ai_agent",
                note="Booked via AI",
            )
            session.add(history_entry)
            await session.commit()
            await session.refresh(appt)
    except IntegrityError as exc:
        # Unique-index violation = slot already taken by another booking
        # since we last checked availability. Surface this so the caller can
        # tell the caller "sorry, that slot was just taken — pick another."
        logger.warning(
            "[NativeSched] Booking conflict for provider=%s at %s (uid=%s): %s",
            provider_id, scheduled_at, booking_uid, str(exc.orig)[:160],
        )
        return {"status": "CONFLICT", "reason": "slot_taken"}

    result = {
        "id": str(appt.id),
        "uid": booking_uid,
        "status": "ACCEPTED",
    }
    logger.info("[NativeSched] ✓ Booking created: %s for %s @ %s (%d min, provider=%s)",
                booking_uid, caller_info.get("name"), start_time, duration_minutes, provider_id)
    return result


# ── Cancellation ─────────────────────────────────────────────────────────────


async def cancel_native_booking(
    booking_uid: str,
    reason: str = "",
) -> bool:
    """Cancel an appointment by its booking UID. Returns True on success."""
    async with async_session() as session:
        result = await session.execute(
            select(Appointment).where(Appointment.cal_booking_uid == booking_uid)
        )
        appt = result.scalar_one_or_none()
        if not appt:
            logger.warning("[NativeSched] Cancel failed — no booking with uid=%s", booking_uid)
            return False

        old_status = appt.status
        appt.status = AppointmentStatus.CANCELLED
        appt.notes = (appt.notes or "") + f"\nCancelled: {reason}" if reason else appt.notes

        # Record in audit trail
        from backend.models.status_history import AppointmentStatusHistory
        history_entry = AppointmentStatusHistory(
            appointment_id=appt.id,
            old_status=old_status.value if old_status else None,
            new_status=AppointmentStatus.CANCELLED.value,
            changed_by="ai_agent",
            note=f"Cancelled by caller via call{(': ' + reason) if reason else ''}",
        )
        session.add(history_entry)

        await session.commit()

    logger.info("[NativeSched] ✓ Cancelled booking %s (reason: %s)", booking_uid, reason or "none")
    return True


# ── Reschedule ───────────────────────────────────────────────────────────────


async def reschedule_native_booking(
    booking_uid: str,
    new_start_time: str,
    provider_id: str | None = None,
) -> dict[str, Any] | None:
    """Reschedule an appointment to a new time, optionally changing the provider.

    Validates that the new time slot is available before committing.
    Returns result dict, {"status": "CONFLICT"} if slot is full, or None on error.
    """
    try:
        new_dt = datetime.fromisoformat(new_start_time)
        # Convert to UTC for storage — handles both tz-aware and naive inputs
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=timezone.utc)
        else:
            new_dt = new_dt.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        logger.error("[NativeSched] Bad new_start_time: %s — %s", new_start_time, exc)
        return None

    async with async_session() as session:
        result = await session.execute(
            select(Appointment).where(Appointment.cal_booking_uid == booking_uid)
        )
        appt = result.scalar_one_or_none()
        if not appt:
            logger.warning("[NativeSched] Reschedule failed — no booking with uid=%s", booking_uid)
            return None

        # ── Verify the new time slot is available ────────────────────────
        # Count confirmed appointments at the new time (excluding this one)
        # to enforce slot_capacity. Resolve config from tenant.
        from backend.services.calendar_service import _resolve_appointment_config
        from backend.models.tenant import Tenant

        tenant = None
        max_conc = 1
        duration = appt.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
        _appt_tenant_id = appt.tenant_id  # @property → caller.tenant_id
        if _appt_tenant_id:
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == _appt_tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if tenant:
                duration, max_conc = _resolve_appointment_config(
                    appt.appointment_type or "", tenant
                )

        new_end = new_dt + timedelta(minutes=duration)
        # Fetch confirmed appointments that could overlap with the new time.
        # We fetch rows and check overlap in Python to avoid Postgres-specific
        # interval arithmetic on the duration_minutes column.
        overlap_query = (
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.scheduled_at < new_end,
                    Appointment.cal_booking_uid != booking_uid,  # exclude self
                )
            )
        )
        if _appt_tenant_id:
            overlap_query = overlap_query.where(Caller.tenant_id == _appt_tenant_id)
        if appt.appointment_type:
            overlap_query = overlap_query.where(
                func.lower(Appointment.appointment_type) == slugify_appointment_type(appt.appointment_type)
            )

        overlap_result = await session.execute(overlap_query)
        overlap_appointments = overlap_result.scalars().all()

        # Count actual overlaps: an existing appointment overlaps if its
        # time range (scheduled_at .. scheduled_at + duration) intersects
        # with the new range (new_dt .. new_end).
        overlap_count = 0
        for existing in overlap_appointments:
            ex_start = existing.scheduled_at
            if ex_start.tzinfo is None:
                ex_start = ex_start.replace(tzinfo=timezone.utc)
            ex_end = ex_start + timedelta(minutes=existing.duration_minutes or duration)
            if ex_start < new_end and ex_end > new_dt:
                overlap_count += 1

        if overlap_count >= max_conc:
            logger.warning(
                "[NativeSched] Reschedule blocked — new time %s is full "
                "(%d/%d slots for type=%s)",
                new_start_time, overlap_count, max_conc, appt.appointment_type,
            )
            return {"status": "CONFLICT", "reason": "slot_full"}

        old_time = appt.scheduled_at.isoformat() if appt.scheduled_at else "unknown"
        old_status = appt.status

        # Update provider if a new one was requested
        if provider_id:
            try:
                appt.provider_id = uuid.UUID(provider_id) if isinstance(provider_id, str) else provider_id
                logger.info("[NativeSched] Provider changed to %s for %s", provider_id, booking_uid)
            except (ValueError, AttributeError) as exc:
                logger.warning("[NativeSched] Invalid provider_id=%s — %s", provider_id, exc)

        # Record in audit trail: transition through RESCHEDULED status
        from backend.models.status_history import AppointmentStatusHistory

        # Step 1: old status → RESCHEDULED (marks the reschedule event)
        appt.status = AppointmentStatus.RESCHEDULED
        session.add(AppointmentStatusHistory(
            appointment_id=appt.id,
            old_status=old_status.value if old_status else None,
            new_status=AppointmentStatus.RESCHEDULED.value,
            changed_by="ai_agent",
            note=f"Rescheduled from {old_time}",
        ))

        # Step 2: RESCHEDULED → CONFIRMED at the new time
        appt.scheduled_at = new_dt
        appt.status = AppointmentStatus.CONFIRMED
        session.add(AppointmentStatusHistory(
            appointment_id=appt.id,
            old_status=AppointmentStatus.RESCHEDULED.value,
            new_status=AppointmentStatus.CONFIRMED.value,
            changed_by="ai_agent",
            note=f"Confirmed at new time {new_dt.isoformat()}",
        ))

        await session.commit()

    logger.info("[NativeSched] ✓ Rescheduled %s: %s → %s", booking_uid, old_time, new_start_time)
    return {"uid": booking_uid, "new_start": new_start_time, "status": "RESCHEDULED"}
