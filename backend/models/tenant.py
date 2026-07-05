"""
Tenant model — each row represents one onboarded client (gym, studio, etc.)
with all their integration credentials and config.

This is the foundation of multi-tenancy: every call, caller record, and call
state is scoped to a tenant via tenant_id foreign keys.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, String, Text, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.database import Base


class TenantStatus(str, enum.Enum):
    """Lifecycle states for tenant onboarding."""
    PENDING = "PENDING"          # submitted, awaiting admin approval
    APPROVED = "APPROVED"        # approved, integrations being configured
    ACTIVE = "ACTIVE"            # live, accepting calls
    SUSPENDED = "SUSPENDED"      # temporarily disabled (billing, abuse, etc.)
    DEACTIVATED = "DEACTIVATED"  # permanently off


class BusinessType(str, enum.Enum):
    """Supported business verticals."""
    FITNESS_STUDIO = "fitness_studio"
    CUSTOM = "custom"


class PlanTier(str, enum.Enum):
    """Pricing tiers — controls feature access."""
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(100), unique=True, nullable=False, index=True)

    # ── Business identity ────────────────────────────────────────────────
    business_name = Column(String(255), nullable=False)
    business_type = Column(
        Enum(BusinessType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=BusinessType.FITNESS_STUDIO,
    )
    business_phone = Column(String(20), nullable=True)
    business_address = Column(Text, nullable=True)
    business_website = Column(String(255), nullable=True)
    google_maps_url = Column(Text, nullable=True)
    timezone = Column(String(50), nullable=False, default="America/Chicago")

    # ── Owner / admin contact ────────────────────────────────────────────
    owner_name = Column(String(255), nullable=False)
    owner_email = Column(String(255), nullable=False, unique=True, index=True)
    owner_phone = Column(String(20), nullable=True)

    # ── Auth ─────────────────────────────────────────────────────────────
    password_hash = Column(String(255), nullable=True)  # bcrypt hash
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    # ── Plan & status ────────────────────────────────────────────────────
    plan = Column(Enum(PlanTier), nullable=False, default=PlanTier.STARTER)
    status = Column(Enum(TenantStatus), nullable=False, default=TenantStatus.PENDING)
    agent_active = Column(Boolean, nullable=False, default=True)
    demo_mode = Column(Boolean, nullable=False, default=True)

    # ── Agent personality ────────────────────────────────────────────────
    agent_name = Column(String(100), nullable=False, default="Sarah")
    greeting_message = Column(
        Text, nullable=True,
        default="Thank you for calling. How can I help you today?",
    )
    system_prompt_override = Column(Text, nullable=True)
    voice_config = Column(
        JSONB, nullable=False,
        default=lambda: {"provider": "11labs", "voiceId": "21m00Tcm4TlvDq8ikWAM"},
    )
    end_call_phrases = Column(
        JSONB, nullable=False,
        default=lambda: ["goodbye", "thank you bye", "bye bye"],
    )

    # ── Twilio integration ───────────────────────────────────────────────
    twilio_account_sid = Column(String(255), nullable=True)
    twilio_auth_token = Column(String(255), nullable=True)
    twilio_phone_number = Column(String(20), nullable=True)

    feature_twilio_enabled = Column(Boolean, nullable=False, default=True)

    # ── Usage metering ───────────────────────────────────────────────────
    call_minutes_used = Column(Float, nullable=False, default=0.0)
    sms_sent = Column(Integer, nullable=False, default=0)
    current_period_start = Column(DateTime(timezone=True), nullable=True)

    # ── Google Calendar OAuth ───────────────────────────────────────────
    google_calendar_refresh_token = Column(String(512), nullable=True)
    google_calendar_email = Column(String(255), nullable=True)
    google_calendar_connected = Column(Boolean, nullable=False, default=False)

    # ── Test Agent (chat) ──────────────────────────────────────────────
    test_callers = Column(JSONB, nullable=False, default=lambda: [])

    # DEPRECATED: Legacy separate arrays — kept for migration, prefer test_callers
    test_caller_phone = Column(String(20), nullable=True)
    test_caller_phones = Column(JSONB, nullable=False, default=lambda: [])
    test_client_name = Column(String(100), nullable=True, default="Alex Johnson")
    test_client_names = Column(JSONB, nullable=False, default=lambda: ["Alex Johnson"])

    # ── Escalation ───────────────────────────────────────────────────────
    escalation_phone = Column(String(20), nullable=True)
    escalation_transfer_number = Column(String(20), nullable=True)

    # ── Appointment configuration ────────────────────────────────────────
    appointment_types = Column(
        JSONB, nullable=False,
        default=lambda: [
            {
                "code": "personal_training",
                "name": "Personal Training Session",
                "duration_minutes": 60,
                "slot_capacity": 1,
            },
        ],
    )
    business_hours = Column(JSONB, nullable=True)

    holidays = Column(JSONB, nullable=False, default=lambda: [])

    reminder_settings = Column(
        JSONB, nullable=False,
        default=lambda: {
            "2h_enabled": True,
            "confirmation_reply_enabled": True,
        },
    )

    review_settings = Column(
        JSONB, nullable=False,
        default=lambda: {
            "enabled": False,
            "google_review_link": "",
            "delay_hours": 24,
            "appointment_types": [],
        },
    )

    knowledge_base = Column(JSONB, nullable=False, default=lambda: {})
    emergency_guidance = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<Tenant {self.slug} ({self.business_name})>"
