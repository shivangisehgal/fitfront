"""
Support ticket service — CRUD operations for the help/support ticket system.

Includes:
  - Ticket create / list / get / update
  - Conversation thread (add message, list messages)
  - Reopen workflow (tenant can reopen a resolved ticket)
  - Admin stats dashboard
"""
from __future__ import annotations


import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.database import async_session
from backend.models.support_ticket import (
    SupportTicket, SupportTicketMessage,
    TicketStatus, MessageSender,
)
from backend.models.tenant import Tenant

logger = logging.getLogger(__name__)


# ── Serialisation helpers ────────────────────────────────────────────────────

def message_to_dict(m: SupportTicketMessage) -> dict[str, Any]:
    """Serialize a SupportTicketMessage to a JSON-safe dict."""
    return {
        "id": str(m.id),
        "ticket_id": str(m.ticket_id),
        "sender_type": m.sender_type,
        "sender_name": m.sender_name,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def ticket_to_dict(
    t: SupportTicket,
    include_tenant: bool = False,
    include_messages: bool = False,
) -> dict[str, Any]:
    """Serialize a SupportTicket to a JSON-safe dict."""
    d = {
        "id": str(t.id),
        "tenant_id": str(t.tenant_id),
        "subject": t.subject,
        "body": t.body,
        "category": t.category,
        "status": t.status,
        "priority": t.priority,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "admin_notes": t.admin_notes,
        "resolved_by": t.resolved_by,
        "message_count": len(t.messages) if t.messages else 0,
    }
    if include_tenant and t.tenant:
        d["tenant_name"] = t.tenant.business_name
        d["tenant_slug"] = t.tenant.slug
        d["tenant_email"] = t.tenant.owner_email
    if include_messages and t.messages:
        d["messages"] = [message_to_dict(m) for m in t.messages]
    return d


# ── Create ───────────────────────────────────────────────────────────────────

async def create_ticket(
    tenant_id: uuid.UUID,
    subject: str,
    body: str,
    category: str = "GENERAL",
    priority: str = "MEDIUM",
) -> dict[str, Any]:
    """Create a new support ticket for a tenant."""
    async with async_session() as session:
        ticket = SupportTicket(
            tenant_id=tenant_id,
            subject=subject,
            body=body,
            category=category,
            priority=priority,
            status=TicketStatus.OPEN.value,
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        logger.info("[SupportTicket] Created ticket %s for tenant %s: %s",
                    ticket.id, tenant_id, subject[:60])
        return ticket_to_dict(ticket)


# ── List ─────────────────────────────────────────────────────────────────────

async def list_tickets(
    tenant_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """
    List tickets. If tenant_id is provided, scopes to that tenant.
    If None, returns all (admin view).
    """
    async with async_session() as session:
        query = select(SupportTicket).order_by(SupportTicket.created_at.desc())
        count_query = select(func.count(SupportTicket.id))

        if tenant_id:
            query = query.where(SupportTicket.tenant_id == tenant_id)
            count_query = count_query.where(SupportTicket.tenant_id == tenant_id)
        if status_filter:
            query = query.where(SupportTicket.status == status_filter)
            count_query = count_query.where(SupportTicket.status == status_filter)

        total = (await session.execute(count_query)).scalar() or 0

        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)

        # Eagerly load tenant for admin views + always load messages for count
        load_opts = [selectinload(SupportTicket.messages)]
        if not tenant_id:
            load_opts.append(selectinload(SupportTicket.tenant))
        query = query.options(*load_opts)

        result = await session.execute(query)
        tickets = result.scalars().all()

        return {
            "tickets": [ticket_to_dict(t, include_tenant=not tenant_id) for t in tickets],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 0,
        }


# ── Get single ───────────────────────────────────────────────────────────────

async def get_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """
    Get a single ticket by ID, including full conversation thread.
    If tenant_id is provided, enforces ownership (tenant can only see own tickets).
    """
    async with async_session() as session:
        query = select(SupportTicket).where(SupportTicket.id == ticket_id)
        if tenant_id:
            query = query.where(SupportTicket.tenant_id == tenant_id)

        query = query.options(
            selectinload(SupportTicket.tenant),
            selectinload(SupportTicket.messages),
        )

        result = await session.execute(query)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None
        return ticket_to_dict(ticket, include_tenant=True, include_messages=True)


# ── Update ───────────────────────────────────────────────────────────────────

async def update_ticket(
    ticket_id: uuid.UUID,
    data: dict[str, Any],
    changed_by: str = "admin",
) -> dict[str, Any] | None:
    """Update a ticket (admin only). Handles status transitions, resolved_at, and audit trail."""
    async with async_session() as session:
        query = select(SupportTicket).where(SupportTicket.id == ticket_id)
        query = query.options(selectinload(SupportTicket.messages))
        result = await session.execute(query)
        ticket = result.scalar_one_or_none()
        if not ticket:
            return None

        old_status = ticket.status

        allowed_fields = {"status", "admin_notes", "resolved_by", "priority"}
        for field, value in data.items():
            if field in allowed_fields and value is not None:
                setattr(ticket, field, value)

        # Auto-set resolved_at when status changes to RESOLVED or CLOSED
        if data.get("status") in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
            ticket.resolved_at = datetime.now(timezone.utc)
        elif data.get("status") in (
            TicketStatus.OPEN.value,
            TicketStatus.IN_PROGRESS.value,
            TicketStatus.REOPENED.value,
        ):
            ticket.resolved_at = None  # Re-opened

        # Record SYSTEM audit message whenever status changes
        new_status = data.get("status")
        if new_status and new_status != old_status:
            audit_msg = SupportTicketMessage(
                ticket_id=ticket_id,
                sender_type=MessageSender.SYSTEM.value,
                sender_name="system",
                body=f"Status changed from {old_status} to {new_status} by {changed_by}",
            )
            session.add(audit_msg)

        ticket.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(ticket)
        logger.info("[SupportTicket] Updated ticket %s: %s", ticket_id, list(data.keys()))
        return ticket_to_dict(ticket, include_messages=True)


# ── Reopen (tenant action) ──────────────────────────────────────────────────

async def reopen_ticket(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID,
    reason: str,
    tenant_name: str = "Tenant",
) -> dict[str, Any] | None:
    """
    Reopen a resolved/closed ticket.

    The tenant provides a reason (e.g. "resolution didn't work").
    This sets the status to REOPENED and adds a message to the thread.
    """
    async with async_session() as session:
        query = (
            select(SupportTicket)
            .where(SupportTicket.id == ticket_id, SupportTicket.tenant_id == tenant_id)
            .options(selectinload(SupportTicket.messages))
        )
        result = await session.execute(query)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None

        # Only resolved/closed tickets can be reopened
        if ticket.status not in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
            return {"error": "only_resolved_can_reopen"}

        old_status = ticket.status
        ticket.status = TicketStatus.REOPENED.value
        ticket.resolved_at = None
        ticket.updated_at = datetime.now(timezone.utc)

        # SYSTEM audit entry for the status change
        audit_msg = SupportTicketMessage(
            ticket_id=ticket_id,
            sender_type=MessageSender.SYSTEM.value,
            sender_name="system",
            body=f"Status changed from {old_status} to REOPENED by tenant ({tenant_name})",
        )
        session.add(audit_msg)

        # Tenant's reason message
        msg = SupportTicketMessage(
            ticket_id=ticket_id,
            sender_type=MessageSender.TENANT.value,
            sender_name=tenant_name,
            body=reason,
        )
        session.add(msg)

        await session.commit()
        await session.refresh(ticket)
        logger.info("[SupportTicket] Ticket %s reopened by tenant %s", ticket_id, tenant_id)
        return ticket_to_dict(ticket, include_messages=True)


# ── Messages ─────────────────────────────────────────────────────────────────

async def add_message(
    ticket_id: uuid.UUID,
    sender_type: str,
    sender_name: str,
    body: str,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """
    Add a message to a ticket's conversation thread.

    Args:
        ticket_id: The ticket to add the message to.
        sender_type: "TENANT" or "ADMIN".
        sender_name: Display name (business name or admin email).
        body: The message text.
        tenant_id: If provided, enforces ownership (tenant can only
            message their own tickets).

    Returns:
        The full ticket dict with updated messages, or None if not found.
    """
    async with async_session() as session:
        # Verify ticket exists (and belongs to tenant if scoped)
        query = select(SupportTicket).where(SupportTicket.id == ticket_id)
        if tenant_id:
            query = query.where(SupportTicket.tenant_id == tenant_id)
        query = query.options(selectinload(SupportTicket.messages))

        result = await session.execute(query)
        ticket = result.scalar_one_or_none()
        if not ticket:
            return None

        msg = SupportTicketMessage(
            ticket_id=ticket_id,
            sender_type=sender_type,
            sender_name=sender_name,
            body=body.strip(),
        )
        session.add(msg)

        # If admin replies and ticket was OPEN, auto-move to IN_PROGRESS
        if sender_type == MessageSender.ADMIN.value and ticket.status == TicketStatus.OPEN.value:
            ticket.status = TicketStatus.IN_PROGRESS.value
            auto_msg = SupportTicketMessage(
                ticket_id=ticket_id,
                sender_type=MessageSender.SYSTEM.value,
                sender_name="system",
                body="Status automatically changed from OPEN to IN_PROGRESS (admin replied)",
            )
            session.add(auto_msg)

        # If tenant replies and ticket was RESOLVED, auto-reopen
        if sender_type == MessageSender.TENANT.value and ticket.status == TicketStatus.RESOLVED.value:
            ticket.status = TicketStatus.REOPENED.value
            ticket.resolved_at = None
            auto_msg = SupportTicketMessage(
                ticket_id=ticket_id,
                sender_type=MessageSender.SYSTEM.value,
                sender_name="system",
                body="Status automatically changed from RESOLVED to REOPENED (tenant replied)",
            )
            session.add(auto_msg)

        ticket.updated_at = datetime.now(timezone.utc)
        await session.commit()

        # Re-fetch to get the full messages list including the new one
        await session.refresh(ticket)
        result2 = await session.execute(
            select(SupportTicket)
            .where(SupportTicket.id == ticket_id)
            .options(selectinload(SupportTicket.messages))
        )
        ticket = result2.scalar_one()

        logger.info("[SupportTicket] %s message added to ticket %s by %s",
                    sender_type, ticket_id, sender_name)
        return ticket_to_dict(ticket, include_messages=True)


async def get_messages(
    ticket_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
) -> list[dict[str, Any]] | None:
    """
    Get all messages for a ticket, ordered chronologically.

    Returns None if ticket not found (or doesn't belong to tenant).
    """
    async with async_session() as session:
        # Verify access
        query = select(SupportTicket).where(SupportTicket.id == ticket_id)
        if tenant_id:
            query = query.where(SupportTicket.tenant_id == tenant_id)
        query = query.options(selectinload(SupportTicket.messages))

        result = await session.execute(query)
        ticket = result.scalar_one_or_none()
        if not ticket:
            return None

        return [message_to_dict(m) for m in (ticket.messages or [])]


# ── Stats ────────────────────────────────────────────────────────────────────

async def get_ticket_stats() -> dict[str, int]:
    """Get ticket count by status (admin dashboard)."""
    async with async_session() as session:
        stats = {}
        for status in TicketStatus:
            count_q = select(func.count(SupportTicket.id)).where(
                SupportTicket.status == status.value
            )
            stats[status.value] = (await session.execute(count_q)).scalar() or 0
        stats["total"] = sum(stats.values())
        return stats
