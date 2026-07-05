"""
Provider service — CRUD and query operations for practice providers.

Multi-tenant: every function accepts a tenant_id (or provider_id that implicitly
belongs to one tenant) so that provider lookups and writes are scoped correctly.

Providers are the trainers/coaches who lead classes and personal training sessions.
Each provider can:
  - Handle a subset of appointment types (or all types if list is empty)
  - Have their own Google Calendar or share the tenant's primary calendar
  - Override the tenant's default business hours
  - Be soft-deleted (is_active=False) for vacation / leave / termination

Used by the scheduling engine to match incoming appointment requests to the
right practitioner based on type, availability, and calendar.
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, and_, func

from backend.database import async_session
from backend.defaults import slugify_appointment_type
from backend.models.caller import Caller
from backend.models.provider import Trainer
from backend.models.appointment import Appointment, AppointmentStatus

logger = logging.getLogger(__name__)

# ── Allowed fields for update ──────────────────────────────────────────────────

_UPDATABLE_FIELDS = frozenset({
    "name",
    "title",
    "appointment_types",
    "calendar_id",
    "business_hours_override",
    "slot_capacity",
    "specialty",
    "trial_session_slots",
    "is_active",
})


# ── Serialisation helper ──────────────────────────────────────────────────────

def provider_to_dict(p) -> dict:
    """
    Convert a Provider ORM object to a plain dictionary.

    Args:
        p: A Provider ORM instance.

    Returns:
        Dictionary with provider fields serialised for JSON responses.
        The ``id`` field is returned as a string for API compatibility.
    """
    return {
        "id": str(p.id),
        "name": p.name,
        "title": p.title,
        "slot_capacity": p.slot_capacity or 1,
        "appointment_types": p.appointment_types or [],
        "calendar_id": p.calendar_id,
        "business_hours_override": p.business_hours_override,
        "specialty": p.specialty,
        "trial_session_slots": p.trial_session_slots,
        "is_active": p.is_active,
    }


# ── Create ─────────────────────────────────────────────────────────────────────

async def create_provider(
    tenant_id: uuid.UUID,
    name: str,
    title: str = "",
    appointment_types: list[str] = None,
    calendar_id: str = None,
    business_hours_override: dict = None,
    slot_capacity: int = 1,
    specialty: str = None,
    trial_session_slots: dict | None = None,
) -> dict:
    """
    Create a new provider for a tenant.

    Args:
        tenant_id: UUID of the owning tenant.
        name: Display name, e.g. "Dr. Sarah Patel".
        title: Professional title — "DDS", "DMD", "RDH", etc.
        appointment_types: List of appointment type keys this provider handles.
            An empty list (or None) means the provider handles all types.
        calendar_id: Optional Google Calendar ID for this provider's calendar.
            If omitted, the tenant's primary calendar is used.
        business_hours_override: Optional per-provider business hours that
            override the tenant defaults.  Same shape as ``tenant.business_hours``.
        slot_capacity: Maximum overlapping appointments this provider can handle.
            Default 1 (single-booking). Set higher for providers who can see
            multiple callers in parallel slots.

    Returns:
        Dictionary representation of the newly created provider.
    """
    async with async_session() as session:
        provider = Trainer(
            tenant_id=tenant_id,
            name=name.strip(),
            title=title.strip() if title else None,
            appointment_types=[slugify_appointment_type(t) for t in appointment_types] if appointment_types else [],
            calendar_id=calendar_id,
            business_hours_override=business_hours_override,
            slot_capacity=max(1, slot_capacity),
            specialty=specialty.strip() if specialty else None,
            trial_session_slots=trial_session_slots if trial_session_slots else None,
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)

        logger.info(
            "[Provider] Created provider %s (%s) for tenant=%s, types=%s",
            provider.name,
            provider.title or "N/A",
            tenant_id,
            provider.appointment_types,
        )
        return provider_to_dict(provider)


# ── List ───────────────────────────────────────────────────────────────────────

async def list_providers(
    tenant_id: uuid.UUID,
    active_only: bool = True,
) -> list[dict]:
    """
    List all providers belonging to a tenant.

    Args:
        tenant_id: UUID of the tenant to query.
        active_only: If True (default), only return providers with
            ``is_active=True``.  Pass False to include soft-deleted providers.

    Returns:
        List of provider dictionaries, ordered by name.
    """
    async with async_session() as session:
        filters = [Trainer.tenant_id == tenant_id]
        if active_only:
            filters.append(Trainer.is_active == True)  # noqa: E712

        result = await session.execute(
            select(Trainer)
            .where(and_(*filters))
            .order_by(Trainer.name.asc())
        )
        providers = result.scalars().all()

        logger.info(
            "[Provider] Listed %d provider(s) for tenant=%s (active_only=%s)",
            len(providers),
            tenant_id,
            active_only,
        )
        return [provider_to_dict(p) for p in providers]


# ── Get single ─────────────────────────────────────────────────────────────────

async def get_provider(provider_id: uuid.UUID) -> dict | None:
    """
    Retrieve a single provider by its ID.

    Args:
        provider_id: UUID of the provider.

    Returns:
        Provider dictionary, or None if not found.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Trainer).where(Trainer.id == provider_id)
        )
        provider = result.scalar_one_or_none()

        if not provider:
            logger.warning("[Provider] Provider not found: %s", provider_id)
            return None

        return provider_to_dict(provider)


# ── Update ─────────────────────────────────────────────────────────────────────

async def update_provider(provider_id: uuid.UUID, data: dict) -> dict | None:
    """
    Update allowed fields on an existing provider.

    Only the following fields can be modified:
    ``name``, ``title``, ``appointment_types``, ``calendar_id``,
    ``business_hours_override``, ``is_active``.

    Args:
        provider_id: UUID of the provider to update.
        data: Dictionary of field names to new values.  Keys not in the
            allowed set are silently ignored.

    Returns:
        Updated provider dictionary, or None if the provider was not found.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Trainer).where(Trainer.id == provider_id)
        )
        provider = result.scalar_one_or_none()

        if not provider:
            logger.warning("[Provider] Cannot update — provider not found: %s", provider_id)
            return None

        updated_fields = []
        for field_name, value in data.items():
            if field_name in _UPDATABLE_FIELDS:
                # Normalize appointment type slugs on write
                if field_name == "appointment_types" and isinstance(value, list):
                    value = [slugify_appointment_type(t) for t in value]
                setattr(provider, field_name, value)
                updated_fields.append(field_name)

        if not updated_fields:
            logger.info("[Provider] No updatable fields provided for provider %s", provider_id)
            return provider_to_dict(provider)

        await session.commit()
        await session.refresh(provider)

        logger.info(
            "[Provider] Updated provider %s (%s): fields=%s",
            provider.name,
            provider_id,
            updated_fields,
        )
        return provider_to_dict(provider)


# ── Soft-delete ────────────────────────────────────────────────────────────────

async def delete_provider(provider_id: uuid.UUID) -> bool:
    """
    Soft-delete a provider by setting ``is_active=False``.

    This preserves historical data and referential integrity while removing
    the provider from active scheduling.

    Args:
        provider_id: UUID of the provider to deactivate.

    Returns:
        True if the provider was found and deactivated, False otherwise.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Trainer).where(Trainer.id == provider_id)
        )
        provider = result.scalar_one_or_none()

        if not provider:
            logger.warning("[Provider] Cannot delete — provider not found: %s", provider_id)
            return False

        provider.is_active = False
        await session.commit()

        logger.info(
            "[Provider] Soft-deleted provider %s (%s) tenant=%s",
            provider.name,
            provider_id,
            provider.tenant_id,
        )
        return True


# ── Query by appointment type ──────────────────────────────────────────────────

async def get_providers_for_appointment_type(
    tenant_id: uuid.UUID,
    appointment_type: str,
) -> list[dict]:
    """
    Find active providers who can handle a specific appointment type.

    A provider matches if:
      - Their ``appointment_types`` list contains the given type, OR
      - Their ``appointment_types`` list is empty (meaning they handle all types).

    Args:
        tenant_id: UUID of the tenant.
        appointment_type: The appointment type key to match,
            e.g. "consultation", "follow_up", "emergency".

    Returns:
        List of matching provider dictionaries with id, name, and title.
    """
    async with async_session() as session:
        # Fetch all active providers for this tenant; filter in Python
        # because JSONB array-contains semantics vary across dialects and
        # the provider count per tenant is small (typically < 20).
        result = await session.execute(
            select(Trainer).where(
                and_(
                    Trainer.tenant_id == tenant_id,
                    Trainer.is_active == True,  # noqa: E712
                )
            )
        )
        all_active = result.scalars().all()

    matched = []
    slug = slugify_appointment_type(appointment_type)
    for p in all_active:
        types_list = p.appointment_types or []
        slugged_list = [slugify_appointment_type(t) for t in types_list] if types_list else []
        if not types_list or slug in slugged_list:
            matched.append({
                "id": str(p.id),
                "name": p.name,
                "title": p.title,
            })

    logger.info(
        "[Provider] Found %d provider(s) for type=%r in tenant=%s",
        len(matched),
        appointment_type,
        tenant_id,
    )
    return matched


# ── Auto-assign (load-balanced provider selection) ───────────────────────────

async def auto_assign_provider(
    tenant_id: uuid.UUID,
    appointment_type: str,
    requested_datetime: datetime,
    duration_minutes: int = 30,
) -> dict | None:
    """
    Find the best available provider for the given slot.

    Strategy:
      1. Get all active providers matching the appointment_type.
      2. For each provider, check if they have capacity at the requested time
         (respect slot_capacity and existing bookings).
      3. Pick the provider with the fewest bookings that day (load balancing).

    Returns:
        Provider dict {id, name, title} of the selected provider, or None if
        nobody is available.
    """
    async with async_session() as session:
        # Get all active providers for this tenant
        result = await session.execute(
            select(Trainer).where(
                and_(
                    Trainer.tenant_id == tenant_id,
                    Trainer.is_active == True,  # noqa: E712
                )
            )
        )
        all_active = result.scalars().all()

    # Filter to those matching the appointment type
    candidates = []
    slug = slugify_appointment_type(appointment_type)
    for p in all_active:
        types_list = p.appointment_types or []
        slugged_list = [slugify_appointment_type(t) for t in types_list] if types_list else []
        if not types_list or slug in slugged_list:
            candidates.append(p)

    if not candidates:
        logger.info("[Provider] No active providers for type=%r in tenant=%s", appointment_type, tenant_id)
        return None

    # Check existing bookings at the requested time for each candidate
    slot_start = requested_datetime
    slot_end = requested_datetime + timedelta(minutes=duration_minutes)
    day_start = requested_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    async with async_session() as session:
        # Get all active bookings for these providers on the requested day
        provider_ids = [p.id for p in candidates]
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == tenant_id,
                    Appointment.provider_id.in_(provider_ids),
                    Appointment.scheduled_at >= day_start,
                    Appointment.scheduled_at < day_end,
                    Appointment.status.in_([
                        AppointmentStatus.CONFIRMED.value,
                        AppointmentStatus.RESCHEDULED.value,
                    ]),
                )
            )
        )
        day_bookings = result.scalars().all()

    # Build per-provider booking counts and slot capacity check
    provider_day_counts: dict[uuid.UUID, int] = {p.id: 0 for p in candidates}
    provider_slot_counts: dict[uuid.UUID, int] = {p.id: 0 for p in candidates}

    for appt in day_bookings:
        if appt.provider_id in provider_day_counts:
            provider_day_counts[appt.provider_id] += 1
            # Check if this booking overlaps with the requested slot
            appt_start = appt.scheduled_at
            appt_end = appt_start + timedelta(minutes=appt.duration_minutes or 30)
            if appt_start < slot_end and appt_end > slot_start:
                provider_slot_counts[appt.provider_id] += 1

    # Filter to providers with available capacity at the requested time
    available = []
    for p in candidates:
        if provider_slot_counts[p.id] < (p.slot_capacity or 1):
            available.append(p)

    if not available:
        logger.info(
            "[Provider] No available providers at %s for type=%r (all %d at capacity)",
            requested_datetime.isoformat(), appointment_type, len(candidates),
        )
        return None

    # Load balance: pick the provider with the fewest bookings that day
    available.sort(key=lambda p: provider_day_counts[p.id])
    selected = available[0]

    logger.info(
        "[Provider] Auto-assigned %s (%s) for %s at %s (day_bookings=%d, slot=%d/%d)",
        selected.name, selected.id, appointment_type,
        requested_datetime.isoformat(),
        provider_day_counts[selected.id],
        provider_slot_counts[selected.id],
        selected.slot_capacity or 1,
    )
    return {
        "id": str(selected.id),
        "name": selected.name,
        "title": selected.title,
    }
