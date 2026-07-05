"""
Inbound SMS handling service — processes incoming text messages from callers
and routes them to the appropriate handler.

Flow:
  1. Twilio delivers inbound SMS as POST form data (From, To, Body, MessageSid)
  2. We resolve the tenant by matching the To number against tenants' twilio_phone_number
  3. Log the inbound message
  4. Route:
     a. Quick-reply keywords (C/CONFIRM/YES, R/RESCHEDULE, X/CANCEL, STOP) -> fast path
     b. Waitlist confirmation (YES with a NOTIFIED waitlist entry) -> book the slot
     c. Everything else -> LLM agent for agentic two-way SMS conversation
  5. Log and send the outbound reply

Multi-tenant: resolved by matching the Twilio To number to a tenant row.
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, and_

from backend.database import async_session
from backend.models.tenant import Tenant, TenantStatus
from backend.models.caller import Caller
from backend.models.sms_message import SMSMessage, SMSDirection
from backend.models.appointment import Appointment, AppointmentStatus
from backend.models.waitlist import WaitlistEntry, WaitlistStatus
from backend.services import sms_service, llm_service, waitlist_service
from backend.services.caller_service import _phone_digits_tail, _phone_col_clean, upsert_caller
from backend.services.tenant_service import _tenant_to_context, TenantContext


def _contact_line(tenant_ctx: TenantContext) -> str:
    """Return 'Please call us at +1234' or 'Please reply to this message' when no phone."""
    phone = tenant_ctx.business_phone or tenant_ctx.escalation_phone
    if phone:
        return f"Please call us at {phone}"
    return "Please reply to this message"

logger = logging.getLogger(__name__)


# ── Phone number normalisation ────────────────────────────────────────────────


def _normalize_phone(phone: str) -> str:
    """Normalize a phone number: strip whitespace and ensure +1 prefix for US numbers.

    Examples:
        '  2125551234 '   -> '+12125551234'
        '12125551234'     -> '+12125551234'
        '+12125551234'    -> '+12125551234'
        '+442071234567'   -> '+442071234567'  (non-US left as-is)
    """
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not cleaned:
        return cleaned
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("1") and len(cleaned) == 11:
        return f"+{cleaned}"
    if len(cleaned) == 10:
        return f"+1{cleaned}"
    # Fallback: prefix with + if not already present
    return f"+{cleaned}"


# ── Tenant resolution ─────────────────────────────────────────────────────────


async def resolve_tenant_by_phone(to_number: str) -> tuple[Tenant | None, TenantContext | None]:
    """Resolve a tenant by matching the inbound SMS 'To' number against
    the tenant's configured twilio_phone_number.

    Normalizes both numbers before comparison to handle formatting differences
    (e.g. '+1 555 123 4567' vs '+15551234567').

    Args:
        to_number: The Twilio phone number the caller texted (the institute's number).

    Returns:
        (Tenant, TenantContext) if an active tenant is found, (None, None) otherwise.
    """
    normalized = _normalize_phone(to_number)
    if not normalized:
        logger.warning("[SMS Inbound] Empty to_number — cannot resolve tenant")
        return None, None

    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(
                Tenant.status == TenantStatus.ACTIVE,
            )
        )
        tenants = result.scalars().all()

    # Match by normalized phone — DB values may have inconsistent formatting
    for tenant in tenants:
        tenant_phone = _normalize_phone(tenant.twilio_phone_number or "")
        if tenant_phone and tenant_phone == normalized:
            ctx = _tenant_to_context(tenant)
            logger.info(
                "[SMS Inbound] Resolved tenant: %s (%s) for To=%s",
                tenant.slug, tenant.business_name, to_number,
            )
            return tenant, ctx

    logger.warning("[SMS Inbound] No active tenant found for To=%s (normalized: %s)", to_number, normalized)
    return None, None


# ── SMS logging helper ────────────────────────────────────────────────────────


async def _log_sms(
    tenant_id: uuid.UUID,
    direction: SMSDirection,
    from_number: str,
    to_number: str,
    body: str,
    caller_phone: str,
    twilio_sid: str = "",
) -> None:
    """Persist an SMS message record for audit and conversation threading.

    Upserts a caller record for caller_phone (required — SMSMessage.caller_id
    is NOT NULL). The tenant_id is stored on the caller, not on SMSMessage.

    Args:
        tenant_id: The tenant this message belongs to.
        direction: INBOUND or OUTBOUND.
        from_number: The sender's phone number.
        to_number: The recipient's phone number.
        body: The message text.
        caller_phone: The caller's phone (used to look up / create caller).
        twilio_sid: Twilio's MessageSid for delivery tracking.
    """
    try:
        # Upsert caller so caller_id is always set (is_test derived from stored flag)
        caller_rec = await upsert_caller(
            name="",
            phone=caller_phone,
            email="",
            tenant_id=tenant_id,
            is_test=caller_phone.startswith("+1555"),
        )
        caller_id = caller_rec.id if caller_rec else None

        async with async_session() as session:
            msg = SMSMessage(
                caller_id=caller_id,
                direction=direction,
                from_number=from_number,
                to_number=to_number,
                body=body,
                twilio_sid=twilio_sid,
            )
            session.add(msg)
            await session.commit()
    except Exception as exc:
        logger.error(
            "[SMS Inbound] Failed to log %s SMS (tenant=%s, caller=%s): %s",
            direction.value, tenant_id, caller_phone, exc,
        )


# ── Quick-reply handlers ─────────────────────────────────────────────────────


async def _handle_confirmation(
    from_number: str,
    tenant_id: uuid.UUID,
    tenant_ctx: TenantContext,
) -> str:
    """Handle a caller confirming their upcoming appointment via SMS reply.

    Finds the next upcoming CONFIRMED appointment for the given phone + tenant
    and sets confirmed_by_client = True.

    Args:
        from_number: The caller's phone number.
        tenant_id: The tenant UUID.
        tenant_ctx: TenantContext for business name etc.

    Returns:
        A human-readable reply message.
    """
    # Use suffix matching (last 10 digits) so "+6352418405" and "+16352418405"
    # both resolve to the same appointment — no more two-pass lookup needed.
    phone_tail = _phone_digits_tail(from_number)
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == tenant_id,
                    _phone_col_clean(Appointment.client_phone).endswith(phone_tail),
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.scheduled_at >= now,
                )
            ).order_by(Appointment.scheduled_at.asc()).limit(1)
        )
        appt = result.scalar_one_or_none()

        if not appt:
            logger.info(
                "[SMS Inbound] Confirmation received but no upcoming appointment found "
                "(phone=%s, tenant=%s)",
                from_number, tenant_id,
            )
            return (
                f"Thanks for your reply! We don't see an upcoming appointment for this number. "
                f"{_contact_line(tenant_ctx)} if you need assistance."
            )

        appt.confirmed_by_client = True
        await session.commit()

    _local_appt = sms_service._to_local_time(appt.scheduled_at, tenant_ctx)
    date_str = _local_appt.strftime("%A, %B %d")
    time_str = _local_appt.strftime("%I:%M %p").lstrip("0")
    biz_name = tenant_ctx.business_name

    logger.info(
        "[SMS Inbound] Appointment confirmed by caller (appt=%s, phone=%s, tenant=%s)",
        appt.id, from_number, tenant_id,
    )
    return (
        f"Your appointment at {biz_name} on {date_str} at {time_str} is confirmed. "
        f"See you then!"
    )


async def _handle_cancel_request(
    from_number: str,
    tenant_id: uuid.UUID,
    tenant_ctx: TenantContext,
) -> str:
    """Handle a caller requesting cancellation of their upcoming appointment.

    Finds the next upcoming appointment (CONFIRMED or RESCHEDULED) for the given
    phone + tenant, cancels it, and checks the waitlist for anyone who could
    fill the opening.

    Args:
        from_number: The caller's phone number.
        tenant_id: The tenant UUID.
        tenant_ctx: TenantContext for business name and waitlist notifications.

    Returns:
        A human-readable reply message.
    """
    normalized_phone = _normalize_phone(from_number)
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Find the next upcoming appointment for this caller
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == tenant_id,
                    Appointment.client_phone.in_([normalized_phone, from_number]),
                    Appointment.status.in_([
                        AppointmentStatus.CONFIRMED,
                        AppointmentStatus.RESCHEDULED,
                    ]),
                    Appointment.scheduled_at >= now,
                )
            ).order_by(Appointment.scheduled_at.asc()).limit(1)
        )
        appt = result.scalar_one_or_none()

        if not appt:
            logger.info(
                "[SMS Inbound] Cancel request but no upcoming appointment found "
                "(phone=%s, tenant=%s)",
                from_number, tenant_id,
            )
            return (
                f"We don't see an upcoming appointment for this number. "
                f"{_contact_line(tenant_ctx)} if you need help."
            )

        # Save details before cancelling (for SMS and waitlist)
        appt_type = appt.appointment_type
        scheduled_at = appt.scheduled_at
        client_name = appt.client_name
        cancelled_provider_id = appt.provider_id
        date_str = scheduled_at.strftime("%Y-%m-%d")
        slot_str = scheduled_at.isoformat()

        # Cancel the appointment
        appt.status = AppointmentStatus.CANCELLED
        await session.commit()

    logger.info(
        "[SMS Inbound] Appointment cancelled via SMS (appt=%s, phone=%s, tenant=%s)",
        appt.id, from_number, tenant_id,
    )

    # Send cancellation confirmation SMS
    sms_service.send_cancellation(
        caller_name=client_name,
        phone=from_number,
        scheduled_at=scheduled_at,
        tenant_ctx=tenant_ctx,
    )

    # Check waitlist for anyone who could fill the now-open slot
    try:
        await waitlist_service.check_waitlist_for_opening(
            tenant_id=tenant_id,
            appointment_type=appt_type,
            date=date_str,
            available_slot=slot_str,
            tenant_ctx=tenant_ctx,
            provider_id=cancelled_provider_id,
        )
    except Exception as exc:
        logger.error(
            "[SMS Inbound] Waitlist check failed after cancellation (appt=%s): %s",
            appt.id, exc,
        )

    _local_cancel = sms_service._to_local_time(scheduled_at, tenant_ctx)
    display_date = _local_cancel.strftime("%A, %B %d")
    display_time = _local_cancel.strftime("%I:%M %p").lstrip("0")
    biz_phone = tenant_ctx.business_phone or tenant_ctx.escalation_phone
    call_part = f"call us at {biz_phone} or j" if biz_phone else "J"

    return (
        f"Your appointment on {display_date} at {display_time} has been cancelled. "
        f"If you'd like to reschedule, {call_part}ust text us the date that works for you."
    )


# ── Waitlist confirmation check ───────────────────────────────────────────────


async def _has_notified_waitlist_entry(phone: str, tenant_id: uuid.UUID) -> bool:
    """Check if this phone number has a NOTIFIED waitlist entry for this tenant.

    Used to distinguish between a general 'YES' (appointment confirmation) and
    a waitlist slot acceptance.

    Args:
        phone: The caller's phone number.
        tenant_id: The tenant UUID.

    Returns:
        True if a NOTIFIED waitlist entry exists, False otherwise.
    """
    normalized = _normalize_phone(phone)
    async with async_session() as session:
        result = await session.execute(
            select(WaitlistEntry.id)
            .join(Caller, WaitlistEntry.caller_id == Caller.id)
            .where(
                and_(
                    WaitlistEntry.client_phone.in_([normalized, phone]),
                    Caller.tenant_id == tenant_id,
                    WaitlistEntry.status == WaitlistStatus.NOTIFIED,
                )
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


# ── Main entry point ──────────────────────────────────────────────────────────


async def handle_inbound_sms(
    from_number: str,
    to_number: str,
    body: str,
    twilio_sid: str = "",
) -> str:
    """Process an inbound SMS from a caller and return the response text.

    This is the main entry point called by the Twilio webhook route. The caller
    wraps the returned string in a TwiML <Message> response.

    Routing priority:
      1. STOP keyword -> no reply (Twilio handles opt-out automatically)
      2. Tenant resolution -> reject if no tenant found
      3. Log the inbound message
      4. Quick-reply keywords (C/CONFIRM/YES, R/RESCHEDULE, X/CANCEL)
         - YES with a NOTIFIED waitlist entry -> waitlist confirmation path
         - YES/C/CONFIRM without waitlist entry -> appointment confirmation
      5. All other messages -> LLM agent

    Args:
        from_number: The caller's phone number (Twilio 'From').
        to_number: The institute's Twilio phone number (Twilio 'To').
        body: The message text (Twilio 'Body').
        twilio_sid: Twilio's MessageSid for tracking.

    Returns:
        The reply text to send back to the caller. Empty string means no reply
        (e.g. for STOP messages).
    """
    text = body.strip()
    keyword = text.upper()

    logger.info(
        "[SMS Inbound] Received: From=%s To=%s Body=%r SID=%s",
        from_number, to_number, text[:100], twilio_sid,
    )

    # ── 1. STOP — opt-out (Twilio handles this; we just log and exit) ────
    if keyword == "STOP":
        logger.info("[SMS Inbound] STOP received from %s — opt-out logged, no reply", from_number)
        # Still attempt tenant resolution for logging, but don't fail if not found
        tenant, _ = await resolve_tenant_by_phone(to_number)
        if tenant:
            await _log_sms(
                tenant_id=tenant.id,
                direction=SMSDirection.INBOUND,
                from_number=from_number,
                to_number=to_number,
                body=text,
                caller_phone=from_number,
                twilio_sid=twilio_sid,
            )
        return ""

    # ── 2. Resolve tenant ────────────────────────────────────────────────
    tenant, tenant_ctx = await resolve_tenant_by_phone(to_number)
    if not tenant or not tenant_ctx:
        logger.error(
            "[SMS Inbound] No tenant for To=%s — dropping message from %s",
            to_number, from_number,
        )
        return "Sorry, we couldn't process your message. Please call the office directly."

    tenant_id = tenant.id

    # ── 3. Log inbound message ───────────────────────────────────────────
    await _log_sms(
        tenant_id=tenant_id,
        direction=SMSDirection.INBOUND,
        from_number=from_number,
        to_number=to_number,
        body=text,
        caller_phone=from_number,
        twilio_sid=twilio_sid,
    )

    # ── 4. Quick-reply keyword routing ───────────────────────────────────
    reply = ""

    if keyword in ("C", "CONFIRM"):
        reply = await _handle_confirmation(from_number, tenant_id, tenant_ctx)

    elif keyword in ("YES", "Y"):
        # YES can mean either waitlist confirmation or appointment confirmation.
        # Check for a NOTIFIED waitlist entry first (more specific match wins).
        if await _has_notified_waitlist_entry(from_number, tenant_id):
            reply = await _handle_waitlist_confirmation(from_number, tenant_id, tenant_ctx)
        else:
            reply = await _handle_confirmation(from_number, tenant_id, tenant_ctx)

    elif keyword in ("R", "RESCHEDULE"):
        biz_phone = tenant_ctx.business_phone or tenant_ctx.escalation_phone
        call_part = f"Call us at {biz_phone} to reschedule, or t" if biz_phone else "T"
        reply = f"{call_part}ell me what date works better and I'll find you a slot."
        logger.info("[SMS Inbound] Reschedule keyword from %s (tenant=%s)", from_number, tenant_id)

    elif keyword in ("X", "CANCEL"):
        # First step of two-step cancellation: ask for confirmation.
        # When the caller replies YES, the LLM agent (else branch below)
        # will see the conversation context and handle the actual cancellation.
        # We also support "CANCEL YES" as a single-message shortcut.
        reply = (
            "Are you sure you want to cancel your upcoming appointment? "
            "Reply YES to confirm cancellation."
        )
        logger.info("[SMS Inbound] Cancel keyword from %s (tenant=%s) — awaiting confirmation", from_number, tenant_id)

    elif keyword == "CANCEL YES":
        # Single-message shortcut: caller sent "CANCEL YES" to skip the two-step flow.
        reply = await _handle_cancel_request(from_number, tenant_id, tenant_ctx)

    else:
        # ── 5. LLM agent — agentic two-way SMS ──────────────────────────
        session_key = f"sms-{tenant_id}-{_normalize_phone(from_number)}"
        try:
            logger.info(
                "[SMS Inbound] Routing to LLM agent (session=%s, tenant=%s)",
                session_key, tenant_ctx.slug,
            )
            ai_response = await llm_service.process_message(
                call_id=session_key,
                user_message=text,
                tenant_ctx=tenant_ctx,
                caller_number=from_number,
            )
            reply = ai_response.strip() if ai_response else ""
        except Exception as exc:
            logger.error(
                "[SMS Inbound] LLM processing failed (session=%s): %s",
                session_key, exc, exc_info=True,
            )
            reply = (
                f"Sorry, I had trouble understanding your message. "
                f"{_contact_line(tenant_ctx)} for assistance."
            )

    if not reply:
        logger.info("[SMS Inbound] No reply generated for message from %s", from_number)
        return ""

    # ── 6. Send reply and log outbound ───────────────────────────────────
    sms_service._send_sms(to=from_number, body=reply, tenant_ctx=tenant_ctx)

    await _log_sms(
        tenant_id=tenant_id,
        direction=SMSDirection.OUTBOUND,
        from_number=to_number,
        to_number=from_number,
        body=reply,
        caller_phone=from_number,
    )

    logger.info(
        "[SMS Inbound] Reply sent to %s (%d chars, tenant=%s)",
        from_number, len(reply), tenant_ctx.slug,
    )

    return reply


# ── Waitlist confirmation handler ─────────────────────────────────────────────


async def _handle_waitlist_confirmation(
    from_number: str,
    tenant_id: uuid.UUID,
    tenant_ctx: TenantContext,
) -> str:
    """Handle a caller confirming a waitlist slot via YES reply.

    Calls waitlist_service.confirm_waitlist_booking() to mark the entry as
    BOOKED and returns a confirmation message to the caller.

    Args:
        from_number: The caller's phone number.
        tenant_id: The tenant UUID.
        tenant_ctx: TenantContext for business name.

    Returns:
        A human-readable reply message.
    """
    try:
        booking = await waitlist_service.confirm_waitlist_booking(
            client_phone=_normalize_phone(from_number),
            tenant_id=tenant_id,
        )

        if not booking:
            # Fallback: try with the raw phone number
            booking = await waitlist_service.confirm_waitlist_booking(
                client_phone=from_number,
                tenant_id=tenant_id,
            )

        if not booking:
            logger.warning(
                "[SMS Inbound] Waitlist confirmation failed — no NOTIFIED entry found "
                "(phone=%s, tenant=%s)",
                from_number, tenant_id,
            )
            return (
                f"We couldn't find a pending waitlist offer for your number. "
                f"{_contact_line(tenant_ctx)} for assistance."
            )

        appt_type = booking.get("appointment_type", "appointment").replace("_", " ").title()
        preferred_date = booking.get("preferred_date", "")
        client_name = booking.get("client_name", "")
        biz_name = tenant_ctx.business_name

        # Format the date for display
        display_date = preferred_date
        try:
            from datetime import datetime as dt_cls
            parsed = dt_cls.strptime(preferred_date, "%Y-%m-%d")
            display_date = parsed.strftime("%A, %B %d")
        except (ValueError, TypeError):
            pass

        logger.info(
            "[SMS Inbound] Waitlist booking confirmed for %s — %s on %s (tenant=%s)",
            client_name, appt_type, preferred_date, tenant_id,
        )

        return (
            f"Great news, {client_name}! Your {appt_type} at {biz_name} "
            f"on {display_date} has been confirmed. We'll see you then!"
        )

    except Exception as exc:
        logger.error(
            "[SMS Inbound] Waitlist confirmation error (phone=%s, tenant=%s): %s",
            from_number, tenant_id, exc, exc_info=True,
        )
        return (
            f"Sorry, something went wrong confirming your slot. "
            f"{_contact_line(tenant_ctx)} for assistance."
        )
