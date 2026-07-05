"""
Support ticket routes — tenant self-service and admin management.

Tenant endpoints (scoped to own tenant):
  POST  /api/support/tickets               → create a new ticket
  GET   /api/support/tickets               → list own tickets
  GET   /api/support/tickets/{id}          → get single ticket (with messages)
  POST  /api/support/tickets/{id}/messages → post a message
  POST  /api/support/tickets/{id}/reopen   → reopen a resolved ticket

Admin endpoints:
  GET   /api/admin/support/tickets         → list ALL tickets (filterable)
  GET   /api/admin/support/tickets/stats   → ticket counts by status
  GET   /api/admin/support/tickets/{id}    → get any ticket (with messages)
  PATCH /api/admin/support/tickets/{id}    → update status, priority, etc.
  POST  /api/admin/support/tickets/{id}/messages → admin reply message
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.models.tenant import Tenant
from backend.services import auth_service
from backend.services import support_ticket_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Support"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class TicketCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)
    category: str = Field(default="GENERAL")
    priority: str = Field(default="MEDIUM")


class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    priority: Optional[str] = None


class MessageRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class ReopenRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


# ── Tenant endpoints ───────────────────────────────────────────────────────


@router.post("/api/support/tickets", status_code=201)
async def create_ticket(
    req: TicketCreateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Create a new support ticket."""
    ticket = await support_ticket_service.create_ticket(
        tenant_id=current_user.id,
        subject=req.subject,
        body=req.body,
        category=req.category,
        priority=req.priority,
    )
    logger.info("[Support] Ticket created by %s: %s", current_user.slug, req.subject[:60])
    return ticket


@router.get("/api/support/tickets")
async def list_own_tickets(
    status: Optional[str] = Query(None, description="Filter by status: OPEN, IN_PROGRESS, RESOLVED, CLOSED, REOPENED"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List the current tenant's support tickets."""
    return await support_ticket_service.list_tickets(
        tenant_id=current_user.id,
        status_filter=status,
        page=page,
        per_page=per_page,
    )


@router.get("/api/support/tickets/{ticket_id}")
async def get_own_ticket(
    ticket_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Get a single ticket with full conversation thread (scoped to own tenant)."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    ticket = await support_ticket_service.get_ticket(tid, tenant_id=current_user.id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return ticket


@router.post("/api/support/tickets/{ticket_id}/messages")
async def post_tenant_message(
    ticket_id: str,
    req: MessageRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Post a message on a ticket as the tenant."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    result = await support_ticket_service.add_message(
        ticket_id=tid,
        sender_type="TENANT",
        sender_name=current_user.business_name or current_user.slug,
        body=req.body,
        tenant_id=current_user.id,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    logger.info("[Support] Tenant %s posted message on ticket %s", current_user.slug, ticket_id)
    return result


@router.post("/api/support/tickets/{ticket_id}/reopen")
async def reopen_ticket(
    ticket_id: str,
    req: ReopenRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Reopen a resolved/closed ticket with a reason."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    result = await support_ticket_service.reopen_ticket(
        ticket_id=tid,
        tenant_id=current_user.id,
        reason=req.reason,
        tenant_name=current_user.business_name or current_user.slug,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    if isinstance(result, dict) and result.get("error") == "only_resolved_can_reopen":
        raise HTTPException(status_code=400, detail="Only resolved or closed tickets can be reopened.")

    logger.info("[Support] Tenant %s reopened ticket %s", current_user.slug, ticket_id)
    return result


# ── Admin endpoints ────────────────────────────────────────────────────────


@router.get("/api/admin/support/tickets")
async def admin_list_tickets(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    admin: Tenant = Depends(auth_service.require_admin),
):
    """List ALL support tickets across tenants (admin only)."""
    return await support_ticket_service.list_tickets(
        tenant_id=None,
        status_filter=status,
        page=page,
        per_page=per_page,
    )


@router.get("/api/admin/support/tickets/stats")
async def admin_ticket_stats(
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Get ticket count by status (admin dashboard)."""
    return await support_ticket_service.get_ticket_stats()


@router.get("/api/admin/support/tickets/{ticket_id}")
async def admin_get_ticket(
    ticket_id: str,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Get any ticket by ID with full conversation thread (admin only)."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    ticket = await support_ticket_service.get_ticket(tid)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return ticket


@router.patch("/api/admin/support/tickets/{ticket_id}")
async def admin_update_ticket(
    ticket_id: str,
    req: TicketUpdateRequest,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Update a ticket — change status, add admin notes, resolve. (Admin only)"""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    # Auto-fill resolved_by with admin's name/email if resolving
    if req.status in ("RESOLVED", "CLOSED") and not req.resolved_by:
        update_data["resolved_by"] = admin.owner_email

    ticket = await support_ticket_service.update_ticket(
        tid, update_data, changed_by=admin.owner_email or admin.slug
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    logger.info("[Support] Admin %s updated ticket %s: %s", admin.slug, ticket_id, list(update_data.keys()))
    return ticket


@router.post("/api/admin/support/tickets/{ticket_id}/messages")
async def admin_post_message(
    ticket_id: str,
    req: MessageRequest,
    admin: Tenant = Depends(auth_service.require_admin),
):
    """Post a reply message on any ticket as an admin."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID format.")

    result = await support_ticket_service.add_message(
        ticket_id=tid,
        sender_type="ADMIN",
        sender_name=admin.owner_email or admin.slug,
        body=req.body,
        tenant_id=None,  # admin can message any ticket
    )
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    logger.info("[Support] Admin %s replied to ticket %s", admin.slug, ticket_id)
    return result
