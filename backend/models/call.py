"""
Call log database model — stores every inbound voice call handled by the AI agent.

Multi-tenant: every call belongs to exactly one tenant via tenant_id FK.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class CallOutcome(str, enum.Enum):
    BOOKED = "BOOKED"
    ESCALATED = "ESCALATED"
    INQUIRY = "INQUIRY"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


class Call(Base):
    __tablename__ = "calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True)
    vapi_call_id = Column(String(255), unique=True, nullable=True, index=True)
    caller_number = Column(String(20), nullable=True)
    caller_id = Column(UUID(as_uuid=True), ForeignKey("callers.id"), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    outcome = Column(Enum(CallOutcome), nullable=True)
    # JSONB array of {role, content, timestamp} objects
    transcript = Column(JSONB, nullable=True, default=list)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", backref="calls", lazy="selectin")
    appointments = relationship("Appointment", back_populates="call", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Call {self.vapi_call_id} outcome={self.outcome}>"
