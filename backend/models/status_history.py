"""
Appointment status history — audit trail for every status change.

Records who changed the status, when, and optionally why. This provides
a complete timeline of each appointment's lifecycle:
  CONFIRMED → COMPLETED (attended by caller)
  CONFIRMED → NO_SHOW (caller didn't show)
  CONFIRMED → CANCELLED (cancelled by caller or institute)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class AppointmentStatusHistory(Base):
    __tablename__ = "appointment_status_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    appointment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_status = Column(String(20), nullable=True)   # NULL for initial creation
    new_status = Column(String(20), nullable=False)
    changed_by = Column(String(100), nullable=False, default="system")  # "system", "dashboard", email
    note = Column(Text, nullable=True)               # optional reason/context
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    appointment = relationship("Appointment", backref="status_history", lazy="selectin")

    def __repr__(self) -> str:
        return f"<StatusHistory {self.old_status} → {self.new_status} @ {self.created_at}>"
