"""
Support ticket model — generic help/support ticket system.

Tenants submit tickets for setup help, billing, bugs, feature requests, etc.
Platform admins view, respond to, and resolve tickets.

Each ticket has a conversation thread (SupportTicketMessage) allowing
back-and-forth between tenant and admin.  Tenants can reopen resolved
tickets if unsatisfied with the resolution.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class TicketCategory(str, enum.Enum):
    GENERAL = "GENERAL"
    BILLING = "BILLING"
    TECHNICAL = "TECHNICAL"
    FEATURE_REQUEST = "FEATURE_REQUEST"
    VOICE_SETUP = "VOICE_SETUP"
    OTHER = "OTHER"


class TicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class TicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class MessageSender(str, enum.Enum):
    """Who sent the message — the tenant (client), a platform admin, or the system (audit trail)."""
    TENANT = "TENANT"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Ticket content
    subject = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(20), nullable=False, default=TicketCategory.GENERAL.value)
    status = Column(String(20), nullable=False, default=TicketStatus.OPEN.value)
    priority = Column(String(10), nullable=False, default=TicketPriority.MEDIUM.value)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Admin response (legacy single-note — kept for backward compat)
    admin_notes = Column(Text, nullable=True)
    resolved_by = Column(String(255), nullable=True)

    # Relationships
    tenant = relationship("Tenant", backref="support_tickets")
    messages = relationship(
        "SupportTicketMessage",
        back_populates="ticket",
        order_by="SupportTicketMessage.created_at.asc()",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Composite index for filtered queries
    __table_args__ = (
        Index("ix_support_tickets_tenant_status", "tenant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<SupportTicket {self.id} [{self.status}] {self.subject[:40]}>"


class SupportTicketMessage(Base):
    """
    A single message in a ticket's conversation thread.

    Both tenants and admins can post messages.  Messages are ordered by
    created_at ascending to form a chronological chat history.
    """
    __tablename__ = "support_ticket_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Who sent this message
    sender_type = Column(String(10), nullable=False)  # TENANT or ADMIN
    sender_name = Column(String(255), nullable=False)  # display name / email

    # Message content
    body = Column(Text, nullable=False)

    # Timestamp (stored UTC, rendered in tenant's timezone on the client)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    ticket = relationship("SupportTicket", back_populates="messages")

    __table_args__ = (
        Index("ix_ticket_messages_ticket_created", "ticket_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<TicketMessage {self.id} [{self.sender_type}] {self.body[:40]}>"
