"""
Caller database model — stores caller contact and preference data.
Used for caller recognition (phone-based lookup) and personalised greetings.

Multi-tenant: each caller record belongs to one tenant. The same phone number can
exist under different tenants (enforced via UniqueConstraint on tenant_id + phone).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class Caller(Base):
    __tablename__ = "callers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_caller_tenant_phone"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True)

    # ── Identity ─────────────────────────────────────────────────────────
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    date_of_birth = Column(String(20), nullable=True)

    # ── Preference ───────────────────────────────────────────────────────
    preferred_appointment_type = Column(String(100), nullable=True)  # e.g. "demo_class"
    notes = Column(Text, nullable=True)                              # staff / instructor notes

    # ── Vertical-specific fields (JSONB) ─────────────────────────────────
    # Stores domain-specific data without new columns per vertical.
    # Fitness studios: {"goal": "fat_loss", "specialty_interest": "strength_training", "preferred_trainer": "Alex"}
    # NOTE: Cannot use 'metadata' — reserved by SQLAlchemy Declarative API.
    extra_data = Column(JSONB, nullable=False, default=lambda: {})

    # ── Test data flag ───────────────────────────────────────────────────
    is_test = Column(Boolean, nullable=False, default=False)  # True = created via Test Agent chat

    # ── State tracking ───────────────────────────────────────────────────
    is_new_caller = Column(Boolean, default=True)
    visit_count = Column(Integer, default=0)                         # total completed visits
    no_show_count = Column(Integer, default=0)                       # missed appointments
    first_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_appointment_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", backref="callers", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Caller {self.name} ({self.phone})>"
