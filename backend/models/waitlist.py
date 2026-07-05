"""
Waitlist model — tracks clients waiting for a spot in a full class.

When the AI agent finds no available slots for a requested date/type, it offers
to add the caller to the waitlist. When a cancellation creates an opening, the
system auto-notifies the top waitlisted caller via SMS.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, case, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from backend.database import Base


class WaitlistStatus(str, enum.Enum):
    WAITING = "WAITING"
    NOTIFIED = "NOTIFIED"
    BOOKED = "BOOKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id = Column(UUID(as_uuid=True), ForeignKey("callers.id"), nullable=False, index=True)

    client_name = Column(String(255), nullable=False)
    client_phone = Column(String(20), nullable=False, index=True)
    client_email = Column(String(255), nullable=True)

    appointment_type = Column(String(100), nullable=False)
    preferred_date = Column(String(10), nullable=False)
    preferred_time_start = Column(String(5), nullable=True)
    preferred_time_end = Column(String(5), nullable=True)

    provider_id = Column(UUID(as_uuid=True), ForeignKey("trainers.id"), nullable=True)

    priority = Column(Integer, nullable=False, default=100)

    @hybrid_property
    def is_test(self) -> bool:
        return self.caller.is_test if self.caller else False

    @is_test.inplace.expression
    @classmethod
    def _is_test_expr(cls):
        from backend.models.caller import Caller
        return case(
            (cls.caller_id.is_(None), False),
            else_=(
                select(Caller.is_test)
                .where(Caller.id == cls.caller_id)
                .correlate(cls)
                .scalar_subquery()
            ),
        )

    status = Column(Enum(WaitlistStatus), nullable=False, default=WaitlistStatus.WAITING)
    notified_at = Column(DateTime(timezone=True), nullable=True)
    booked_at = Column(DateTime(timezone=True), nullable=True)
    appointment_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @property
    def tenant_id(self):
        return self.caller.tenant_id if self.caller else None

    caller = relationship("Caller", lazy="selectin", foreign_keys=[caller_id])
    provider = relationship("Trainer", lazy="selectin")

    def __repr__(self) -> str:
        return f"<WaitlistEntry {self.client_name} waiting for {self.appointment_type} on {self.preferred_date}>"
