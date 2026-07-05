"""
Call log API routes — paginated listing and detail views.

GET  /api/calls          → paginated call list
GET  /api/calls/{id}     → single call with full transcript
GET  /api/calls/export   → CSV export
"""
from __future__ import annotations


import csv
import io
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.call import Call, CallOutcome
from backend.models.tenant import Tenant
from backend.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["Calls"])


# ── Response schemas ──────────────────────────────────────────────────────────


class CallSummary(BaseModel):
    id: str
    vapi_call_id: str
    caller_number: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]
    outcome: Optional[str]
    summary: Optional[str]

    class Config:
        from_attributes = True


class CallDetail(CallSummary):
    transcript: Optional[list]

    class Config:
        from_attributes = True


class PaginatedCalls(BaseModel):
    items: list[CallSummary]
    total: int
    page: int
    page_size: int
    pages: int


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("", response_model=PaginatedCalls)
async def list_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    outcome: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tenant_id: Optional[str] = Query(None, description="Filter by tenant UUID (admin-only)"),
    db: AsyncSession = Depends(get_db),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List call logs with pagination and optional filters. Auto-scoped to current tenant."""
    logger.info("Listing calls for user=%s admin=%s page=%d outcome=%s",
                current_user.owner_email, current_user.is_admin, page, outcome)
    query = select(Call)

    # Tenant scoping: non-admins always see only their own tenant's calls
    if not current_user.is_admin:
        query = query.where(Call.tenant_id == current_user.id)
    elif tenant_id:
        # Admins can optionally filter to a specific tenant
        try:
            tid = uuid.UUID(tenant_id)
            query = query.where(Call.tenant_id == tid)
        except ValueError:
            pass

    # Filters
    if outcome:
        try:
            query = query.where(Call.outcome == CallOutcome(outcome.upper()))
        except ValueError:
            pass
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            query = query.where(Call.started_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.where(Call.started_at <= dt)
        except ValueError:
            pass

    # Total count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(desc(Call.started_at)).offset(offset).limit(page_size)
    result = await db.execute(query)
    calls = result.scalars().all()

    pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedCalls(
        items=[
            CallSummary(
                id=str(c.id),
                vapi_call_id=c.vapi_call_id,
                caller_number=c.caller_number,
                started_at=c.started_at,
                ended_at=c.ended_at,
                duration_seconds=c.duration_seconds,
                outcome=c.outcome.value if c.outcome else None,
                summary=c.summary,
            )
            for c in calls
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/export")
async def export_calls_csv(
    db: AsyncSession = Depends(get_db),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Export all call logs as CSV (scoped to current tenant)."""
    logger.info("Exporting calls to CSV for %s", current_user.owner_email)
    query = select(Call).order_by(desc(Call.started_at))
    if not current_user.is_admin:
        query = query.where(Call.tenant_id == current_user.id)
    result = await db.execute(query)
    calls = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Caller Number", "Started At", "Duration (s)", "Outcome", "Summary"])

    for c in calls:
        writer.writerow([
            str(c.id),
            c.caller_number,
            c.started_at.isoformat() if c.started_at else "",
            c.duration_seconds or "",
            c.outcome.value if c.outcome else "",
            c.summary or "",
        ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=call_logs.csv"},
    )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Get a single call with full transcript (scoped to current tenant)."""
    logger.info("Fetching call detail: %s", call_id)
    try:
        uid = uuid.UUID(call_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid call ID format.")

    result = await db.execute(select(Call).where(Call.id == uid))
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    if not current_user.is_admin and call.tenant_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    return CallDetail(
        id=str(call.id),
        vapi_call_id=call.vapi_call_id,
        caller_number=call.caller_number,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        outcome=call.outcome.value if call.outcome else None,
        summary=call.summary,
        transcript=call.transcript,
    )
