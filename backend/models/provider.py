"""
Trainer model — represents an individual coach/trainer within a fitness studio.

Each trainer can have:
  - Their own Google Calendar (calendar_id) or share the tenant's primary
  - A subset of appointment types they handle
  - Custom business hours that override the tenant defaults
  - An active/inactive flag for vacation / leave

Multi-tenant: every trainer belongs to one tenant via tenant_id FK.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class Trainer(Base):
    __tablename__ = "trainers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    title = Column(String(100), nullable=True)

    appointment_types = Column(JSONB, nullable=False, default=list)

    slot_capacity = Column(Integer, nullable=False, default=1)

    calendar_id = Column(String(255), nullable=True)

    business_hours_override = Column(JSONB, nullable=True)

    # Specialty this trainer focuses on (e.g. "Strength & Conditioning", "Yoga", "HIIT").
    specialty = Column(String(255), nullable=True)

    # Fixed time windows for intro/trial sessions (used to offer trial-session booking slots).
    # Format: {"monday": [{"start": "08:00", "end": "10:00"}], ...}
    trial_session_slots = Column(JSONB, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant", backref="trainers", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Trainer {self.name} ({self.title or 'N/A'})>"


# Backward-compatible alias for imports during transition
Provider = Trainer
