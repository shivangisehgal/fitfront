"""
SMS message model — tracks all inbound and outbound SMS for two-way conversations.

Inbound: caller texts the institute's Twilio number → AI agent responds
Outbound: reminders, confirmations, waitlist notifications, review requests

Tenant scope and the caller's phone are derived via caller_id → callers
(no stored tenant_id or client_phone columns). All SQL filtering by tenant
or phone uses an explicit JOIN to callers.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String, Text, case, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from backend.database import Base


class SMSDirection(str, enum.Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class SMSMessage(Base):
    __tablename__ = "sms_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id = Column(UUID(as_uuid=True), ForeignKey("callers.id"), nullable=False, index=True)

    direction = Column(Enum(SMSDirection), nullable=False)
    from_number = Column(String(20), nullable=False)
    to_number = Column(String(20), nullable=False)
    body = Column(Text, nullable=False)

    # Twilio message SID for delivery tracking
    twilio_sid = Column(String(50), nullable=True)

    # Who sent this outbound message: 'agent' (AI), 'admin' (manual from UI), 'system' (automated)
    sender_type = Column(String(20), nullable=False, server_default='agent')

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

    # Derived from caller — no stored columns (use explicit JOIN for SQL filtering)
    @property
    def tenant_id(self):
        return self.caller.tenant_id if self.caller else None

    @property
    def client_phone(self):
        return self.caller.phone if self.caller else None

    # Relationships
    caller = relationship("Caller", lazy="selectin", foreign_keys=[caller_id])

    def __repr__(self) -> str:
        return f"<SMSMessage {self.direction.value} {self.from_number} → {self.to_number}>"
