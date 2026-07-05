"""
Appointment database model — tracks every booking made via AI or manually.

Tenant scope is derived via caller_id → callers.tenant_id (no stored tenant_id
column). All SQL filtering by tenant uses an explicit JOIN to callers.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, case, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from backend.database import Base


class AppointmentStatus(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class BookedVia(str, enum.Enum):
    AI = "AI"
    MANUAL = "MANUAL"


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id = Column(UUID(as_uuid=True), ForeignKey("callers.id"), nullable=False, index=True)
    cal_booking_id = Column(String(255), nullable=True)
    cal_booking_uid = Column(String(255), nullable=True, index=True)
    client_name = Column(String(255), nullable=False)
    client_phone = Column(String(20), nullable=False)
    client_email = Column(String(255), nullable=True)
    date_of_birth = Column(String(20), nullable=True)
    appointment_type = Column(String(100), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    status = Column(Enum(AppointmentStatus), nullable=False, default=AppointmentStatus.CONFIRMED)
    booked_via = Column(Enum(BookedVia), nullable=False, default=BookedVia.AI)
    call_id = Column(UUID(as_uuid=True), ForeignKey("calls.id"), nullable=True)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("trainers.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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

    @property
    def tenant_id(self):
        return self.caller.tenant_id if self.caller else None

    reminder_sent_at = Column(DateTime(timezone=True), nullable=True)
    reminder_2h_sent_at = Column(DateTime(timezone=True), nullable=True)
    followup_sent_at = Column(DateTime(timezone=True), nullable=True)
    review_requested_at = Column(DateTime(timezone=True), nullable=True)

    confirmed_by_client = Column(
        Boolean,
        nullable=True,
    )

    caller = relationship("Caller", lazy="selectin", foreign_keys=[caller_id])
    call = relationship("Call", back_populates="appointments")
    provider = relationship("Trainer", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Appointment {self.client_name} @ {self.scheduled_at}>"
