"""
Waitlist management API routes.

GET    /api/waitlist                       -> list waitlist entries for the tenant
DELETE /api/waitlist/{entry_id}            -> cancel a waitlist entry
POST   /api/waitlist/{entry_id}/promote    -> promote a waitlist entry to a booked appointment
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.models.tenant import Tenant
from backend.services import auth_service, tenant_service, waitlist_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/waitlist", tags=["Waitlist"])


# -- Request schemas -----------------------------------------------------------


class PromoteRequest(BaseModel):
    """Body for POST /api/waitlist/{entry_id}/promote."""
    scheduled_at: str = Field(
        ...,
        description="ISO datetime string for the appointment slot (e.g. '2026-05-28T10:30:00').",
        min_length=10,
    )
    force: bool = Field(
        False,
        description=(
            "If True, bypass the provider/time slot capacity check. Use only when "
            "the admin has confirmed the double-book in the UI (e.g. the faculty member "
            "has explicitly agreed to take an overlapping appointment)."
        ),
    )


class ConflictCheckRequest(BaseModel):
    """Body for POST /api/waitlist/{entry_id}/check-conflicts."""
    scheduled_at: str = Field(
        ...,
        description="ISO datetime string for the proposed appointment slot.",
        min_length=10,
    )


# -- Routes --------------------------------------------------------------------


@router.get("")
async def list_waitlist_entries(
    status: Optional[str] = Query(None, description="Filter by waitlist entry status"),
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    include_test: bool = Query(False, description="Include test/demo waitlist data"),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List waitlist entries for the authenticated tenant, with optional filters."""
    logger.info("[Waitlist] Listing entries for tenant=%s status=%s date=%s include_test=%s",
                current_user.slug, status, date, include_test)
    try:
        entries = await waitlist_service.get_waitlist_entries(
            current_user.id, status=status, date=date, include_test=include_test,
        )
        return entries
    except Exception as exc:
        logger.error("[Waitlist] Failed to list entries for tenant=%s: %s", current_user.slug, exc)
        raise HTTPException(status_code=500, detail="Failed to list waitlist entries.")


@router.delete("/{entry_id}")
async def cancel_waitlist_entry(
    entry_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Cancel a waitlist entry. The caller is notified by SMS."""
    logger.info("[Waitlist] Cancelling entry %s for tenant=%s", entry_id, current_user.slug)
    try:
        tenant_ctx = await tenant_service.resolve_by_id(current_user.id)
        result = await waitlist_service.cancel_waitlist_entry(entry_id, tenant_ctx=tenant_ctx)
        if not result:
            raise HTTPException(status_code=404, detail="Waitlist entry not found.")
        return {"status": "cancelled", "entry_id": entry_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Waitlist] Failed to cancel entry %s: %s", entry_id, exc)
        raise HTTPException(status_code=500, detail="Failed to cancel waitlist entry.")


@router.post("/{entry_id}/check-conflicts")
async def check_promote_conflicts(
    entry_id: str,
    req: ConflictCheckRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Inspect existing appointments that would conflict with promoting this
    waitlist entry into the requested slot. Used by the admin UI to surface
    a "these appointments already exist for this provider — still promote?"
    confirmation before actually booking.

    Returns:
        { "conflicts": [<appointment dicts>], "provider_id": "...", "scheduled_at": "..." }
    """
    logger.info(
        "[Waitlist] Conflict check for entry %s tenant=%s at %s",
        entry_id, current_user.slug, req.scheduled_at,
    )
    try:
        result = await waitlist_service.find_promote_conflicts(
            entry_id=entry_id,
            tenant_id=current_user.id,
            scheduled_at=req.scheduled_at,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[Waitlist] Conflict check failed for %s: %s", entry_id, exc)
        raise HTTPException(status_code=500, detail="Failed to check conflicts.")


@router.post("/{entry_id}/promote")
async def promote_waitlist_entry(
    entry_id: str,
    req: PromoteRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Promote a waitlist entry to a booked appointment.

    Books the slot via the tenant's calendar (Google or native), marks the
    entry BOOKED, and sends the caller an SMS confirming the booking.
    """
    logger.info(
        "[Waitlist] Promoting entry %s for tenant=%s at %s",
        entry_id, current_user.slug, req.scheduled_at,
    )
    try:
        tenant_ctx = await tenant_service.resolve_by_id(current_user.id)
        if not tenant_ctx:
            raise HTTPException(status_code=403, detail="Tenant context unavailable.")

        result = await waitlist_service.promote_to_appointment(
            entry_id=entry_id,
            scheduled_at=req.scheduled_at,
            tenant_ctx=tenant_ctx,
            force=req.force,
        )
        if not result:
            raise HTTPException(
                status_code=400,
                detail="Could not promote entry. It may not exist, may already be booked/cancelled, or the slot may not be available.",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Waitlist] Failed to promote entry %s: %s", entry_id, exc)
        raise HTTPException(status_code=500, detail="Failed to promote waitlist entry.")
