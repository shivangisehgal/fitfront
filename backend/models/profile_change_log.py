"""
Profile change log — audit trail for tenant profile edits.

Records every change a tenant owner makes to their profile (name, password,
business settings) so admins can review the history of modifications.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class ProfileChangeLog(Base):
    __tablename__ = "profile_change_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    # What was changed
    field_name = Column(String(100), nullable=False)   # e.g. "owner_name", "password", "business_name"
    old_value = Column(Text, nullable=True)             # Previous value (null for password changes)
    new_value = Column(Text, nullable=True)             # New value (null for password changes)

    # Who changed it
    changed_by = Column(String(255), nullable=False)    # email of the person who made the change

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", backref="profile_changes", lazy="selectin")

    def __repr__(self) -> str:
        return f"<ProfileChangeLog {self.field_name}: {self.old_value} → {self.new_value}>"
