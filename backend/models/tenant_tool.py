"""
TenantTool — per-tenant custom tool definitions stored in PostgreSQL.

Tenant admins create these from the UI. The ToolRegistry loads active tools
at session time and injects them into the LLM's tool list, enabling zero-code
custom tool extensions.

Handler types:
    http_webhook  — POST args to a URL the tenant controls
    kb_lookup     — search this tenant's knowledge base on a topic
    sms_send      — send a templated SMS using the tenant's Twilio config
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database import Base


class HandlerType(str, enum.Enum):
    HTTP_WEBHOOK = "http_webhook"
    KB_LOOKUP    = "kb_lookup"
    SMS_SEND     = "sms_send"


class TenantTool(Base):
    """
    A custom tool defined by a tenant admin.

    handler_config keys by type:
      http_webhook : {"url": "https://...", "method": "POST", "headers": {...}}
      kb_lookup    : {"topic": "hours|location|services|faqs|all"}
      sms_send     : {"template": "Hi {parent_name}, here is your info: {url}"}
    """

    __tablename__ = "tenant_tools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_tool_name"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Snake-case tool name exposed to the LLM (e.g. "check_scholarship")
    name = Column(String(100), nullable=False)
    # Shown verbatim in the LLM's function schema — write it like a docstring
    description = Column(Text, nullable=False)
    # JSON Schema object describing the tool's parameters
    parameters_schema = Column(
        JSONB,
        nullable=False,
        default=lambda: {"type": "object", "properties": {}, "required": []},
    )
    handler_type = Column(Enum(HandlerType), nullable=False)
    handler_config = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant = relationship("Tenant", backref="custom_tools", lazy="selectin")
