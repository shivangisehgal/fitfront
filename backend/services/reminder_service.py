"""
Reminder & follow-up service — sends SMS notifications for upcoming and
completed appointments on a scheduled interval.

Multi-tenant: iterates over all ACTIVE tenants, resolves their TenantContext
for per-tenant Twilio creds and business name, then sends reminders/followups
scoped to each tenant.

- 2h-before reminder: "Your appointment is coming up today at {time}"
- Post-visit follow-up: "How was your visit? Reply 1-5 to rate"
- Google review solicitation: sent after follow-up with review link

All run as async background tasks started during FastAPI lifespan.
Respects per-tenant reminder_settings and review_settings.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_

from backend.database import async_session
from backend.models.appointment import Appointment, AppointmentStatus
from backend.models.caller import Caller
from backend.models.tenant import Tenant, TenantStatus
from backend.services import sms_service, waitlist_service
from backend.services.tenant_service import _tenant_to_context

logger = logging.getLogger(__name__)

# How often the scheduler checks (in seconds)
CHECK_INTERVAL = 30 * 60  # 30 minutes


# ── Per-tenant reminder cycle ───────────────────────────────────────────────


async def _process_tenant_reminders(tenant: Tenant) -> tuple[int, int, int]:
    """
    Send reminders, follow-ups, and review requests for a single tenant.
    Returns (reminders_2h, followups, reviews).
    """
    ctx = _tenant_to_context(tenant)
    now = datetime.now(timezone.utc)

    reminders_2h = await _send_2h_reminders(tenant.id, ctx, now)
    followups_sent = await _send_followups(tenant.id, ctx, now)
    reviews_sent = await _send_review_requests(tenant.id, ctx, now)

    return reminders_2h, followups_sent, reviews_sent


# ── Reminder (2h before) ───────────────────────────────────────────────────

async def _send_2h_reminders(tenant_id, tenant_ctx, now: datetime) -> int:
    """
    Find CONFIRMED appointments in the next 1.5-2.5h window that haven't
    received a 2h reminder yet, scoped to a tenant. Send an SMS for each.
    """
    if tenant_ctx and tenant_ctx.reminder_settings.get("2h_enabled") is False:
        return 0

    window_start = now + timedelta(hours=1.5)
    window_end = now + timedelta(hours=2.5)

    filters = [
        Appointment.status == AppointmentStatus.CONFIRMED,
        Appointment.scheduled_at >= window_start,
        Appointment.scheduled_at <= window_end,
        Appointment.reminder_2h_sent_at.is_(None),
    ]
    if tenant_id:
        filters.append(Caller.tenant_id == tenant_id)

    async with async_session() as session:
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(and_(*filters))
        )
        appointments = result.scalars().all()

        sent = 0
        for appt in appointments:
            ok = sms_service.send_reminder(
                caller_name=appt.client_name,
                phone=appt.client_phone,
                appointment_type=appt.appointment_type.replace("_", " ").title(),
                scheduled_at=appt.scheduled_at,
                tenant_ctx=tenant_ctx,
            )
            if ok:
                appt.reminder_2h_sent_at = now
                sent += 1
                logger.info("[Reminders][%s] Sent 2h reminder to %s for %s",
                            tenant_ctx.slug if tenant_ctx else "default",
                            appt.client_phone, appt.scheduled_at)

        if sent:
            await session.commit()

    return sent


# ── Follow-up (24h after) ──────────────────────────────────────────────────

async def _send_followups(tenant_id, tenant_ctx, now: datetime) -> int:
    """
    Find appointments that ended 20-28h ago, haven't been followed up,
    and are CONFIRMED or COMPLETED. Send a satisfaction SMS.
    """
    window_start = now - timedelta(hours=28)
    window_end = now - timedelta(hours=20)

    filters = [
        Appointment.status.in_([
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.COMPLETED,
        ]),
        Appointment.scheduled_at >= window_start,
        Appointment.scheduled_at <= window_end,
        Appointment.followup_sent_at.is_(None),
    ]
    if tenant_id:
        filters.append(Caller.tenant_id == tenant_id)

    async with async_session() as session:
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(and_(*filters))
        )
        appointments = result.scalars().all()

        sent = 0
        for appt in appointments:
            ok = sms_service.send_followup(
                caller_name=appt.client_name,
                phone=appt.client_phone,
                tenant_ctx=tenant_ctx,
            )
            if ok:
                appt.followup_sent_at = now
                sent += 1
                logger.info("[Reminders][%s] Sent follow-up to %s",
                            tenant_ctx.slug if tenant_ctx else "default",
                            appt.client_phone)

        if sent:
            await session.commit()

    return sent


# ── Google review solicitation ─────────────────────────────────────────────


def _send_review_sms(caller_name: str, phone: str, review_link: str,
                     tenant_ctx) -> bool:
    """Send a Google review solicitation SMS via the generic SMS sender."""
    business_name = tenant_ctx.business_name if tenant_ctx else "our office"
    body = (
        f"Hi {caller_name}! We're glad you visited {business_name}. "
        f"If you had a great experience, we'd love a quick review: "
        f"{review_link}. Thank you!"
    )
    ok = sms_service._send_sms(phone, body, tenant_ctx)
    if ok:
        sms_service._log_outbound_sms(tenant_ctx, to_number=phone, body=body, caller_phone=phone)
    return ok


async def _send_review_requests(tenant_id, tenant_ctx, now: datetime) -> int:
    """
    Find CONFIRMED/COMPLETED appointments whose scheduled_at is 20-28h in the
    past, whose follow-up has already been sent, and that haven't received a
    review request yet. Send a Google review solicitation SMS for each.
    """
    if tenant_ctx and tenant_ctx.review_settings.get("enabled") is False:
        return 0

    review_link = (
        tenant_ctx.review_settings.get("google_review_link", "")
        if tenant_ctx else ""
    )
    if not review_link:
        return 0

    window_start = now - timedelta(hours=28)
    window_end = now - timedelta(hours=20)

    filters = [
        Appointment.status.in_([
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.COMPLETED,
        ]),
        Appointment.scheduled_at >= window_start,
        Appointment.scheduled_at <= window_end,
        Appointment.review_requested_at.is_(None),
        Appointment.followup_sent_at.isnot(None),
    ]
    if tenant_id:
        filters.append(Caller.tenant_id == tenant_id)

    async with async_session() as session:
        result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(and_(*filters))
        )
        appointments = result.scalars().all()

        sent = 0
        for appt in appointments:
            ok = _send_review_sms(
                caller_name=appt.client_name,
                phone=appt.client_phone,
                review_link=review_link,
                tenant_ctx=tenant_ctx,
            )
            if ok:
                appt.review_requested_at = now
                sent += 1
                logger.info("[Reminders][%s] Sent review request to %s",
                            tenant_ctx.slug if tenant_ctx else "default",
                            appt.client_phone)

        if sent:
            await session.commit()

    return sent


# ── Background scheduler ────────────────────────────────────────────────────

async def run_reminder_loop():
    """
    Long-running async task that periodically sends reminders and follow-ups.
    Iterates over all ACTIVE tenants so each gets their own branded SMS.
    Started in FastAPI's lifespan; runs until cancelled.
    """
    logger.info("[Reminders] Background reminder scheduler started (interval=%ds)", CHECK_INTERVAL)

    while True:
        try:
            # Load all active tenants
            async with async_session() as session:
                result = await session.execute(
                    select(Tenant).where(Tenant.status == TenantStatus.ACTIVE)
                )
                tenants = result.scalars().all()

            if tenants:
                total_2h = 0
                total_followups = 0
                total_reviews = 0
                for tenant in tenants:
                    try:
                        r2, f, rv = await _process_tenant_reminders(tenant)
                        total_2h += r2
                        total_followups += f
                        total_reviews += rv
                    except Exception as exc:
                        logger.error("[Reminders] Error processing tenant %s: %s",
                                     tenant.slug, exc, exc_info=True)

                if total_2h or total_followups or total_reviews:
                    logger.info(
                        "[Reminders] Cycle complete: %d 2h reminders, "
                        "%d follow-ups, %d reviews across %d tenant(s)",
                        total_2h, total_followups, total_reviews,
                        len(tenants),
                    )
            else:
                # No tenants in DB — fall back to unscoped (legacy mode)
                now = datetime.now(timezone.utc)
                reminders_2h = await _send_2h_reminders(None, None, now)
                followups = await _send_followups(None, None, now)
                if reminders_2h or followups:
                    logger.info("[Reminders] Legacy mode: %d 2h reminders, %d follow-ups sent",
                                reminders_2h, followups)

            # Expire stale waitlist entries each cycle
            try:
                await waitlist_service.expire_stale_entries()
            except Exception as exc:
                logger.error("[Reminders] Waitlist expiry error: %s", exc, exc_info=True)

        except Exception as exc:
            logger.error("[Reminders] Scheduler error: %s", exc, exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)
