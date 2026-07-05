"""
Waitlist management service — manages the caller waitlist for cancellation
fill-ins across all tenants.

When no slots are available, the AI agent offers to add the caller to the
waitlist. When a cancellation opens a slot, the system automatically notifies
the highest-priority matching caller via SMS. If they reply YES, the slot
is booked for them; otherwise, the next person on the list is offered the slot.

Multi-tenant: every operation is scoped to a tenant_id. SMS notifications
use the tenant's own Twilio credentials via TenantContext.
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, and_, update, func

from backend.database import async_session
from backend.defaults import DEFAULT_TIMEZONE, slugify_appointment_type
from backend.models.caller import Caller
from backend.models.waitlist import WaitlistEntry, WaitlistStatus
from backend.services import sms_service
from backend.services.caller_service import _phone_digits_tail, _phone_col_clean, _normalise_phone, upsert_caller

logger = logging.getLogger(__name__)


# ── Add to waitlist ─────────────────────────────────────────────────────────


async def add_to_waitlist(
    tenant_id: uuid.UUID,
    client_name: str,
    client_phone: str,
    appointment_type: str,
    preferred_date: str,
    client_email: str | None = None,
    preferred_time_start: str | None = None,
    preferred_time_end: str | None = None,
    provider_id: uuid.UUID | None = None,
    tenant_ctx: Any | None = None,
    is_test: bool = False,
) -> dict:
    """
    Add a caller to the waitlist for a specific date and appointment type.

    The entry is created with status=WAITING and expires_at set to 23:59:59 UTC
    on the preferred_date. When a matching cancellation occurs, the caller will
    be notified via SMS.

    Args:
        tenant_id: Tenant this waitlist entry belongs to.
        client_name: Full name of the caller.
        client_phone: Phone number for SMS notification.
        appointment_type: Type of appointment (e.g. "consultation", "follow_up").
        preferred_date: Desired date in YYYY-MM-DD format.
        client_email: Optional email address.
        preferred_time_start: Optional preferred window start (HH:MM).
        preferred_time_end: Optional preferred window end (HH:MM).
        provider_id: Optional preferred provider UUID.

    Returns:
        Dict with the entry id and status for the LLM tool response.
    """
    try:
        target_date = datetime.strptime(preferred_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.error("[Waitlist] Invalid preferred_date format: %r", preferred_date)
        return {
            "ok": False,
            "error": "missing_date",
            "summary_for_assistant": (
                "You need a specific date (YYYY-MM-DD) to add someone to the waitlist. "
                "Ask the caller which day they'd prefer — e.g. 'Which day would work best for you?' "
                "Then call add_to_waitlist again with that date. "
                "Do NOT tell the caller they have been added to the waitlist yet."
            ),
        }

    # ── Past-date guard ────────────────────────────────────────────────
    # Adding a caller to the waitlist for a date that has already passed
    # is never useful — we'd just create an entry that immediately expires.
    # Refuse with a clear message so the LLM tells the caller to pick a
    # future date instead of silently confirming a useless waitlist entry.
    try:
        from zoneinfo import ZoneInfo
        tz_name = (
            getattr(tenant_ctx, "timezone", None) if tenant_ctx else None
        ) or DEFAULT_TIMEZONE
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        today_local = datetime.now(tz).date()
    except Exception:
        today_local = datetime.now(timezone.utc).date()

    if target_date < today_local:
        friendly = target_date.strftime("%A, %B %d")
        logger.warning(
            "[Waitlist] Refused add for past date %s (today=%s, caller=%s)",
            preferred_date, today_local.isoformat(), client_name,
        )
        return {
            "ok": False,
            "error": "past_date",
            "preferred_date": preferred_date,
            "today": today_local.isoformat(),
            "summary_for_assistant": (
                f"That date ({friendly}) has already passed — today is "
                f"{today_local.strftime('%A, %B %d, %Y')}. Tell the caller the date "
                f"is in the past and ask which upcoming day they'd prefer. Do NOT add "
                f"them to the waitlist for a past date."
            ),
        }

    # ── Holiday guard ──────────────────────────────────────────────────
    # If the preferred date is a configured tenant holiday, the office is
    # closed — adding a waitlist entry would never resolve since no slots
    # exist. Refuse with a clear message naming the holiday so the LLM can
    # explain the closure to the caller.
    holiday_match: dict | None = None
    try:
        holidays = (
            getattr(tenant_ctx, "holidays", None) if tenant_ctx else None
        ) or []
        for h in holidays:
            if isinstance(h, dict) and h.get("date") == preferred_date:
                holiday_match = {
                    "date": h["date"],
                    "name": (h.get("name") or "Holiday"),
                }
                break
    except Exception:
        holiday_match = None

    if holiday_match:
        friendly = target_date.strftime("%A, %B %d")
        holiday_name = holiday_match["name"]
        logger.info(
            "[Waitlist] Refused add for holiday %s (%s) — caller=%s",
            preferred_date, holiday_name, client_name,
        )
        return {
            "ok": False,
            "error": "holiday",
            "preferred_date": preferred_date,
            "holiday": holiday_match,
            "summary_for_assistant": (
                f"The office is CLOSED on {friendly} for {holiday_name}. "
                f"Tell the caller we're closed that day for {holiday_name} and ask "
                f"which other day works. Do NOT add them to the waitlist for a holiday."
            ),
        }

    # Expire at end of the preferred date (23:59:59 UTC)
    expires_at = datetime.combine(
        target_date, datetime.max.time(), tzinfo=timezone.utc
    )

    # Normalise the phone before storing — prevents dirty data (dashes, spaces)
    norm_phone = _normalise_phone(client_phone)

    # Canonicalize so waitlist matching uses the same slug as booking
    appointment_type = slugify_appointment_type(appointment_type)

    # ── Duplicate guard ───────────────────────────────────────────────
    norm_tail = _phone_digits_tail(norm_phone or client_phone)
    async with async_session() as session:
        dup_result = await session.execute(
            select(WaitlistEntry)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(
                and_(
                    _phone_col_clean(WaitlistEntry.client_phone).endswith(norm_tail),
                    Caller.tenant_id == tenant_id,
                    WaitlistEntry.appointment_type == appointment_type,
                    WaitlistEntry.preferred_date == preferred_date,
                    WaitlistEntry.status == WaitlistStatus.WAITING,
                )
            )
            .limit(1)
        )
        existing = dup_result.scalars().first()
        if existing:
            friendly_date = target_date.strftime("%A, %B %d")
            friendly_type = appointment_type.replace("_", " ").title()
            logger.info(
                "[Waitlist] Duplicate detected: %s already on waitlist for %s on %s",
                client_name, appointment_type, preferred_date,
            )
            return {
                "ok": False,
                "error": "duplicate",
                "summary_for_assistant": (
                    f"{client_name} is already on the waitlist for {friendly_type} on {friendly_date}. "
                    f"Let the caller know they're already on the list — no need to add them again. "
                    f"If they want to check their position, use the check_waitlist_status tool."
                ),
            }

    # Upsert caller so we can link caller_id (is_test lives on the caller)
    _norm_phone = norm_phone or client_phone
    caller_rec = await upsert_caller(
        name=client_name,
        phone=_norm_phone,
        email=client_email or "",
        tenant_id=tenant_id,
        is_test=is_test,
    )

    entry = WaitlistEntry(
        id=uuid.uuid4(),
        caller_id=caller_rec.id if caller_rec else None,
        client_name=client_name,
        client_phone=_norm_phone,
        client_email=client_email,
        appointment_type=appointment_type,
        preferred_date=preferred_date,
        preferred_time_start=preferred_time_start,
        preferred_time_end=preferred_time_end,
        provider_id=provider_id,
        status=WaitlistStatus.WAITING,
        expires_at=expires_at,
    )

    async with async_session() as session:
        session.add(entry)
        await session.commit()

    logger.info(
        "[Waitlist] Added %s to waitlist for %s on %s (id=%s)",
        client_name,
        appointment_type,
        preferred_date,
        entry.id,
    )

    # Notify the caller by SMS that they've been added to the waitlist
    try:
        sms_service.send_waitlist_added(
            caller_name=client_name,
            phone=client_phone,
            appointment_type=appointment_type,
            preferred_date=preferred_date,
            preferred_time_start=preferred_time_start,
            preferred_time_end=preferred_time_end,
            tenant_ctx=tenant_ctx,
            is_test=is_test,
        )
    except Exception as exc:
        logger.error("[Waitlist] Failed to send 'added' SMS for entry %s: %s", entry.id, exc)

    friendly_date = target_date.strftime("%A, %B %d")
    return {
        "ok": True,
        "id": str(entry.id),
        "status": WaitlistStatus.WAITING.value,
        "preferred_date": preferred_date,
        "appointment_type": appointment_type,
        "message": f"{client_name} has been added to the waitlist for {appointment_type} on {preferred_date}.",
        "summary_for_assistant": (
            f"{client_name} has been added to the waitlist for {appointment_type} on {friendly_date}. "
            f"Tell them they're on the list and will be notified by SMS if a slot opens up. "
            f"Do NOT promise a specific time or slot."
        ),
    }


# ── Check waitlist status (caller-facing) ─────────────────────────────────


async def check_waitlist_status(
    client_phone: str,
    tenant_id: uuid.UUID,
) -> dict:
    """
    Check a caller's waitlist entries — called by LLM when caller asks
    about their waitlist status.
    """
    phone_tail = _phone_digits_tail(client_phone)

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(
                and_(
                    _phone_col_clean(WaitlistEntry.client_phone).endswith(phone_tail),
                    Caller.tenant_id == tenant_id,
                    WaitlistEntry.status.notin_([WaitlistStatus.EXPIRED, WaitlistStatus.CANCELLED]),
                )
            )
            .order_by(WaitlistEntry.created_at.desc())
            .limit(10)
        )
        entries = result.scalars().all()

        if not entries:
            return {
                "ok": True,
                "entries": [],
                "summary_for_assistant": (
                    "This caller has no active waitlist entries. "
                    "If they'd like to be added to the waitlist for a specific date, offer to do so."
                ),
            }

        # Calculate position for WAITING entries
        entry_dicts = []
        for e in entries:
            d = {
                "appointment_type": e.appointment_type.replace("_", " ").title(),
                "preferred_date": e.preferred_date,
                "preferred_time_start": e.preferred_time_start,
                "preferred_time_end": e.preferred_time_end,
                "status": e.status.value if e.status else "UNKNOWN",
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }

            if e.status == WaitlistStatus.WAITING:
                # Count how many WAITING entries are ahead
                pos_result = await session.execute(
                    select(func.count())
                    .select_from(WaitlistEntry)
                    .join(Caller, WaitlistEntry.caller_id == Caller.id)
                    .where(
                        and_(
                            Caller.tenant_id == tenant_id,
                            WaitlistEntry.appointment_type == e.appointment_type,
                            WaitlistEntry.preferred_date == e.preferred_date,
                            WaitlistEntry.status == WaitlistStatus.WAITING,
                            WaitlistEntry.created_at < e.created_at,
                        )
                    )
                )
                ahead = pos_result.scalar() or 0
                d["position"] = ahead + 1  # 1-based
            elif e.status == WaitlistStatus.NOTIFIED:
                d["status_detail"] = "A slot opened up — check your SMS to confirm"
            elif e.status == WaitlistStatus.BOOKED:
                d["status_detail"] = "Successfully booked from waitlist"

            entry_dicts.append(d)

        # Build summary
        lines = []
        for d in entry_dicts:
            line = f"{d['appointment_type']} on {d['preferred_date']}"
            if d["status"] == "WAITING":
                line += f" — position #{d.get('position', '?')} in queue"
            elif d["status"] == "NOTIFIED":
                line += " — a slot opened! Check SMS to confirm"
            elif d["status"] == "BOOKED":
                line += " — booked from waitlist"
            lines.append(line)

        return {
            "ok": True,
            "entries": entry_dicts,
            "summary_for_assistant": (
                f"Caller has {len(entry_dicts)} active waitlist entry/entries: "
                + "; ".join(lines) + ". "
                "Tell the caller their waitlist status naturally. "
                "If they've been NOTIFIED, remind them to check their SMS and reply YES to confirm."
            ),
        }


# ── Query waitlist entries ──────────────────────────────────────────────────


async def get_waitlist_entries(
    tenant_id: uuid.UUID,
    status: WaitlistStatus | None = None,
    date: str | None = None,
    include_test: bool = False,
) -> list[dict]:
    """
    Retrieve waitlist entries for a tenant, optionally filtered by status
    and/or preferred date.

    Args:
        tenant_id: Tenant to scope the query to.
        status: Optional WaitlistStatus filter (e.g. WAITING, NOTIFIED).
        date: Optional preferred_date filter in YYYY-MM-DD format.
        include_test: If False (default), exclude test/demo data.

    Returns:
        List of dicts, each containing full entry details.
    """
    filters = [Caller.tenant_id == tenant_id]

    # Real data only (default) or test data only when include_test is set
    if not include_test:
        filters.append(WaitlistEntry.is_test == False)  # noqa: E712
    else:
        filters.append(WaitlistEntry.is_test == True)  # noqa: E712

    if status is not None:
        filters.append(WaitlistEntry.status == status)

    if date is not None:
        filters.append(WaitlistEntry.preferred_date == date)

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(and_(*filters))
            .order_by(WaitlistEntry.priority.asc(), WaitlistEntry.created_at.asc())
        )
        entries = result.scalars().all()

    logger.info(
        "[Waitlist] Fetched %d entries for tenant %s (status=%s, date=%s)",
        len(entries),
        tenant_id,
        status,
        date,
    )

    return [
        {
            "id": str(e.id),
            "caller_id": str(e.caller_id) if e.caller_id else None,
            "client_name": e.client_name,
            "client_phone": e.client_phone,
            "client_email": e.client_email,
            "appointment_type": e.appointment_type,
            "preferred_date": e.preferred_date,
            "preferred_time_start": e.preferred_time_start,
            "preferred_time_end": e.preferred_time_end,
            "provider_id": str(e.provider_id) if e.provider_id else None,
            "priority": e.priority,
            "status": e.status.value if e.status else None,
            "notified_at": e.notified_at.isoformat() if e.notified_at else None,
            "booked_at": e.booked_at.isoformat() if e.booked_at else None,
            "appointment_scheduled_at": e.appointment_scheduled_at.isoformat() if e.appointment_scheduled_at else None,
            "expires_at": e.expires_at.isoformat() if e.expires_at else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "is_test": e.is_test or False,
        }
        for e in entries
    ]


# ── Check waitlist for cancellation fill-in ─────────────────────────────────


async def check_waitlist_for_opening(
    tenant_id: uuid.UUID,
    appointment_type: str,
    date: str,
    available_slot: str,
    tenant_ctx: Any | None = None,
    provider_id: uuid.UUID | None = None,
) -> bool:
    """
    Called when a cancellation creates an available slot. Finds the
    highest-priority WAITING entry matching the appointment type and date,
    then sends an SMS notification offering the slot.

    Matching logic:
      - appointment_type must match exactly.
      - preferred_date must match the given date.
      - PROVIDER MATCH: If the cancelled slot has a provider_id, only notify
        entries that requested either that same provider OR no provider at
        all. We never push a caller onto a provider they didn't request.
      - If the entry has a preferred time window (preferred_time_start /
        preferred_time_end), the available_slot time must fall within that
        window. Entries with no time preference always match.
      - Ordered by priority ASC (lower = higher priority), then created_at ASC.

    Args:
        tenant_id: Tenant that owns the cancelled slot.
        appointment_type: The type of the cancelled appointment.
        date: The date of the cancelled slot (YYYY-MM-DD).
        available_slot: The available time slot as an ISO datetime string
            or HH:MM string (used for time-window matching and SMS text).
        tenant_ctx: Optional TenantContext for multi-tenant SMS sending.
        provider_id: Optional UUID of the provider whose slot just opened.
            When set, only entries that requested this provider (or no
            provider) are eligible.

    Returns:
        True if a waitlisted caller was notified, False otherwise.
    """
    # Parse the slot time for window-matching
    try:
        if "T" in available_slot:
            slot_dt = datetime.fromisoformat(available_slot)
            slot_time_str = slot_dt.strftime("%H:%M")
        else:
            slot_time_str = available_slot
    except (ValueError, TypeError):
        logger.error("[Waitlist] Invalid available_slot format: %s", available_slot)
        return False

    # Query all WAITING entries matching type + date for this tenant.
    # Provider filtering is enforced via OR in the WHERE so the DB only
    # returns rows that requested THIS provider or no provider at all.
    from sqlalchemy import or_  # local import to avoid widening top-level surface

    # Normalize so the match is consistent with what was stored
    appointment_type = slugify_appointment_type(appointment_type)

    filters = [
        Caller.tenant_id == tenant_id,
        WaitlistEntry.status == WaitlistStatus.WAITING,
        WaitlistEntry.appointment_type == appointment_type,
        WaitlistEntry.preferred_date == date,
    ]
    if provider_id is not None:
        filters.append(
            or_(
                WaitlistEntry.provider_id == provider_id,
                WaitlistEntry.provider_id.is_(None),
            )
        )

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(and_(*filters))
            .order_by(WaitlistEntry.priority.asc(), WaitlistEntry.created_at.asc())
        )
        candidates = list(result.scalars().all())

        if not candidates:
            logger.info(
                "[Waitlist] No WAITING entries for %s on %s (tenant %s, provider=%s)",
                appointment_type,
                date,
                tenant_id,
                provider_id,
            )
            return False

        # Find the first candidate whose time window matches (or has no window)
        matched_entry: WaitlistEntry | None = None
        for entry in candidates:
            if entry.preferred_time_start and entry.preferred_time_end:
                # Check if the slot falls within the preferred window
                if entry.preferred_time_start <= slot_time_str <= entry.preferred_time_end:
                    matched_entry = entry
                    break
            elif entry.preferred_time_start:
                # Only a start time — slot must be at or after it
                if slot_time_str >= entry.preferred_time_start:
                    matched_entry = entry
                    break
            elif entry.preferred_time_end:
                # Only an end time — slot must be at or before it
                if slot_time_str <= entry.preferred_time_end:
                    matched_entry = entry
                    break
            else:
                # No time preference — any slot on this date works
                matched_entry = entry
                break

        if not matched_entry:
            logger.info(
                "[Waitlist] %d candidates found but none match time window for slot %s",
                len(candidates),
                available_slot,
            )
            return False

        # Update the matched entry: NOTIFIED + timestamp
        now = datetime.now(timezone.utc)
        matched_entry.status = WaitlistStatus.NOTIFIED
        matched_entry.notified_at = now
        await session.commit()

    # Format a human-readable date and time for the SMS
    try:
        display_date = datetime.strptime(date, "%Y-%m-%d").strftime("%B %d")
    except ValueError:
        display_date = date

    # Format display time (e.g. "2:30 PM")
    try:
        h, m = map(int, slot_time_str.split(":"))
        slot_dt_for_display = datetime(2000, 1, 1, h, m)
        display_time = slot_dt_for_display.strftime("%I:%M %p").lstrip("0")
    except (ValueError, TypeError):
        display_time = slot_time_str

    display_type = appointment_type.replace("_", " ").title()

    sms_body = (
        f"Hi {matched_entry.client_name}! Great news — a {display_type} slot "
        f"just opened up on {display_date} at {display_time}. Reply YES to "
        f"book it or it will go to the next person on our waitlist."
    )

    ok = sms_service._send_sms(
        to=matched_entry.client_phone,
        body=sms_body,
        tenant_ctx=tenant_ctx,
    )
    if ok:
        sms_service._log_outbound_sms(
            tenant_ctx,
            to_number=matched_entry.client_phone,
            body=sms_body,
            caller_phone=matched_entry.client_phone,
        )

    logger.info(
        "[Waitlist] Notified %s (%s) about %s slot on %s at %s (entry %s)",
        matched_entry.client_name,
        matched_entry.client_phone,
        appointment_type,
        date,
        display_time,
        matched_entry.id,
    )

    return True


# ── Confirm waitlist booking ────────────────────────────────────────────────


async def confirm_waitlist_booking(
    client_phone: str,
    tenant_id: uuid.UUID,
) -> dict | None:
    """
    Confirm a waitlist booking when a caller replies YES to the SMS
    notification. Finds the most recently notified entry for this phone
    number and tenant, then marks it as BOOKED.

    Args:
        client_phone: The phone number that replied YES.
        tenant_id: The tenant the caller belongs to.

    Returns:
        Dict with the booked entry details (so the caller can create the
        actual appointment), or None if no matching NOTIFIED entry was found.
    """
    async with async_session() as session:
        # Suffix match on last 10 digits to handle phone format variations
        phone_tail = _phone_digits_tail(client_phone)
        result = await session.execute(
            select(WaitlistEntry)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(
                and_(
                    _phone_col_clean(WaitlistEntry.client_phone).endswith(phone_tail),
                    Caller.tenant_id == tenant_id,
                    WaitlistEntry.status == WaitlistStatus.NOTIFIED,
                )
            )
            .order_by(WaitlistEntry.notified_at.desc())
            .limit(1)
        )
        entry = result.scalars().first()

        if not entry:
            logger.info(
                "[Waitlist] No NOTIFIED entry found for phone %s (tenant %s)",
                client_phone,
                tenant_id,
            )
            return None

        now = datetime.now(timezone.utc)
        entry.status = WaitlistStatus.BOOKED
        entry.booked_at = now
        await session.commit()

    logger.info(
        "[Waitlist] Confirmed booking for %s — %s on %s (entry %s)",
        entry.client_name,
        entry.appointment_type,
        entry.preferred_date,
        entry.id,
    )

    return {
        "id": str(entry.id),
        "client_name": entry.client_name,
        "client_phone": entry.client_phone,
        "client_email": entry.client_email,
        "appointment_type": entry.appointment_type,
        "preferred_date": entry.preferred_date,
        "preferred_time_start": entry.preferred_time_start,
        "preferred_time_end": entry.preferred_time_end,
        "provider_id": str(entry.provider_id) if entry.provider_id else None,
        "status": WaitlistStatus.BOOKED.value,
        "booked_at": now.isoformat(),
    }


# ── Cancel a waitlist entry ─────────────────────────────────────────────────


async def cancel_waitlist_entry(
    entry_id: uuid.UUID,
    tenant_ctx: Any | None = None,
) -> bool:
    """
    Cancel a specific waitlist entry by setting its status to CANCELLED.

    Args:
        entry_id: The UUID of the waitlist entry to cancel.
        tenant_ctx: Optional TenantContext for sending the caller SMS.

    Returns:
        True if the entry was found and cancelled, False otherwise.
    """
    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry).where(WaitlistEntry.id == entry_id)
        )
        entry = result.scalars().first()

        if not entry:
            logger.warning("[Waitlist] Cancel failed — entry %s not found", entry_id)
            return False

        if entry.status in (WaitlistStatus.BOOKED, WaitlistStatus.CANCELLED):
            logger.warning(
                "[Waitlist] Cancel skipped — entry %s already %s",
                entry_id,
                entry.status.value,
            )
            return False

        entry.status = WaitlistStatus.CANCELLED

        # Snapshot fields we need for the SMS before the session closes
        client_name = entry.client_name
        client_phone = entry.client_phone
        appointment_type = entry.appointment_type
        preferred_date = entry.preferred_date
        entry_is_test = entry.is_test or False

        await session.commit()

    logger.info(
        "[Waitlist] Cancelled entry %s (%s, %s on %s)",
        entry_id,
        client_name,
        appointment_type,
        preferred_date,
    )

    # Notify the caller that their waitlist entry was cancelled
    try:
        sms_service.send_waitlist_cancelled(
            caller_name=client_name,
            phone=client_phone,
            appointment_type=appointment_type,
            preferred_date=preferred_date,
            tenant_ctx=tenant_ctx,
            is_test=entry_is_test,
        )
    except Exception as exc:
        logger.error("[Waitlist] Failed to send 'cancelled' SMS for entry %s: %s", entry_id, exc)

    return True


# ── Promote a waitlist entry to a real appointment ─────────────────────────


async def find_promote_conflicts(
    entry_id: uuid.UUID,
    tenant_id: uuid.UUID,
    scheduled_at: str,
) -> dict:
    """
    Find existing appointments that would conflict with promoting a waitlist
    entry into the given slot.

    Returns appointments for the entry's preferred provider (or all providers
    if no provider preference) whose scheduled_at falls within a ±duration
    window of the proposed slot. The admin UI uses this to ask the user
    "this slot already has appointments — still promote?" before doing a
    force-bypass booking.

    Args:
        entry_id: UUID of the waitlist entry being promoted.
        tenant_id: Tenant scope (from authenticated user).
        scheduled_at: Proposed start time for the new booking (ISO string).

    Returns:
        {
          "conflicts": [<appointment dict>, ...],
          "provider_id": "<uuid or null>",
          "scheduled_at": "<iso>",
          "window_minutes": <int>,
        }
    """
    from backend.models.appointment import Appointment, AppointmentStatus
    from backend.models.provider import Trainer

    # Parse the target time
    try:
        target_dt = datetime.fromisoformat(scheduled_at)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid scheduled_at: {scheduled_at!r}")
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)
    else:
        target_dt = target_dt.astimezone(timezone.utc)

    async with async_session() as session:
        # Load the entry to pull provider_id + appointment_type
        result = await session.execute(
            select(WaitlistEntry).where(WaitlistEntry.id == entry_id)
        )
        entry = result.scalars().first()
        if not entry:
            raise ValueError(f"Waitlist entry not found: {entry_id}")
        if entry.tenant_id != tenant_id:
            raise ValueError("Waitlist entry does not belong to this tenant.")

        entry_provider_id = entry.provider_id
        # Default duration window — 60 minutes covers typical appointment lengths
        window = timedelta(minutes=60)
        window_start = target_dt - window
        window_end = target_dt + window

        filters = [
            Caller.tenant_id == tenant_id,
            Appointment.status.in_(
                [AppointmentStatus.CONFIRMED, AppointmentStatus.RESCHEDULED]
            ),
            Appointment.scheduled_at >= window_start,
            Appointment.scheduled_at <= window_end,
        ]
        # If the entry targets a specific provider, narrow to that provider only.
        # If not, show conflicts across all providers (admin will pick a provider).
        if entry_provider_id is not None:
            filters.append(Appointment.provider_id == entry_provider_id)

        appt_rows = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(and_(*filters))
            .order_by(Appointment.scheduled_at.asc())
        )
        appts = list(appt_rows.scalars().all())

        # Build a provider_id -> name map for friendly display
        provider_names: dict[str, str] = {}
        provider_ids = {a.provider_id for a in appts if a.provider_id}
        if provider_ids:
            prov_rows = await session.execute(
                select(Trainer).where(Trainer.id.in_(provider_ids))
            )
            for p in prov_rows.scalars().all():
                provider_names[str(p.id)] = p.name

    conflicts = [
        {
            "id": str(a.id),
            "client_name": a.client_name,
            "client_phone": a.client_phone,
            "appointment_type": a.appointment_type,
            "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
            "duration_minutes": a.duration_minutes,
            "status": a.status.value if a.status else None,
            "provider_id": str(a.provider_id) if a.provider_id else None,
            "provider_name": (
                provider_names.get(str(a.provider_id)) if a.provider_id else None
            ),
        }
        for a in appts
    ]

    logger.info(
        "[Waitlist] Conflict check for entry %s @ %s → %d conflicts (provider=%s)",
        entry_id, scheduled_at, len(conflicts), entry_provider_id,
    )

    return {
        "conflicts": conflicts,
        "provider_id": str(entry_provider_id) if entry_provider_id else None,
        "scheduled_at": scheduled_at,
        "window_minutes": int(window.total_seconds() // 60),
    }


async def promote_to_appointment(
    entry_id: uuid.UUID,
    scheduled_at: str,
    tenant_ctx: Any | None = None,
    force: bool = False,
) -> dict | None:
    """
    Promote a waitlist entry to a booked appointment.

    Books the appointment via calendar_service using the entry's caller info
    and the admin-supplied start time, then marks the entry as BOOKED and
    sends the caller a confirmation SMS.

    Args:
        entry_id: UUID of the waitlist entry to promote.
        scheduled_at: ISO datetime string for the booked slot.
        tenant_ctx: TenantContext for calendar routing + SMS.
        force: When True, bypass the provider/time uniqueness check by
            nudging the stored timestamp by a few seconds so the partial
            unique index doesn't fire. Use only when admin has explicitly
            confirmed the double-book in the UI.

    Returns:
        Dict with appointment + entry info on success, or None on failure.
    """
    # Lazy import to avoid circular imports (calendar_service imports a lot)
    from backend.services import calendar_service

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry).where(WaitlistEntry.id == entry_id)
        )
        entry = result.scalars().first()

        if not entry:
            logger.warning("[Waitlist] Promote failed — entry %s not found", entry_id)
            return None

        if entry.status in (WaitlistStatus.BOOKED, WaitlistStatus.CANCELLED, WaitlistStatus.EXPIRED):
            logger.warning(
                "[Waitlist] Promote skipped — entry %s already %s",
                entry_id,
                entry.status.value,
            )
            return None

        # Snapshot the fields we need outside the session
        caller_info = {
            "name": entry.client_name,
            "phone": entry.client_phone,
            "email": entry.client_email or "",
        }
        appointment_type = entry.appointment_type
        preferred_date = entry.preferred_date
        client_name = entry.client_name
        client_phone = entry.client_phone
        entry_provider_id = entry.provider_id
        entry_is_test = entry.is_test or False

    # Book the appointment through the same path the AI uses.
    # The admin UI sends a naive datetime (e.g. "2026-06-26T14:00:00") that is
    # in the tenant's LOCAL timezone, not UTC. Attach the tenant tz here so
    # create_native_booking converts it correctly to UTC for storage. Without
    # this, a naive string is assumed UTC → calendar shows wrong time (e.g.
    # 2 PM local becomes 7:30 PM IST when displayed back).
    booking_start = scheduled_at
    try:
        _bs_dt = datetime.fromisoformat(booking_start)
        if _bs_dt.tzinfo is None:
            from zoneinfo import ZoneInfo as _ZI
            from backend.defaults import DEFAULT_TIMEZONE as _DT
            _tz_name = getattr(tenant_ctx, "timezone", None) if tenant_ctx else None
            try:
                _bs_dt = _bs_dt.replace(tzinfo=_ZI(_tz_name or _DT))
            except Exception:
                _bs_dt = _bs_dt.replace(tzinfo=_ZI(_DT))
            booking_start = _bs_dt.isoformat()
    except (ValueError, TypeError):
        pass  # leave booking_start as-is; create_native_booking will catch parse errors

    try:
        booking = await calendar_service.book_appointment(
            caller_info=caller_info,
            start_time=booking_start,
            tenant_ctx=tenant_ctx,
            appointment_type_key=appointment_type,
            provider_id=entry_provider_id,
            is_test=entry_is_test,
        )
    except Exception as exc:
        logger.error("[Waitlist] Booking failed during promote of %s: %s", entry_id, exc)
        return None

    if not booking:
        logger.warning("[Waitlist] book_appointment returned None during promote of %s", entry_id)
        return None

    # The native path returns CONFLICT when the unique-index races. When the
    # admin has explicitly opted to force the double-book, retry with a tiny
    # offset (a few seconds) so the partial unique index on
    # (tenant_id, provider_id, scheduled_at) doesn't fire — the faculty member has
    # explicitly agreed to take an overlapping appointment.
    if isinstance(booking, dict) and booking.get("status") == "CONFLICT":
        if force:
            try:
                # Use booking_start (already tz-aware with tenant tz) not raw scheduled_at
                forced_dt = datetime.fromisoformat(booking_start)
                if forced_dt.tzinfo is None:
                    forced_dt = forced_dt.replace(tzinfo=timezone.utc)
                # Walk forward by a few seconds at a time until the unique
                # index lets us in. Cap at 12 retries (~1 minute) — beyond
                # that something else is wrong.
                booking = None
                for offset in range(1, 13):
                    nudged = forced_dt + timedelta(seconds=offset * 5)
                    nudged_iso = nudged.isoformat()
                    try:
                        booking = await calendar_service.book_appointment(
                            caller_info=caller_info,
                            start_time=nudged_iso,
                            tenant_ctx=tenant_ctx,
                            appointment_type_key=appointment_type,
                            provider_id=entry_provider_id,
                            is_test=entry_is_test,
                        )
                    except Exception as exc:
                        logger.error(
                            "[Waitlist] Force-promote retry failed (offset=%ds): %s",
                            offset * 5, exc,
                        )
                        return None
                    if not booking:
                        return None
                    if isinstance(booking, dict) and booking.get("status") == "CONFLICT":
                        continue  # try next offset
                    # Success — record the actual booked time so callers + SMS
                    # show the slightly nudged timestamp.
                    booking_start = nudged_iso
                    logger.info(
                        "[Waitlist] Force-promote of %s succeeded with +%ds offset",
                        entry_id, offset * 5,
                    )
                    break
                else:
                    logger.warning(
                        "[Waitlist] Force-promote of %s exhausted offset retries",
                        entry_id,
                    )
                    return None
                # If after the loop we still hold a CONFLICT, give up.
                if isinstance(booking, dict) and booking.get("status") == "CONFLICT":
                    return None
            except (ValueError, TypeError) as exc:
                logger.error("[Waitlist] Force-promote parse failed: %s", exc)
                return None
        else:
            logger.warning(
                "[Waitlist] Promote of %s lost the unique-index race — slot %s already taken",
                entry_id, scheduled_at,
            )
            return None

    # Mark the entry BOOKED; record the actual appointment time for display
    now = datetime.now(timezone.utc)
    try:
        appt_dt = datetime.fromisoformat(booking_start)
        if appt_dt.tzinfo is None:
            appt_dt = appt_dt.replace(tzinfo=timezone.utc)
        else:
            appt_dt = appt_dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        appt_dt = None

    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry).where(WaitlistEntry.id == entry_id)
        )
        entry = result.scalars().first()
        if entry:
            entry.status = WaitlistStatus.BOOKED
            entry.booked_at = now
            entry.appointment_scheduled_at = appt_dt
            await session.commit()

    # SMS the caller about the promotion (use the time we actually booked,
    # which may be nudged a few seconds from `scheduled_at` for force-promote)
    try:
        sms_start = booking_start
        if isinstance(sms_start, str):
            try:
                start_dt = datetime.fromisoformat(sms_start)
            except ValueError:
                start_dt = datetime.strptime(sms_start, "%Y-%m-%dT%H:%M:%S")
        else:
            start_dt = sms_start
        if start_dt.tzinfo is None:
            # The admin-supplied time is in the tenant's LOCAL timezone (no UTC shift).
            # Attach the tenant tz so _to_local_time doesn't re-convert it.
            try:
                from zoneinfo import ZoneInfo as _ZI
                _tz_name = getattr(tenant_ctx, "timezone", None) if tenant_ctx else None
                from backend.defaults import DEFAULT_TIMEZONE as _DT
                _tz = _ZI(_tz_name or _DT)
            except Exception:
                from datetime import timezone as _tz_mod
                _tz = _tz_mod.utc
            start_dt = start_dt.replace(tzinfo=_tz)

        sms_service.send_waitlist_promoted(
            caller_name=client_name,
            phone=client_phone,
            appointment_type=appointment_type,
            scheduled_at=start_dt,
            tenant_ctx=tenant_ctx,
            is_test=entry_is_test,
        )
    except Exception as exc:
        logger.error("[Waitlist] Failed to send 'promoted' SMS for entry %s: %s", entry_id, exc)

    logger.info(
        "[Waitlist] Promoted entry %s (%s, %s on %s) → booking %s",
        entry_id,
        client_name,
        appointment_type,
        preferred_date,
        booking.get("uid") or booking.get("id"),
    )

    return {
        "id": str(entry_id),
        "status": WaitlistStatus.BOOKED.value,
        "booked_at": now.isoformat(),
        "appointment_type": appointment_type,
        "client_name": client_name,
        "client_phone": client_phone,
        "scheduled_at": booking_start,
        "forced": bool(force),
        "booking": booking,
    }


# ── Expire stale entries (background task) ──────────────────────────────────


async def expire_stale_entries() -> int:
    """
    Find all WAITING entries whose expires_at has passed and set their
    status to EXPIRED. Intended to be called periodically by a background
    task (e.g. every 30 minutes).

    Returns:
        The number of entries that were expired.
    """
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            update(WaitlistEntry)
            .where(
                and_(
                    WaitlistEntry.status == WaitlistStatus.WAITING,
                    WaitlistEntry.expires_at < now,
                )
            )
            .values(status=WaitlistStatus.EXPIRED)
        )
        expired_count = result.rowcount
        await session.commit()

    if expired_count > 0:
        logger.info("[Waitlist] Expired %d stale waitlist entries", expired_count)
    else:
        logger.debug("[Waitlist] No stale entries to expire")

    return expired_count
