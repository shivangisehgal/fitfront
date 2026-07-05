"""
PlatformConfig model — admin-managed global key-value settings.

Keys stored here (vs. .env) are editable at runtime via the admin UI
without requiring a server restart.  Sensitive values (API keys) are
stored as plain text but never returned unmasked through the API.

Current keys:
  bolna_api_key   — Bolna AI outbound calling API key (global, all tenants share)
  bolna_agent_id  — Bolna AI agent UUID (global)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime

from backend.database import Base


class PlatformConfig(Base):
    __tablename__ = "platform_config"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<PlatformConfig key={self.key}>"
