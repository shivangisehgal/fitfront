"""
Caller CRM API routes — list callers, get full caller profile with
appointment history, call logs, and SMS threads.

GET    /api/callers              -> list all callers for the tenant
GET    /api/callers/{caller_id}  -> full caller profile with history
PUT    /api/callers/{caller_id}  -> update caller notes/etc.
DELETE /api/callers/{caller_id}  -> delete caller + related data
POST   /api/callers/bulk-delete  -> delete multiple callers + related data
"""
from __future__ import annotations


import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.tenant import Tenant
from backend.models.caller import Caller
from backend.models.appointment import Appointment, AppointmentStatus
from backend.models.call import Call
from backend.models.sms_message import SMSMessage, SMSDirection
from backend.models.provider import Trainer
from backend.models.waitlist import WaitlistEntry
from backend.models.status_history import AppointmentStatusHistory
from backend.services import auth_service
from backend.services.caller_service import _phone_digits_tail, _phone_col_clean
from backend.defaults import slugify_appointment_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/callers", tags=["Callers"])


# -- Schemas -------------------------------------------------------------------


class CallerUpdateRequest(BaseModel):
    """Partial update for editable caller fields."""
    name: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[str] = None
    notes: Optional[str] = None
    preferred_appointment_type: Optional[str] = None


class BulkDeleteRequest(BaseModel):
    """Request body for bulk caller deletion."""
    caller_ids: List[str]


# -- Routes --------------------------------------------------------------------


@router.get("")
async def list_callers(
    search: Optional[str] = Query(None, description="Search by name or phone"),
    sort: str = Query("recent", description="Sort: recent, name, visits"),
    include_test: bool = Query(False, description="Include test/demo caller data"),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    List all callers for the authenticated tenant with summary stats.
    Returns: id, name, phone, email, visit_count, is_new_caller,
    last_appointment_at, upcoming_count.
    """
    logger.info("[Callers] Listing callers for tenant=%s search=%s include_test=%s", current_user.slug, search, include_test)
    async with async_session() as session:
        # Base query
        filters = [Caller.tenant_id == current_user.id]

        # When include_test=False → real data only; include_test=True → test data only
        if not include_test:
            filters.append(Caller.is_test == False)  # noqa: E712
        else:
            filters.append(Caller.is_test == True)  # noqa: E712

        if search:
            search_term = f"%{search.strip()}%"
            filters.append(
                (Caller.name.ilike(search_term)) | (Caller.phone.ilike(search_term))
            )

        stmt = select(Caller).where(and_(*filters))

        # Sorting
        if sort == "name":
            stmt = stmt.order_by(Caller.name.asc())
        elif sort == "visits":
            stmt = stmt.order_by(desc(Caller.visit_count))
        else:  # "recent" — most recently seen first
            stmt = stmt.order_by(desc(Caller.last_appointment_at))

        result = await session.execute(stmt)
        callers = result.scalars().all()

        # For each caller, count upcoming sessions
        caller_list = []
        for p in callers:
            # Count upcoming confirmed appointments (suffix match handles
            # phone format differences like +6352418405 vs +16352418405)
            phone_tail = _phone_digits_tail(p.phone)
            upcoming_count_stmt = (
                select(func.count(Appointment.id))
                .select_from(Appointment)
                .join(Caller, Appointment.caller_id == Caller.id)
                .where(
                    and_(
                        Caller.tenant_id == current_user.id,
                        _phone_col_clean(Appointment.client_phone).endswith(phone_tail),
                        Appointment.status == AppointmentStatus.CONFIRMED,
                        Appointment.scheduled_at > func.now(),
                    )
                )
            )
            upcoming_result = await session.execute(upcoming_count_stmt)
            upcoming_count = upcoming_result.scalar() or 0

            caller_list.append({
                "id": str(p.id),
                "name": p.name,
                "phone": p.phone,
                "email": p.email,
                "date_of_birth": p.date_of_birth,
                "is_new_caller": p.is_new_caller,
                "visit_count": p.visit_count or 0,
                "no_show_count": p.no_show_count or 0,
                "last_appointment_at": p.last_appointment_at.isoformat() if p.last_appointment_at else None,
                "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
                "upcoming_count": upcoming_count,
                "preferred_appointment_type": p.preferred_appointment_type,
                "is_test": p.is_test or False,
                "extra_data": p.extra_data or {},
            })

        return caller_list


@router.get("/{caller_id}")
async def get_caller_profile(
    caller_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Full caller profile: demographics, all appointments, call logs, SMS threads.
    This is the data backing the CRM caller detail view.
    """
    logger.info("[Callers] Profile request for %s by tenant=%s", caller_id, current_user.slug)
    async with async_session() as session:
        # Get caller record
        result = await session.execute(
            select(Caller).where(
                and_(Caller.id == caller_id, Caller.tenant_id == current_user.id)
            )
        )
        caller_rec = result.scalar_one_or_none()
        if not caller_rec:
            raise HTTPException(status_code=404, detail="Caller not found.")

        # ── Appointments (all, most recent first) ────────────────────────
        # Use suffix matching to handle phone format differences
        phone_tail = _phone_digits_tail(caller_rec.phone)
        appts_result = await session.execute(
            select(Appointment)
            .join(Caller, Appointment.caller_id == Caller.id)
            .where(
                and_(
                    Caller.tenant_id == current_user.id,
                    _phone_col_clean(Appointment.client_phone).endswith(phone_tail),
                )
            )
            .order_by(desc(Appointment.scheduled_at))
            .limit(50)
        )
        appointments = appts_result.scalars().all()

        # ── Provider name map ───────────────────────────────────────────
        provider_ids = {a.provider_id for a in appointments if a.provider_id}
        provider_map: dict = {}
        if provider_ids:
            prov_result = await session.execute(
                select(Trainer).where(Trainer.id.in_(provider_ids))
            )
            for prov in prov_result.scalars().all():
                provider_map[prov.id] = prov.name

        # ── Call logs (matched by caller_number) ─────────────────────────
        # Match on last 10 digits to handle +1 prefix differences
        phone_digits = caller_rec.phone.replace("+", "").replace("-", "").replace(" ", "")
        phone_tail = phone_digits[-10:] if len(phone_digits) >= 10 else phone_digits

        calls_result = await session.execute(
            select(Call)
            .where(
                and_(
                    Call.tenant_id == current_user.id,
                    Call.caller_number.ilike(f"%{phone_tail}"),
                )
            )
            .order_by(desc(Call.started_at))
            .limit(30)
        )
        calls = calls_result.scalars().all()

        # ── SMS messages ─────────────────────────────────────────────────
        sms_result = await session.execute(
            select(SMSMessage)
            .where(SMSMessage.caller_id == caller_rec.id)
            .order_by(desc(SMSMessage.created_at))
            .limit(100)
        )
        sms_messages = sms_result.scalars().all()

        # ── Status history for all appointments ─────────────────────────
        appt_ids = [a.id for a in appointments]
        history_map: dict[str, list] = {str(aid): [] for aid in appt_ids}
        if appt_ids:
            history_result = await session.execute(
                select(AppointmentStatusHistory)
                .where(AppointmentStatusHistory.appointment_id.in_(appt_ids))
                .order_by(AppointmentStatusHistory.created_at.asc())
            )
            for h in history_result.scalars().all():
                history_map.setdefault(str(h.appointment_id), []).append(h)

    # Build slug → display name map from tenant config
    type_display_map: dict[str, str] = {}
    tenant_types = current_user.appointment_types or []
    for at in tenant_types:
        code = slugify_appointment_type(at.get("code", ""))
        name = at.get("name", "")
        if code and name:
            type_display_map[code] = name

    # ── Assemble response ────────────────────────────────────────────────
    return {
        "caller": {
            "id": str(caller_rec.id),
            "name": caller_rec.name,
            "phone": caller_rec.phone,
            "email": caller_rec.email,
            "date_of_birth": caller_rec.date_of_birth,
            "preferred_appointment_type": caller_rec.preferred_appointment_type,
            "notes": caller_rec.notes,
            "is_new_caller": caller_rec.is_new_caller,
            "visit_count": caller_rec.visit_count or 0,
            "no_show_count": caller_rec.no_show_count or 0,
            "first_seen_at": caller_rec.first_seen_at.isoformat() if caller_rec.first_seen_at else None,
            "last_appointment_at": caller_rec.last_appointment_at.isoformat() if caller_rec.last_appointment_at else None,
            "extra_data": caller_rec.extra_data or {},
        },
        "appointments": [
            {
                "id": str(a.id),
                "appointment_type": a.appointment_type,
                "appointment_type_display": type_display_map.get(
                    slugify_appointment_type(a.appointment_type or ""),
                    a.appointment_type,
                ),
                "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
                "duration_minutes": a.duration_minutes,
                "status": a.status.value if a.status else None,
                "booked_via": a.booked_via.value if a.booked_via else None,
                "provider_id": str(a.provider_id) if a.provider_id else None,
                "provider_name": provider_map.get(a.provider_id) if a.provider_id else None,
                "confirmed_by_client": a.confirmed_by_client,
                "reminder_sent_at": a.reminder_sent_at.isoformat() if a.reminder_sent_at else None,
                "notes": a.notes,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "status_history": [
                    {
                        "old_status": h.old_status,
                        "new_status": h.new_status,
                        "changed_by": h.changed_by,
                        "note": h.note,
                        "created_at": h.created_at.isoformat() if h.created_at else None,
                    }
                    for h in history_map.get(str(a.id), [])
                ],
            }
            for a in appointments
        ],
        "calls": [
            {
                "id": str(c.id),
                "vapi_call_id": c.vapi_call_id,
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "duration_seconds": c.duration_seconds,
                "outcome": c.outcome.value if c.outcome else None,
                "summary": c.summary,
                "transcript": c.transcript or [],
            }
            for c in calls
        ],
        "sms_messages": [
            {
                "id": str(m.id),
                "direction": m.direction.value,
                "body": m.body,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sms_messages
        ],
    }


@router.put("/{caller_id}")
async def update_caller(
    caller_id: str,
    req: CallerUpdateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Update editable caller fields (notes, etc.)."""
    logger.info("[Callers] Updating caller %s for tenant=%s", caller_id, current_user.slug)
    async with async_session() as session:
        result = await session.execute(
            select(Caller).where(
                and_(Caller.id == caller_id, Caller.tenant_id == current_user.id)
            )
        )
        caller_rec = result.scalar_one_or_none()
        if not caller_rec:
            raise HTTPException(status_code=404, detail="Caller not found.")

        update_data = {k: v for k, v in req.model_dump().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update.")

        for key, value in update_data.items():
            setattr(caller_rec, key, value)

        await session.commit()
        return {"status": "updated", "caller_id": caller_id, "updated_fields": list(update_data.keys())}


@router.post("/bulk-delete")
async def bulk_delete_callers(
    req: BulkDeleteRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Delete multiple callers and all their related data (appointments,
    waitlist entries, SMS messages). Matches related data by phone number.
    Requires explicit caller IDs list.
    """
    if not req.caller_ids:
        raise HTTPException(status_code=400, detail="No caller IDs provided.")
    if len(req.caller_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 callers per bulk delete.")

    logger.warning(
        "[Callers] Bulk delete requested for %d callers by tenant=%s",
        len(req.caller_ids), current_user.slug,
    )

    async with async_session() as session:
        # Look up all requested callers (scoped to tenant)
        result = await session.execute(
            select(Caller).where(
                and_(
                    Caller.tenant_id == current_user.id,
                    Caller.id.in_(req.caller_ids),
                )
            )
        )
        callers = result.scalars().all()

        if not callers:
            raise HTTPException(status_code=404, detail="No matching callers found.")

        # Cascade delete related data by phone number
        totals = {"callers": 0, "appointments": 0, "waitlist_entries": 0, "sms_messages": 0}
        deleted_names = []

        for caller_rec in callers:
            counts = await _cascade_delete_caller(session, caller_rec, current_user.id)
            totals["callers"] += 1
            totals["appointments"] += counts["appointments"]
            totals["waitlist_entries"] += counts["waitlist_entries"]
            totals["sms_messages"] += counts["sms_messages"]
            deleted_names.append(caller_rec.name or caller_rec.phone)

        await session.commit()

    total = sum(totals.values())
    logger.warning(
        "[Callers] Bulk deleted %d callers (%s) + %d appts + %d waitlist + %d sms for tenant=%s",
        totals["callers"], ", ".join(deleted_names),
        totals["appointments"], totals["waitlist_entries"], totals["sms_messages"],
        current_user.slug,
    )
    return {
        "status": "deleted",
        "deleted": totals,
        "total": total,
        "deleted_names": deleted_names,
    }


@router.delete("/test-data")
async def clear_test_data(
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Delete all test/demo data (callers, appointments, waitlist entries, SMS)
    for the authenticated tenant. Only removes records where is_test=True.
    """
    logger.info("[Callers] Clearing test data for tenant=%s", current_user.slug)
    async with async_session() as session:
        # Subquery: IDs of test callers for this tenant
        test_caller_ids_q = (
            select(Caller.id).where(
                and_(Caller.tenant_id == current_user.id, Caller.is_test == True)  # noqa: E712
            )
        )

        # Delete child records first (FK on caller_id — no CASCADE defined)
        sms_count = (await session.execute(
            delete(SMSMessage).where(SMSMessage.caller_id.in_(test_caller_ids_q))
        )).rowcount

        waitlist_count = (await session.execute(
            delete(WaitlistEntry).where(WaitlistEntry.caller_id.in_(test_caller_ids_q))
        )).rowcount

        appt_count = (await session.execute(
            delete(Appointment).where(Appointment.caller_id.in_(test_caller_ids_q))
        )).rowcount

        caller_count = (await session.execute(
            delete(Caller).where(
                and_(Caller.tenant_id == current_user.id, Caller.is_test == True)  # noqa: E712
            )
        )).rowcount

        await session.commit()

    total = caller_count + appt_count + waitlist_count + sms_count
    logger.info(
        "[Callers] Cleared test data: %d callers, %d appointments, %d waitlist, %d sms",
        caller_count, appt_count, waitlist_count, sms_count,
    )
    return {
        "status": "cleared",
        "deleted": {
            "callers": caller_count,
            "appointments": appt_count,
            "waitlist_entries": waitlist_count,
            "sms_messages": sms_count,
        },
        "total": total,
    }


@router.delete("/{caller_id}")
async def delete_caller(
    caller_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Delete a single caller and all related data (appointments, waitlist
    entries, SMS messages). Matches related data by phone number.
    """
    logger.warning("[Callers] Delete requested for caller %s by tenant=%s", caller_id, current_user.slug)

    async with async_session() as session:
        result = await session.execute(
            select(Caller).where(
                and_(Caller.id == caller_id, Caller.tenant_id == current_user.id)
            )
        )
        caller_rec = result.scalar_one_or_none()
        if not caller_rec:
            raise HTTPException(status_code=404, detail="Caller not found.")

        counts = await _cascade_delete_caller(session, caller_rec, current_user.id)
        caller_name = caller_rec.name or caller_rec.phone
        await session.commit()

    logger.warning(
        "[Callers] Deleted caller '%s' + %d appts + %d waitlist + %d sms for tenant=%s",
        caller_name, counts["appointments"], counts["waitlist_entries"],
        counts["sms_messages"], current_user.slug,
    )
    return {
        "status": "deleted",
        "caller_name": caller_name,
        "deleted": {
            "callers": 1,
            **counts,
        },
        "total": 1 + sum(counts.values()),
    }


# -- Helpers -------------------------------------------------------------------


async def _cascade_delete_caller(
    session: AsyncSession, caller_rec: Caller, tenant_id
) -> dict:
    """
    Delete a caller and all related records (SMS, waitlist, appointments)
    by caller_id FK. Returns counts of deleted related records.
    Must be called within an active session — caller is responsible for commit.
    """
    # Delete child records by caller_id (direct FK — no phone matching needed)
    sms_count = (await session.execute(
        delete(SMSMessage).where(SMSMessage.caller_id == caller_rec.id)
    )).rowcount

    waitlist_count = (await session.execute(
        delete(WaitlistEntry).where(WaitlistEntry.caller_id == caller_rec.id)
    )).rowcount

    appt_count = (await session.execute(
        delete(Appointment).where(Appointment.caller_id == caller_rec.id)
    )).rowcount

    # Delete the caller record itself
    await session.execute(
        delete(Caller).where(Caller.id == caller_rec.id)
    )

    return {
        "appointments": appt_count,
        "waitlist_entries": waitlist_count,
        "sms_messages": sms_count,
    }
