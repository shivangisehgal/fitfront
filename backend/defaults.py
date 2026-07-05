"""
Centralized defaults for the FitFront platform.
"""

import re


def slugify_appointment_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower().strip()).strip("_") or "appointment"


DEFAULT_AGENT_NAME = "Sarah"
DEFAULT_BUSINESS_NAME = "Our Studio"
DEFAULT_GREETING = "Thank you for calling. How can I help you today?"

DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_SLOT_INTERVAL_MINUTES = 15
DEFAULT_APPOINTMENT_DURATION_MINUTES = 60
DEFAULT_BUSINESS_HOURS = {
    "monday": {"open": "06:00", "close": "21:00"},
    "tuesday": {"open": "06:00", "close": "21:00"},
    "wednesday": {"open": "06:00", "close": "21:00"},
    "thursday": {"open": "06:00", "close": "21:00"},
    "friday": {"open": "06:00", "close": "21:00"},
    "saturday": {"open": "08:00", "close": "14:00"},
    "sunday": None,
}

DEFAULT_TEST_CLIENT_NAME = "Alex Johnson"
