"""
PlatformToolProvider — the built-in tools that power every tenant.

This module owns:
  • TOOL_SCHEMAS — the 12 OpenAI-format tool definitions (migrated from
    llm_service.ALL_TOOLS).
  • PlatformToolProvider.list_tools()  — returns filtered schemas for a
    given tenant (respects Twilio, scheduling, escalation flags).
  • PlatformToolProvider.call_tool()   — full dispatch logic (migrated from
    llm_service._execute_tool's if/elif block).
  • Utility helpers: _build_office_info, _resolve_dow_to_date, etc.
    (These are also re-exported so llm_service / llm_proxy can keep their
    current import paths.)

session_ctx keys:
    tenant_ctx  — Tenant ORM object or None
    call_id     — str, vapi call id
    session     — the in-memory session dict (may be mutated)
    is_test     — bool
    t0          — float, time.time() at tool-call start (for latency logging)
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from backend.config import settings
from backend.defaults import DEFAULT_TIMEZONE
from backend.services import calendar_service, sms_service

logger = logging.getLogger(__name__)


# ── Timezone-aware display formatting ────────────────────────────────────────

def _fmt_slot_display(slot_time_str: str, tz_name: str) -> tuple[str, str]:
    """Convert a slot ISO string to (display_time, display_date) in the tenant timezone.

    Returns e.g. ("10:00 AM", "Friday, Jun 27") — safe for the LLM to say verbatim.
    Never raises; falls back to the raw string on parse failure.
    """
    try:
        from zoneinfo import ZoneInfo
        from dateutil import parser as _dtp
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
        dt = _dtp.parse(slot_time_str)
        # Ensure we're in tenant timezone regardless of embedded offset
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz)
        else:
            dt = dt.replace(tzinfo=tz)
        display_time = dt.strftime("%-I:%M %p")   # "10:00 AM" — no leading zero
        display_date = dt.strftime("%A, %B %d")   # "Friday, June 27"
        return display_time, display_date
    except Exception:
        return slot_time_str, ""


# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Look up available session slots for a given date and type. "
                "If the caller/student requested a specific faculty member, include their ID. "
                "IMPORTANT: When checking slots for a RESCHEDULE, pass the booking_uid "
                "of the session being moved — this ensures the caller's own "
                "session doesn't block the new time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to check in YYYY-MM-DD format.",
                    },
                    "appointment_type": {
                        "type": "string",
                        "description": "Type of session.",
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "Optional. ID of the faculty member the caller/student wants to see (from get_providers).",
                    },
                    "booking_uid": {
                        "type": "string",
                        "description": (
                            "Optional. When checking slots for a RESCHEDULE, pass the "
                            "booking_uid of the session being moved. This excludes it "
                            "from the overlap check so the caller's own booking doesn't "
                            "block the new time."
                        ),
                    },
                    "specialty": {
                        "type": "string",
                        "description": (
                            "Coaching institutes only — for demo_class bookings, the subject "
                            "the student wants a demo for (e.g. 'Physics', 'Chemistry', 'Mathematics'). "
                            "Filters results to only show faculty who teach that subject."
                        ),
                    },
                },
                "required": ["date", "appointment_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_week_slots",
            "description": (
                "Get available slots for every remaining day of the current week "
                "(today through Sunday) in a single call. "
                "Use this whenever the caller asks about 'this week', 'any day this week', "
                "'anytime that works', or doesn't specify a particular day. "
                "Returns slots grouped by day so the caller can pick a day and time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_type": {
                        "type": "string",
                        "description": "Type of session.",
                    },
                    "specialty": {
                        "type": "string",
                        "description": (
                            "Coaching institutes only — for demo_class bookings, the subject "
                            "the student wants a demo for (e.g. 'Physics', 'Mathematics'). "
                            "Filters results to only show faculty who teach that subject."
                        ),
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "Optional. ID of the faculty member the caller/student wants.",
                    },
                },
                "required": ["appointment_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a session for the caller. Always include provider_id — "
                "use a specific UUID from get_providers, or '__auto__' if the caller "
                "has no preference (system will auto-assign the best available faculty member). "
                "IMPORTANT: Always use the caller-ID phone number from your system prompt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {"type": "string", "description": "Caller's full name (or parent's name if booking on behalf of a student)."},
                    "phone": {
                        "type": "string",
                        "description": "Caller's phone number — MUST be the caller-ID number from your system prompt.",
                    },
                    "email": {
                        "type": "string",
                        "description": "Caller's email (optional — system provides default if not given).",
                    },
                    "dob": {
                        "type": "string",
                        "description": "Date of birth (MM/DD/YYYY). Optional for coaching institutes where DOB is not collected.",
                    },
                    "appointment_type": {
                        "type": "string",
                        "description": "Type of session.",
                    },
                    "slot_time": {
                        "type": "string",
                        "description": "EXACT exact_slot_time string from get_available_slots. Do NOT construct this yourself — always use a value returned by get_available_slots.",
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "Required. UUID of the faculty member from get_providers, or '__auto__' if the caller has no preference — the system will auto-assign the best available faculty member.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Important details the caller mentioned — reason for visit, course interest and grade (coaching), special requests. Capture anything operationally relevant. Leave empty if nothing notable was said.",
                    },
                    "client_name": {
                        "type": "string",
                        "description": "Coaching institutes only — the student's name. If the student is calling for themselves, this will be the same as caller_name. If a parent is calling on behalf of their child, this is the child's name.",
                    },
                    "grade": {
                        "type": "string",
                        "description": "Coaching institutes only — student's current grade/class (e.g. '10', '11', '12').",
                    },
                    "board": {
                        "type": "string",
                        "description": "Coaching institutes only — student's school board (e.g. 'CBSE', 'ICSE', 'State Board').",
                    },
                    "target_exam": {
                        "type": "string",
                        "description": "Coaching institutes only — the exam the student is preparing for (e.g. 'JEE', 'NEET', 'CA Foundation', 'UPSC').",
                    },
                    "candidate_type": {
                        "type": "string",
                        "enum": ["fresher", "dropper"],
                        "description": "Coaching institutes only — whether the student is appearing for the first time ('fresher') or has attempted the exam before ('dropper').",
                    },
                    "attempt_number": {
                        "type": "integer",
                        "description": "Coaching institutes only — which attempt this is (1 for first attempt, 2 for second, etc.). Set to 1 for freshers; ask droppers 'is this your second attempt?'",
                    },
                    "mode_preference": {
                        "type": "string",
                        "enum": ["online", "offline", "hybrid"],
                        "description": "Coaching institutes only — whether the student prefers online classes, offline (in-person) classes, or hybrid.",
                    },
                    "medium": {
                        "type": "string",
                        "description": "Coaching institutes only — the language/medium of instruction the student prefers (e.g. 'English', 'Hindi').",
                    },
                },
                "required": ["caller_name", "phone", "appointment_type", "slot_time", "provider_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": (
                "Reschedule an existing session. IMPORTANT: Before calling this, "
                "ALWAYS call get_available_slots first to get valid time slots. Use the "
                "exact_slot_time from that result as the new_slot_time — do NOT construct "
                "times yourself. If the caller wants a different faculty member, pass the "
                "provider_id from the available_providers list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_uid": {
                        "type": "string",
                        "description": "Booking reference ID from the original booking.",
                    },
                    "new_slot_time": {
                        "type": "string",
                        "description": "EXACT exact_slot_time string from get_available_slots. Do NOT construct this yourself — always use a value returned by get_available_slots.",
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "UUID of the faculty member to reassign this session to. Use the 'id' from the available_providers list returned by get_available_slots. Pass this whenever the caller picks a specific faculty member.",
                    },
                },
                "required": ["booking_uid", "new_slot_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an existing session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_uid": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["booking_uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Transfer the call to a human team member. ONLY call this for: "
                "(1) immediate emergencies, severe distress, or explicit human requests — "
                "no confirmation needed; "
                "(2) institute-specific questions you cannot answer, or billing disputes — but ONLY "
                "after asking 'Would you like me to connect you with a team member?' and the "
                "caller says yes. "
                "NEVER call this for general knowledge questions — answer those directly using your training. "
                "NEVER call this on greetings or simple chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the call is being escalated.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_callback_request",
            "description": "Request that the office call the caller back.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {"type": "string", "description": "Caller's full name."},
                    "phone": {"type": "string", "description": "Caller's phone number."},
                    "reason": {"type": "string"},
                },
                "required": ["caller_name", "phone", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_office_info",
            "description": (
                "Get accurate institute/office information. ALWAYS call this tool when the caller "
                "asks about: business hours, location, phone number, services, fees, batch details, "
                "course details, 'how long does X take?', or any factual "
                "question about the institute. "
                "Do NOT answer from memory or general knowledge — always call this tool and "
                "read the exact answer from the result. Do not embellish or add information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": ["hours", "location", "services", "faqs", "all"],
                        "description": (
                            "What info to retrieve: "
                            "'hours' for business hours, "
                            "'location' for address/phone, "
                            "'services' for pricing, fees and costs (use this for 'how much' or 'price of X'), "
                            "'faqs' for course/procedure questions like 'how long does X take', "
                            "'all' to get everything (use when unsure)."
                        ),
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_caller_appointments",
            "description": (
                "Look up a caller's/student's upcoming sessions by their phone number. "
                "Use when a returning caller wants to reschedule, cancel, or check "
                "on an existing session. Returns session details including "
                "booking_uid needed for reschedule/cancel. "
                "IMPORTANT: Always use the caller's phone number from caller-ID. "
                "Confirm it verbally first, but always pass the caller-ID number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Caller's phone number — MUST be the caller-ID number from your system prompt.",
                    },
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_waitlist",
            "description": (
                "Add a caller/student to the waitlist when their preferred date/time has no "
                "available slots. The caller will be automatically notified by SMS if "
                "a slot opens up. CRITICAL: Reuse any caller_name, phone, appointment_type, "
                "and preferred_date you already learned in this conversation or from the "
                "CALLER INFORMATION section. Do NOT re-ask the caller for fields you already "
                "know — that's a failure mode. ALSO include preferred_time_start / "
                "preferred_time_end (24h HH:MM format) whenever the caller mentioned a time "
                "preference — admins rely on this to match slots and to contact the caller at "
                "the right time. If the caller asked for a specific faculty member, pass that "
                "faculty member's id as provider_id so the institute can match them when the right faculty has an opening."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caller_name": {"type": "string", "description": "Caller's full name."},
                    "phone": {"type": "string", "description": "Caller's phone number."},
                    "appointment_type": {"type": "string", "description": "Type of session."},
                    "preferred_date": {"type": "string", "description": "Preferred date in YYYY-MM-DD format."},
                    "preferred_time_start": {
                        "type": "string",
                        "description": "Optional start of the caller's preferred time window in 24-hour HH:MM format (e.g. '09:00'). Pass this whenever the caller says they want morning/afternoon/evening or names a time.",
                    },
                    "preferred_time_end": {
                        "type": "string",
                        "description": "Optional end of the caller's preferred time window in 24-hour HH:MM format (e.g. '12:00').",
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "Optional UUID of the faculty member the caller asked for. Use the id from get_providers. Omit if the caller said 'anyone is fine'.",
                    },
                },
                "required": ["caller_name", "phone", "appointment_type", "preferred_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_waitlist_status",
            "description": (
                "Check a caller's/student's waitlist status — their position in queue, whether a slot "
                "opened, etc. Call this when a caller asks about their waitlist status or "
                "position. Use the caller's phone from caller-ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Caller's phone number from caller-ID.",
                    },
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_providers",
            "description": (
                "Get the list of available faculty at this institute. Call this when a "
                "caller asks to see a specific faculty member or when you need to offer faculty choices."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_type": {
                        "type": "string",
                        "description": "Optional. Filter faculty who handle this session type.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_caller",
            "description": (
                "Look up a caller's/student's full profile — name, personal details, past sessions, "
                "and upcoming sessions. CALL THIS FIRST at the start of any booking, rescheduling, "
                "cancellation, or appointment-check flow so you know if they're returning and can "
                "greet them by name and avoid re-asking known info. "
                "Use the phone from the CALLER PHONE section of your system prompt as the primary key. "
                "Only fall back to name if phone is truly unavailable — names can have multiple matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Caller's phone number — ALWAYS provide this. You have it from caller-ID.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Caller's name. Only use as last resort if phone is truly unavailable. May return multiple matches.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_caller_info",
            "description": (
                "Update a caller's/student's profile in the CRM. Use when a caller "
                "provides new or corrected details during the conversation — for example, "
                "their date of birth, notes, or coaching profile fields. "
                "Use the caller's phone number from caller-ID (in your system prompt) to identify them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Caller's phone number (from caller-ID in your system prompt).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Updated caller's name (only if correcting).",
                    },
                    "dob": {
                        "type": "string",
                        "description": "Date of birth (MM/DD/YYYY).",
                    },
                    "email": {
                        "type": "string",
                        "description": "Caller's email address.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Instructor notes about the caller.",
                    },
                    "client_name": {
                        "type": "string",
                        "description": "Coaching institutes only — the student's name.",
                    },
                    "grade": {
                        "type": "string",
                        "description": "Coaching institutes only — student's current grade/class (e.g. '10', '11', '12').",
                    },
                    "board": {
                        "type": "string",
                        "description": "Coaching institutes only — student's school board (e.g. 'CBSE', 'ICSE', 'State Board').",
                    },
                    "target_exam": {
                        "type": "string",
                        "description": "Coaching institutes only — exam the student is targeting (e.g. 'JEE', 'NEET', 'CA Foundation', 'UPSC').",
                    },
                    "candidate_type": {
                        "type": "string",
                        "enum": ["fresher", "dropper"],
                        "description": "Coaching institutes only — 'fresher' for first attempt, 'dropper' for repeat attempt.",
                    },
                    "attempt_number": {
                        "type": "integer",
                        "description": "Coaching institutes only — which attempt this is (1, 2, 3…).",
                    },
                    "mode_preference": {
                        "type": "string",
                        "enum": ["online", "offline", "hybrid"],
                        "description": "Coaching institutes only — student's preferred class mode.",
                    },
                    "medium": {
                        "type": "string",
                        "description": "Coaching institutes only — preferred language/medium of instruction (e.g. 'English', 'Hindi').",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]

# Set of platform tool names for quick lookup
_PLATFORM_TOOL_NAMES: frozenset[str] = frozenset(
    t["function"]["name"] for t in TOOL_SCHEMAS
)

# Tools that require Twilio + escalation to be configured
_TOOLS_REQUIRING_TWILIO = frozenset({"escalate_to_human", "send_callback_request"})

# Tools that require native scheduling (business_hours) to be configured
_TOOLS_REQUIRING_SCHEDULING = frozenset({
    "get_available_slots", "get_week_slots", "book_appointment",
    "reschedule_appointment", "cancel_appointment", "add_to_waitlist",
})


# ── Office info helpers (also used by llm_proxy.py) ──────────────────────────

def _fmt_time_12h(t: str) -> str:
    """Convert '08:00' → '8:00 AM', '16:00' → '4:00 PM'."""
    try:
        parts = t.strip().split(":")
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        suffix = "AM" if h < 12 else "PM"
        display_h = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
        return f"{display_h}:{m:02d} {suffix}"
    except Exception:
        return t


def _get_kb(tenant_ctx: Any | None) -> dict:
    """Get the knowledge base for a tenant (lazy import to avoid circular deps)."""
    from backend.services.knowledge_service import get_tenant_kb
    return get_tenant_kb(tenant_ctx)


def _build_office_info(topic: str, tenant_ctx: Any | None) -> dict[str, Any]:
    """
    Build pre-formatted office info that the LLM can read verbatim.
    Returns a dict with 'summary_for_assistant'.
    """
    parts = []

    # ── Hours ────────────────────────────────────────────────────────────
    if topic in ("hours", "all"):
        bh = tenant_ctx.business_hours if tenant_ctx else None
        if bh:
            day_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            groups: list[tuple[list[str], str]] = []
            for i, day_key in enumerate(day_order):
                info = bh.get(day_key)
                if info and isinstance(info, dict) and info.get("open"):
                    label = f"{_fmt_time_12h(info['open'])} to {_fmt_time_12h(info['close'])}"
                else:
                    label = "Closed"
                if groups and groups[-1][1] == label:
                    groups[-1][0].append(day_names[i])
                else:
                    groups.append(([day_names[i]], label))

            sentence_parts = []
            for day_list, label in groups:
                if len(day_list) == 1:
                    sentence_parts.append(f"{day_list[0]} {label}")
                elif len(day_list) == 2:
                    sentence_parts.append(f"{day_list[0]} and {day_list[1]} {label}")
                else:
                    sentence_parts.append(f"{day_list[0]} through {day_list[-1]} {label}")
            hours_text = "Our hours are: " + ", ".join(sentence_parts) + "."
        else:
            hours_text = "Business hours have not been configured yet."
        parts.append(hours_text)

        # ── Upcoming holidays / closures ────────────────────────────────
        try:
            from backend.services.tenant_service import upcoming_holidays as _upcoming
            upcoming = _upcoming(tenant_ctx, limit=6) if tenant_ctx else []
        except Exception:
            upcoming = []
        if upcoming:
            from datetime import datetime as _dt
            holiday_lines = []
            for h in upcoming:
                try:
                    d = _dt.strptime(h["date"], "%Y-%m-%d").date()
                    holiday_lines.append(f"{d.strftime('%B %-d')} for {h.get('name') or 'Holiday'}")
                except Exception:
                    holiday_lines.append(f"{h.get('date')} for {h.get('name') or 'Holiday'}")
            parts.append(
                "We're also closed on the following upcoming dates: "
                + "; ".join(holiday_lines) + "."
            )

    # ── Location / contact ───────────────────────────────────────────────
    if topic in ("location", "all"):
        contact_parts = []
        if tenant_ctx:
            if tenant_ctx.business_address:
                contact_parts.append(f"We are located at {tenant_ctx.business_address}.")
            if tenant_ctx.business_phone:
                contact_parts.append(f"Our phone number is {tenant_ctx.business_phone}.")
        kb = _get_kb(tenant_ctx)
        office_info = kb.get("office_info", {})
        if not contact_parts:
            if office_info.get("address"):
                contact_parts.append(f"We are located at {office_info['address']}.")
            if office_info.get("phone"):
                contact_parts.append(f"Our phone number is {office_info['phone']}.")
        if contact_parts:
            parts.append(" ".join(contact_parts))
        else:
            parts.append("Office location and contact details are not configured yet.")

    # ── Services & pricing ───────────────────────────────────────────────
    if topic in ("services", "all"):
        kb = _get_kb(tenant_ctx)

        # ── Coaching institutes: use kb["courses"] for fee info ──────────
        courses = kb.get("courses", [])
        if courses and any(c.get("fee") for c in courses):
            svc_lines = ["Here are our courses and fees:"]
            for c in courses:
                name = c.get("name", "Course")
                fee = (c.get("fee") or "").strip()
                desc = (c.get("description") or "").strip()
                line = f"  - {name}"
                if desc:
                    line += f" ({desc})"
                line += f": {fee}" if fee else ": fee available upon enquiry"
                svc_lines.append(line)
            parts.append("\n".join(svc_lines))
        elif courses:
            # Courses exist but no fees configured
            svc_lines = ["Courses we offer:"]
            for c in courses:
                name = c.get("name", "Course")
                desc = (c.get("description") or "").strip()
                line = f"  - {name}" + (f" — {desc}" if desc else "")
                svc_lines.append(line)
            svc_lines.append("  For fee details, please speak to our counselors.")
            parts.append("\n".join(svc_lines))
        else:
            # ── Non-coaching / generic: use kb["services"] ────────────────
            services = kb.get("services", [])
            if services:
                svc_lines = ["Here are our services and approximate pricing:"]
                for svc in services:
                    name = svc.get("name", "Service")
                    low = svc.get("price_min")
                    high = svc.get("price_max")
                    if low and high:
                        svc_lines.append(f"  - {name}: ${low} to ${high}")
                    elif low:
                        svc_lines.append(f"  - {name}: from ${low}")
                    elif high:
                        svc_lines.append(f"  - {name}: up to ${high}")
                    else:
                        svc_lines.append(f"  - {name}: pricing available upon request")
                parts.append("\n".join(svc_lines))
            else:
                if topic == "services":
                    parts.append("Service and pricing information is not available right now. The caller can call during business hours for details.")

    # ── FAQs ─────────────────────────────────────────────────────────────
    if topic in ("faqs", "all"):
        kb = _get_kb(tenant_ctx)
        faqs = kb.get("faqs", [])
        if faqs:
            faq_lines = ["Frequently asked questions:"]
            for faq in faqs:
                faq_lines.append(f"  Q: {faq.get('question', '')}")
                faq_lines.append(f"  A: {faq.get('answer', '')}")
            parts.append("\n".join(faq_lines))

    summary = "\n\n".join(parts) if parts else "I don't have that information available right now."
    return {
        "summary_for_assistant": (
            f"{summary}\n\n"
            f"Read the information above to the caller in 1-2 short sentences. "
            f"Use the EXACT answer from the FAQs — do NOT add generic information, "
            f"do NOT embellish with details not provided, and do NOT guess. "
            f"If the FAQ says 'Typically, 1 hr', say 'Typically about an hour' — nothing more."
        ),
    }


# ── Day-of-week date resolution ───────────────────────────────────────────────

_DOW_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_dow_to_date(user_text: str, tenant_ctx: Any | None = None) -> str | None:
    """
    If the user's message mentions a day-of-week or 'tomorrow'/'today',
    return the corresponding YYYY-MM-DD in the tenant's timezone.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    tz_name = DEFAULT_TIMEZONE
    if tenant_ctx and getattr(tenant_ctx, "timezone", None):
        tz_name = tenant_ctx.timezone
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)

    today = datetime.now(tz)
    text = user_text.lower()

    if "today" in text:
        return today.strftime("%Y-%m-%d")
    if "tomorrow" in text:
        from datetime import timedelta
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    for dow_name, dow_idx in _DOW_MAP.items():
        if dow_name in text:
            today_idx = today.weekday()
            delta = (dow_idx - today_idx) % 7
            if delta == 0:
                delta = 7  # If today IS that day, go to next week
            from datetime import timedelta
            target = today + timedelta(days=delta)
            return target.strftime("%Y-%m-%d")

    return None


# ── PlatformToolProvider ──────────────────────────────────────────────────────

class PlatformToolProvider:
    """
    The platform's built-in scheduling/CRM/escalation tools.

    Migrated from llm_service.ALL_TOOLS (schemas) and the inline
    if/elif dispatch block inside llm_service._execute_tool (execution).
    """

    # Public set for validation (e.g. custom tool name collision check)
    TOOL_NAMES: frozenset[str] = _PLATFORM_TOOL_NAMES

    def handles(self, tool_name: str) -> bool:
        return tool_name in _PLATFORM_TOOL_NAMES

    async def list_tools(self, tenant_ctx: Any) -> list[dict]:
        """
        Return filtered tool schemas for this tenant in OpenAI format.

        Filtering rules:
        • hide Twilio tools when Twilio not configured or escalation disabled
        • hide scheduling tools when no business hours are configured
        • inject appointment_type enum from tenant config
        """
        return self.list_tools_sync(tenant_ctx)

    def list_tools_sync(self, tenant_ctx: Any) -> list[dict]:
        """
        Synchronous version of list_tools() for call sites that cannot await
        (e.g. llm_service.get_tools, llm_proxy).
        """
        if tenant_ctx is None:
            return TOOL_SCHEMAS

        has_twilio = bool(
            tenant_ctx.twilio_account_sid
            and tenant_ctx.twilio_auth_token
            and tenant_ctx.feature_twilio_enabled
        )
        has_scheduling = bool(tenant_ctx.business_hours)
        has_escalation = bool(
            tenant_ctx.emergency_guidance
            or getattr(tenant_ctx, "escalation_transfer_number", "")
            or getattr(tenant_ctx, "escalation_phone", "")
            or getattr(tenant_ctx, "business_phone", "")
        )

        # Build dynamic appointment type enum from tenant config
        appt_keys: list[str] | None = None
        if tenant_ctx.appointment_types:
            appt_keys = [at.get("code", "consultation") for at in tenant_ctx.appointment_types]

        filtered = []
        for tool in TOOL_SCHEMAS:
            name = tool["function"]["name"]
            if name in _TOOLS_REQUIRING_TWILIO and (not has_twilio or not has_escalation):
                continue
            if name in _TOOLS_REQUIRING_SCHEDULING and not has_scheduling:
                continue

            # Inject tenant-specific appointment type enum
            if appt_keys and name in ("get_available_slots", "get_week_slots", "book_appointment", "add_to_waitlist"):
                tool = copy.deepcopy(tool)
                props = tool["function"]["parameters"]["properties"]
                if "appointment_type" in props:
                    props["appointment_type"]["enum"] = appt_keys

            filtered.append(tool)

        logger.info(
            "[Platform] Tool filter: twilio=%s scheduling=%s escalation=%s → %d/%d tools",
            has_twilio, has_scheduling, has_escalation, len(filtered), len(TOOL_SCHEMAS),
        )
        return filtered

    async def call_tool(self, name: str, args: dict, session_ctx: dict) -> dict[str, Any]:
        """
        Execute a platform tool call.

        session_ctx must contain:
            tenant_ctx, call_id, session (mutable dict), is_test, t0
        """
        tenant_ctx = session_ctx.get("tenant_ctx")
        call_id: str = session_ctx.get("call_id", "unknown")
        session: dict | None = session_ctx.get("session")
        is_test: bool = session_ctx.get("is_test", False)
        t0: float = session_ctx.get("t0", time.time())

        if name == "get_available_slots":
            return await self._get_available_slots(args, tenant_ctx, call_id, session, t0)

        elif name == "get_week_slots":
            return await self._get_week_slots(args, tenant_ctx, call_id, t0)

        elif name == "book_appointment":
            return await self._book_appointment(args, tenant_ctx, call_id, session, is_test, t0)

        elif name == "reschedule_appointment":
            return await self._reschedule_appointment(args, tenant_ctx, call_id, session, t0)

        elif name == "cancel_appointment":
            return await self._cancel_appointment(args, tenant_ctx, call_id, session, t0)

        elif name == "escalate_to_human":
            return await self._escalate_to_human(args, tenant_ctx, call_id, session)

        elif name == "send_callback_request":
            return self._send_callback_request(args, tenant_ctx)

        elif name == "get_office_info":
            return _build_office_info(args.get("topic", "all"), tenant_ctx)

        elif name == "lookup_caller_appointments":
            return await self._lookup_caller_appointments(args, tenant_ctx, call_id, t0)

        elif name == "add_to_waitlist":
            return await self._add_to_waitlist(args, tenant_ctx, call_id, is_test, t0)

        elif name == "check_waitlist_status":
            return await self._check_waitlist_status(args, tenant_ctx, call_id, t0)

        elif name == "lookup_caller":
            return await self._lookup_caller(args, tenant_ctx, call_id, session, t0)

        elif name == "update_caller_info":
            return await self._update_caller_info(args, tenant_ctx, call_id, t0)

        elif name == "get_providers":
            return await self._get_providers(args, tenant_ctx, call_id, t0)

        else:
            # Should not reach here if handles() is checked first
            raise ValueError(f"PlatformToolProvider does not handle tool: {name!r}")

    # ── Individual tool handlers ──────────────────────────────────────────────

    async def _get_available_slots(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None, t0: float
    ) -> dict:
        appt_type = args.get("appointment_type", "consultation")
        provider_id_str = args.get("provider_id", "")
        date = args.get("date", "")
        if not date:
            from datetime import timedelta
            date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        # ── Date correction: resolve user's day-of-week to actual date ──
        if session:
            user_text = ""
            for m in reversed(session["messages"]):
                if m.get("role") == "user":
                    user_text = (m.get("content") or "").strip().lower()
                    break
            if user_text:
                resolved = _resolve_dow_to_date(user_text, tenant_ctx)
                if resolved and resolved != date:
                    logger.warning(
                        "[Call %s] Date correction: LLM said %r → user implied %r",
                        call_id, date, resolved,
                    )
                    date = resolved

        # ── Past-date guard ──────────────────────────────────────────────
        try:
            from zoneinfo import ZoneInfo as _ZI
            _tz_name = (
                tenant_ctx.timezone
                if tenant_ctx and getattr(tenant_ctx, "timezone", None)
                else DEFAULT_TIMEZONE
            )
            try:
                _tz = _ZI(_tz_name)
            except Exception:
                _tz = _ZI(DEFAULT_TIMEZONE)
            _today_local = datetime.now(_tz).date()
            _req_date = datetime.strptime(date, "%Y-%m-%d").date()
            if _req_date < _today_local:
                friendly = _req_date.strftime("%A, %B %d")
                logger.warning(
                    "[Call %s] get_available_slots refused — past date %s (today=%s)",
                    call_id, date, _today_local.isoformat(),
                )
                return {
                    "ok": False,
                    "error": "past_date",
                    "date": date,
                    "today": _today_local.isoformat(),
                    "summary_for_assistant": (
                        f"That date ({friendly}) has already passed — today is "
                        f"{_today_local.strftime('%A, %B %d, %Y')}. Politely let the caller know "
                        f"that date is in the past and ask which upcoming day they'd like instead. "
                        f"Do NOT offer the waitlist for a past date. Do NOT call add_to_waitlist."
                    ),
                    "available_slots": [],
                    "count": 0,
                }
        except (ValueError, TypeError):
            pass

        # ── Get provider-aware slots ──────────────────────────────────────
        from backend.services import native_scheduling
        import uuid as uuid_mod

        provider_uuid = None
        if provider_id_str:
            try:
                provider_uuid = uuid_mod.UUID(provider_id_str)
            except (ValueError, TypeError):
                pass

        exclude_uid = args.get("booking_uid", "") or None

        from backend.services.calendar_service import _resolve_appointment_config
        duration, max_conc = _resolve_appointment_config(appt_type, tenant_ctx)

        office_tz_name = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
        specialty_filter = args.get("specialty", "") or args.get("subject", "") or None

        provider_slots_result = await native_scheduling.get_provider_aware_slots(
            date_str=date,
            duration_minutes=duration,
            tenant_id=tenant_ctx.tenant_id if tenant_ctx else None,
            business_hours=tenant_ctx.business_hours if tenant_ctx else None,
            tz_name=office_tz_name,
            slot_capacity=max_conc,
            provider_id=provider_uuid,
            holidays=(tenant_ctx.holidays if tenant_ctx and getattr(tenant_ctx, "holidays", None) else None),
            exclude_booking_uid=exclude_uid,
            appointment_type=appt_type,
            specialty=specialty_filter,
        )

        # ── Holiday short-circuit ─────────────────────────────────────────
        holiday_info = provider_slots_result.get("holiday")
        if holiday_info:
            try:
                from dateutil import parser as dt_parser
                _friendly = dt_parser.parse(date).strftime("%A, %B %d")
            except Exception:
                _friendly = date
            holiday_name = holiday_info.get("name") or "a holiday"
            logger.info(
                "[Call %s] get_available_slots refused — %s is a holiday (%s)",
                call_id, date, holiday_name,
            )
            return {
                "ok": False,
                "error": "holiday",
                "date": date,
                "holiday": holiday_info,
                "summary_for_assistant": (
                    f"The office is CLOSED on {_friendly} for {holiday_name}. "
                    f"Tell the caller we're closed that day for {holiday_name} and "
                    f"ask which other day works. Do NOT offer the waitlist for a holiday. "
                    f"Do NOT call add_to_waitlist for this date."
                ),
                "available_slots": [],
                "count": 0,
            }

        # ── Subject not offered short-circuit ────────────────────────────
        # When get_provider_aware_slots finds no faculty for the requested subject
        # it returns {"slots": [], "subject_filter": <subject>}.  Treat this as
        # "subject not offered" — NOT "day is fully booked".
        _specialty_not_offered = provider_slots_result.get("specialty_filter") or provider_slots_result.get("subject_filter")
        if _specialty_not_offered and not provider_slots_result.get("slots"):
            logger.info(
                "[Call %s] get_available_slots: no faculty for subject=%r",
                call_id, _specialty_not_offered,
            )
            return {
                "ok": False,
                "error": "specialty_not_offered",
                "date": date,
                "specialty": _specialty_not_offered,
                "summary_for_assistant": (
                    f"No faculty at this institute currently teach {_specialty_not_offered!r}. "
                    f"Do NOT say 'no slots available' or 'fully booked' or 'closed'. "
                    f"Tell the caller: 'We don\u2019t currently offer {_specialty_not_offered} coaching.' "
                    f"Ask if they\u2019d like to book a demo for one of our other subjects, "
                    f"or offer to have a counselor follow up."
                ),
                "available_slots": [],
                "count": 0,
                "no_specialty_coverage": True,
            }

        provider_slots = provider_slots_result.get("slots", [])

        # Format slots with human-readable time labels
        try:
            from zoneinfo import ZoneInfo as _SlotZI
            _slot_tz = _SlotZI(office_tz_name)
            _tz_abbr = datetime.now(_slot_tz).strftime("%Z")
        except Exception:
            _tz_abbr = ""

        formatted_slots = []
        for slot_data in provider_slots:
            slot_time = slot_data.get("time", "")
            available_provs = slot_data.get("available_providers", [])
            display_time, display_date = _fmt_slot_display(slot_time, office_tz_name)
            time_label = display_time
            if _tz_abbr and display_time != slot_time:
                time_label += f" {_tz_abbr}"
            formatted_slots.append({
                "time_label": time_label,
                "display_time": display_time,
                "display_date": display_date,
                "exact_slot_time": slot_time,
                "available_providers": available_provs,
                "duration_minutes": slot_data.get("duration_minutes"),
            })

        try:
            from dateutil import parser as dt_parser
            friendly_date = dt_parser.parse(date).strftime("%A, %B %d")
        except Exception:
            friendly_date = date

        if not formatted_slots:
            summary = (
                f"There are no available slots on {friendly_date}. "
                f"Tell the caller politely that day is fully booked or closed, "
                f"and offer to check a different day. Do NOT read this message verbatim — "
                f"speak naturally in 1-2 sentences. "
                f"IMPORTANT: only {friendly_date} was checked — do NOT assume other days "
                f"are also fully booked. If the caller asked about 'this week' or multiple days, "
                f"you MUST call get_available_slots again for each remaining day individually."
            )
        else:
            import re as _re
            requested_time_str = args.get("time", "")
            if not requested_time_str and session:
                for m in reversed(session.get("messages", [])):
                    if m.get("role") == "user":
                        _user_txt = (m.get("content") or "").strip()
                        _time_match = _re.search(
                            r'(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))', _user_txt
                        )
                        if _time_match:
                            requested_time_str = _time_match.group(1)
                        break

            top = formatted_slots[:3]
            exact_match = False
            if requested_time_str:
                try:
                    from dateutil import parser as _tp
                    _req_dt = _tp.parse(requested_time_str)
                    _req_minutes = _req_dt.hour * 60 + _req_dt.minute
                    scored = []
                    for i, s in enumerate(formatted_slots):
                        try:
                            _s_dt = _tp.parse(s["exact_slot_time"])
                            _s_minutes = _s_dt.hour * 60 + _s_dt.minute
                            scored.append((abs(_s_minutes - _req_minutes), i, s))
                            if _s_minutes == _req_minutes:
                                exact_match = True
                        except Exception:
                            scored.append((9999, i, s))
                    scored.sort(key=lambda x: (x[0], x[1]))
                    top = [s for _, _, s in scored[:3]]
                except Exception:
                    pass

            time_list = ", ".join(s["time_label"] for s in top)
            tz_note = f" All times are in {office_tz_name} timezone." if office_tz_name else ""

            if exact_match:
                exact_label = top[0]["time_label"]
                match_note = (
                    f"The caller's requested time ({exact_label}) IS available. "
                    f"Confirm it with them and proceed to collect their details. "
                )
            else:
                match_note = (
                    f"The caller's requested time is NOT available. "
                    f"The closest available times are: {time_list}. "
                    f"Offer these alternatives. "
                )

            if provider_id_str:
                provider_available_slots = [
                    s for s in formatted_slots
                    if any(p["id"] == provider_id_str for p in s.get("available_providers", []))
                ]
                if not provider_available_slots and formatted_slots:
                    other_providers: set[str] = set()
                    for s in formatted_slots[:5]:
                        for p in s.get("available_providers", []):
                            other_providers.add(p["name"])
                    other_names = ", ".join(list(other_providers)[:2])
                    summary = (
                        f"The requested faculty member is fully booked on {friendly_date}, "
                        f"but {other_names} {'has' if len(other_providers) == 1 else 'have'} availability. "
                        f"Ask the caller if they'd like to see another faculty member instead. "
                        f"Available times ({office_tz_name}): {time_list}."
                    )
                else:
                    summary = (
                        f"{match_note}"
                        f"Available times on {friendly_date}: {time_list}.{tz_note} "
                        f"When they pick one, call book_appointment with the EXACT exact_slot_time "
                        f"string and provider_id. Do NOT construct or modify the time string yourself. "
                        f"Do NOT read JSON or field names aloud."
                    )
            else:
                summary = (
                    f"{match_note}"
                    f"Available times on {friendly_date}: {time_list}.{tz_note} "
                    f"When they pick one, call book_appointment with the EXACT exact_slot_time string. "
                    f"Do NOT construct or modify the time string yourself. "
                    f"Include provider_id if the caller chose a specific faculty member. "
                    f"Do NOT read JSON or field names aloud."
                )

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[Call %s] get_available_slots → %d slots in %.0fms (provider=%s)",
            call_id, len(formatted_slots), elapsed, provider_id_str or "any",
        )
        return {
            "summary_for_assistant": summary,
            "available_slots": formatted_slots,
            "date": date,
            "timezone": office_tz_name,
            "count": len(formatted_slots),
            "provider_filter": provider_id_str or None,
        }

    async def _get_week_slots(
        self, args: dict, tenant_ctx: Any, call_id: str, t0: float
    ) -> dict:
        """Return available slots for every remaining day this week (today→Sunday)."""
        import uuid as uuid_mod
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        from backend.services import native_scheduling
        from backend.services.calendar_service import _resolve_appointment_config

        appt_type = args.get("appointment_type", "consultation")
        specialty_filter = args.get("specialty", "") or args.get("subject", "") or None
        provider_id_str = args.get("provider_id", "") or None

        office_tz_name = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
        try:
            _tz = ZoneInfo(office_tz_name)
        except Exception:
            _tz = ZoneInfo(DEFAULT_TIMEZONE)

        today = datetime.now(_tz).date()
        days_to_sunday = 6 - today.weekday()  # Mon=0 … Sun=6
        date_range = [today + timedelta(days=i) for i in range(days_to_sunday + 1)]

        duration, max_conc = _resolve_appointment_config(appt_type, tenant_ctx)

        provider_uuid = None
        if provider_id_str:
            try:
                provider_uuid = uuid_mod.UUID(provider_id_str)
            except (ValueError, TypeError):
                pass

        holidays = (
            tenant_ctx.holidays
            if tenant_ctx and getattr(tenant_ctx, "holidays", None)
            else None
        )

        # Timezone abbreviation for time labels
        try:
            _tz_abbr = datetime.now(_tz).strftime("%Z")
        except Exception:
            _tz_abbr = ""

        days_result: list[dict] = []
        specialty_not_offered: str | None = None

        for d in date_range:
            date_str = d.strftime("%Y-%m-%d")
            friendly = d.strftime("%A, %B %d")

            result = await native_scheduling.get_provider_aware_slots(
                date_str=date_str,
                duration_minutes=duration,
                tenant_id=tenant_ctx.tenant_id if tenant_ctx else None,
                business_hours=tenant_ctx.business_hours if tenant_ctx else None,
                tz_name=office_tz_name,
                slot_capacity=max_conc,
                provider_id=provider_uuid,
                holidays=holidays,
                appointment_type=appt_type,
                specialty=specialty_filter,
            )

            # Subject not offered — provider-level, same for all days; short-circuit
            if result.get("specialty_filter") and not result.get("slots"):
                specialty_not_offered = result["specialty_filter"]
            elif result.get("subject_filter") and not result.get("slots"):
                specialty_not_offered = result["subject_filter"]
                break

            # Holiday — include in response so agent can explain the gap
            holiday_info = result.get("holiday")
            if holiday_info:
                days_result.append({
                    "date": date_str,
                    "day": friendly,
                    "closed": True,
                    "holiday": holiday_info.get("name", "holiday"),
                    "slots": [],
                    "count": 0,
                })
                continue

            # Format slots (cap at 5 per day to keep context lean)
            raw_slots = result.get("slots", [])
            formatted: list[dict] = []
            for slot_data in raw_slots[:5]:
                slot_time = slot_data.get("time", "")
                available_provs = slot_data.get("available_providers", [])
                display_time, display_date = _fmt_slot_display(slot_time, office_tz_name)
                time_label = display_time
                if _tz_abbr and display_time != slot_time:
                    time_label += f" {_tz_abbr}"
                formatted.append({
                    "time_label": time_label,
                    "display_time": display_time,
                    "display_date": display_date,
                    "exact_slot_time": slot_time,
                    "available_providers": available_provs,
                    "duration_minutes": slot_data.get("duration_minutes"),
                })

            days_result.append({
                "date": date_str,
                "day": friendly,
                "closed": False,
                "slots": formatted,
                "count": len(raw_slots),  # total before cap
            })

        # ── Build summary ─────────────────────────────────────────────────
        if specialty_not_offered:
            summary = (
                f"No faculty at this institute currently teach {specialty_not_offered!r}. "
                f"Do NOT say 'no slots available' or 'fully booked' or 'closed'. "
                f"Tell the caller: \u2018We don\u2019t currently offer {specialty_not_offered} coaching.\u2019 "
                f"Ask if they\u2019d like to book a demo for one of our other subjects, "
                f"or offer to have a counselor follow up."
            )
        else:
            open_days = [d for d in days_result if not d["closed"] and d["slots"]]
            if not open_days:
                summary = (
                    f"No available slots were found for any remaining day this week. "
                    f"Tell the caller the rest of this week appears to be fully booked. "
                    f"Offer to check next week or add them to the waitlist."
                )
            else:
                tz_note = f" (all times in {office_tz_name})" if office_tz_name else ""
                lines = []
                for day_info in open_days:
                    top_times = ", ".join(s["time_label"] for s in day_info["slots"][:3])
                    lines.append(f"  {day_info['day']}: {top_times}")
                summary = (
                    f"Available slots remaining this week{tz_note}:\n"
                    + "\n".join(lines)
                    + "\n\nAsk the caller which day and time works best. "
                    + "Once they choose, call book_appointment with the EXACT exact_slot_time "
                    + "and provider_id. Do NOT construct or modify the time string yourself."
                )

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[Call %s] get_week_slots → %d/%d days have slots in %.0fms",
            call_id,
            len([d for d in days_result if not d.get("closed") and d.get("slots")]),
            len(days_result),
            elapsed,
        )
        return {
            "summary_for_assistant": summary,
            "days": days_result,
            "days_checked": [d["date"] for d in days_result],
            "total_days_with_slots": len(
                [d for d in days_result if not d.get("closed") and d.get("slots")]
            ),
            "specialty_not_offered": bool(specialty_not_offered),
            "timezone": office_tz_name,
        }

    async def _book_appointment(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None, is_test: bool, t0: float
    ) -> dict:
        # ── Validate provider_id ──────────────────────────────────────────
        if not args.get("provider_id", "").strip():
            logger.warning("[Call %s] book_appointment rejected — no provider_id", call_id)
            return {
                "ok": False,
                "error": "missing_provider",
                "summary_for_assistant": (
                    "Provider is required — please ask the caller for their faculty "
                    "preference first, or assign any available faculty member."
                ),
            }

        # ── Validate required fields ──────────────────────────────────────
        required = {
            "caller_name": "the parent/caller's full name",
            "phone": "the caller's phone number",
            "slot_time": "a confirmed appointment time (call get_available_slots first)",
            "appointment_type": "the appointment type",
        }
        missing = []
        for field, desc in required.items():
            val = args.get(field, "")
            if not val or not str(val).strip():
                missing.append((field, desc))

        if missing:
            missing_list = "; ".join(f"{f} ({d})" for f, d in missing)
            logger.warning("[Call %s] book_appointment rejected — missing: %s",
                           call_id, [f for f, _ in missing])
            return {
                "ok": False,
                "error": "missing_required_fields",
                "missing": [f for f, _ in missing],
                "summary_for_assistant": (
                    f"Cannot book yet — missing: {missing_list}. "
                    f"Ask the caller ONLY for the missing items above in a natural sentence."
                ),
            }

        appt_type = args.get("appointment_type", "consultation")
        email = args.get("email", "") or ""

        provider_id_str = args.get("provider_id", "")
        provider_id = None
        provider_name = None
        if provider_id_str:
            try:
                from backend.services import provider_service
                import uuid
                provider_id = uuid.UUID(provider_id_str)
                provider = await provider_service.get_provider(provider_id)
                if provider:
                    provider_name = provider.get("name", "")
                    logger.info("[Call %s] Provider selected: %s (%s)", call_id, provider_name, provider_id)
            except Exception as prov_exc:
                logger.warning("[Call %s] Provider lookup failed: %s", call_id, prov_exc)

        caller_info = {
            "name": args.get("caller_name", ""),
            "phone": args.get("phone", ""),
            "email": email,
            "dob": args.get("dob", ""),
        }
        if session:
            session["caller_info"] = caller_info

        slot_time = args.get("slot_time", "")

        # ── Smart slot matching ──────────────────────────────────────────
        logger.info("[Call %s] LLM provided slot_time: '%s' — attempting smart match", call_id, slot_time)
        try:
            from dateutil import parser as dt_parser
            requested_dt = dt_parser.parse(slot_time)
            target_hour = requested_dt.hour
            target_minute = requested_dt.minute
            correct_date = requested_dt.replace(year=datetime.now(timezone.utc).year)
            date_str = correct_date.strftime("%Y-%m-%d")

            available = await calendar_service.get_available_slots(
                date_from=date_str,
                date_to=date_str,
                tenant_ctx=tenant_ctx,
                appointment_type_key=appt_type,
            )

            matched = False
            for real_slot in available:
                slot_dt = dt_parser.parse(real_slot)
                if slot_dt.hour == target_hour and slot_dt.minute == target_minute:
                    slot_time = real_slot
                    matched = True
                    logger.info("[Call %s] Exact slot match: %s", call_id, slot_time)
                    break

            if not matched:
                for real_slot in available:
                    slot_dt = dt_parser.parse(real_slot)
                    if slot_dt.hour == target_hour:
                        slot_time = real_slot
                        matched = True
                        logger.info("[Call %s] Hour-level slot match: %s", call_id, slot_time)
                        break

            if not matched:
                logger.warning("[Call %s] No slot match found — using original: %s", call_id, slot_time)
        except Exception as match_exc:
            logger.warning("[Call %s] Slot matching failed: %s — using original", call_id, match_exc)

        # ── Demo class checks (coaching institutes only) ──────────────────────
        if "demo" in appt_type.lower() and tenant_ctx:
            _demo_tenant_id = getattr(tenant_ctx, "tenant_id", None)
            _demo_kb = getattr(tenant_ctx, "knowledge_base", None) or {}
            _demo_max = _demo_kb.get("max_demo_classes_per_student")
            _demo_raw_phone = args.get("phone", "")

            try:
                from backend.services import caller_service as _cs_demo
                _demo_phone = _cs_demo._normalise_phone(_demo_raw_phone)
            except Exception:
                _demo_phone = _demo_raw_phone

            if _demo_tenant_id and _demo_phone:
                try:
                    from backend.database import async_session as _get_demo_session
                    from sqlalchemy import select as _sa_sel, func as _sa_fn, and_ as _sa_and
                    from backend.models.appointment import (
                        Appointment as _DemoAppt, AppointmentStatus as _DemoStatus,
                    )
                    from backend.models.caller import Caller as _DemoCaller
                    from dateutil import parser as _dtp_demo
                    from datetime import timedelta as _td_demo

                    # Resolve slot to UTC for window comparison
                    _demo_slot_dt = _dtp_demo.parse(slot_time)
                    if _demo_slot_dt.tzinfo is None:
                        from zoneinfo import ZoneInfo as _DemoZI
                        _demo_slot_dt = _demo_slot_dt.replace(
                            tzinfo=_DemoZI(tenant_ctx.timezone or DEFAULT_TIMEZONE)
                        )
                    _demo_slot_utc = _demo_slot_dt.astimezone(timezone.utc)

                    # Slot duration (for overlap window)
                    _demo_dur = 60
                    for _demo_at in (getattr(tenant_ctx, "appointment_types", None) or []):
                        if _demo_at.get("code") == appt_type:
                            _demo_dur = int(_demo_at.get("duration_minutes", 60))
                            break
                    _demo_slot_end_utc = _demo_slot_utc + _td_demo(minutes=_demo_dur)

                    # Shared base: this phone × this tenant × any demo type
                    _demo_base = [
                        _DemoAppt.client_phone == _demo_phone,
                        _DemoCaller.tenant_id == _demo_tenant_id,
                        _DemoAppt.appointment_type.ilike("%demo%"),
                    ]

                    async with _get_demo_session() as _demo_db:
                        # 1. Overlap check: existing CONFIRMED demo whose window
                        #    intersects [slot_start, slot_end).  Uses symmetric window
                        #    so a demo that starts before but ends after slot_start is caught.
                        _overlap_q = (
                            _sa_sel(_sa_fn.count())
                            .select_from(_DemoAppt)
                            .join(_DemoCaller, _DemoAppt.caller_id == _DemoCaller.id)
                            .where(
                                _sa_and(
                                    *_demo_base,
                                    _DemoAppt.status == _DemoStatus.CONFIRMED,
                                    # existing.start < new_end  AND  existing.start > new_start - duration
                                    _DemoAppt.scheduled_at < _demo_slot_end_utc,
                                    _DemoAppt.scheduled_at > _demo_slot_utc - _td_demo(minutes=_demo_dur),
                                )
                            )
                        )
                        _demo_overlap = (await _demo_db.execute(_overlap_q)).scalar() or 0
                        if _demo_overlap > 0:
                            logger.warning(
                                "[Call %s] Demo overlap blocked — phone=%s already has demo at %s",
                                call_id, _demo_phone, slot_time,
                            )
                            return {
                                "ok": False,
                                "error": "demo_overlap",
                                "summary_for_assistant": (
                                    "This student already has a demo class scheduled at that time. "
                                    "Inform them they cannot attend two demo sessions simultaneously "
                                    "and offer a different available time slot."
                                ),
                            }

                        # 2. Max demo count check
                        if _demo_max is not None:
                            try:
                                _demo_max_int = int(_demo_max)
                            except (ValueError, TypeError):
                                _demo_max_int = None

                            if _demo_max_int and _demo_max_int > 0:
                                _count_q = (
                                    _sa_sel(_sa_fn.count())
                                    .select_from(_DemoAppt)
                                    .join(_DemoCaller, _DemoAppt.caller_id == _DemoCaller.id)
                                    .where(
                                        _sa_and(
                                            *_demo_base,
                                            _DemoAppt.status.in_(
                                                [_DemoStatus.CONFIRMED, _DemoStatus.COMPLETED]
                                            ),
                                        )
                                    )
                                )
                                _demo_count = (await _demo_db.execute(_count_q)).scalar() or 0
                                if _demo_count >= _demo_max_int:
                                    logger.warning(
                                        "[Call %s] Demo limit reached — phone=%s has %d/%d demos",
                                        call_id, _demo_phone, _demo_count, _demo_max_int,
                                    )
                                    return {
                                        "ok": False,
                                        "error": "demo_limit_reached",
                                        "summary_for_assistant": (
                                            f"This student has already attended {_demo_count} demo "
                                            f"class(es), which is the maximum of {_demo_max_int} allowed. "
                                            "Politely let them know that no additional demo sessions "
                                            "are available for them. Offer to connect them with a "
                                            "counsellor who can discuss enrollment directly — use "
                                            "escalate_to_human to transfer them."
                                        ),
                                    }
                except Exception as _demo_chk_exc:
                    logger.warning(
                        "[Call %s] Demo class check failed (non-blocking): %s", call_id, _demo_chk_exc
                    )

        booking_notes = args.get("notes", "").strip() or None
        result = await calendar_service.book_appointment(
            caller_info=caller_info,
            start_time=slot_time,
            tenant_ctx=tenant_ctx,
            appointment_type_key=appt_type,
            provider_id=provider_id,
            is_test=is_test,
            notes=booking_notes,
        )

        if result and isinstance(result, dict) and result.get("status") == "CONFLICT":
            elapsed = (time.time() - t0) * 1000
            logger.warning(
                "[Call %s] Booking conflict — slot taken (provider=%s, %s) in %.0fms",
                call_id, provider_id, slot_time, elapsed,
            )
            return {
                "ok": False,
                "error": "slot_taken",
                "summary_for_assistant": (
                    "That slot was just booked by someone else. "
                    "Apologize, then offer the caller another available time."
                ),
            }

        if result:
            sms_sent = False
            try:
                scheduled_dt = datetime.fromisoformat(args.get("slot_time", slot_time))
                sms_service.send_confirmation(
                    caller_name=args.get("caller_name", ""),
                    phone=args.get("phone", ""),
                    appointment_type=args.get("appointment_type", "").replace("_", " ").title(),
                    scheduled_at=scheduled_dt,
                    tenant_ctx=tenant_ctx,
                    is_test=is_test,
                )
                sms_sent = True
            except Exception as sms_exc:
                logger.warning("[Call %s] SMS confirmation failed: %s", call_id, sms_exc)

            # ── Build coaching extra_data ────────────────────────────────
            coaching_extra: dict | None = None
            coaching_extra = {}
            for field in ("client_name", "grade", "board", "target_exam", "medium"):
                val = args.get(field, "")
                if val and str(val).strip():
                    coaching_extra[field] = str(val).strip()
            for field in ("candidate_type", "mode_preference"):
                val = args.get(field, "")
                if val and str(val).strip():
                    coaching_extra[field] = str(val).strip()
            if "attempt_number" in args and args["attempt_number"] is not None:
                try:
                    coaching_extra["attempt_number"] = int(args["attempt_number"])
                except (ValueError, TypeError):
                    pass
            if not coaching_extra:
                coaching_extra = None

            if not tenant_ctx:
                try:
                    from backend.services import caller_service
                    booking_uid = result.get("uid", "") if isinstance(result, dict) else ""
                    booking_id = result.get("id", "") if isinstance(result, dict) else ""
                    await caller_service.record_session(
                        client_phone=args.get("phone", ""),
                        appointment_type=args.get("appointment_type", ""),
                        scheduled_at=datetime.fromisoformat(slot_time),
                        cal_booking_uid=str(booking_uid),
                        cal_booking_id=str(booking_id),
                        client_name=args.get("caller_name", ""),
                        client_email=email,
                        dob=args.get("dob", ""),
                        tenant_id=None,
                        provider_id=provider_id,
                        extra_data=coaching_extra,
                    )
                    logger.info("[Call %s] ✓ Appointment recorded in DB (uid=%s)", call_id, booking_uid)
                except Exception as db_exc:
                    logger.warning("[Call %s] Caller/session DB upsert failed: %s", call_id, db_exc)
            else:
                booking_uid = result.get("uid", "") if isinstance(result, dict) else ""
                logger.info("[Call %s] ✓ Native booking already persisted (uid=%s)", call_id, booking_uid)
                # For real tenants, native scheduling created the caller row.
                # Write coaching extra_data separately if present.
                if coaching_extra:
                    try:
                        from backend.services import caller_service
                        await caller_service.upsert_caller(
                            name=args.get("caller_name", ""),
                            phone=args.get("phone", ""),
                            email=email,
                            dob=args.get("dob", ""),
                            appointment_type=args.get("appointment_type", ""),
                            tenant_id=tenant_ctx.tenant_id if hasattr(tenant_ctx, "tenant_id") else None,
                            extra_data=coaching_extra,
                        )
                        logger.info("[Call %s] ✓ Coaching extra_data written for %s", call_id, args.get("phone", ""))
                    except Exception as ed_exc:
                        logger.warning("[Call %s] Coaching extra_data write failed: %s", call_id, ed_exc)

            elapsed = (time.time() - t0) * 1000
            logger.info("[Call %s] book_appointment → SUCCESS in %.0fms: %s", call_id, elapsed, result)
            tenant_tz_name = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
            time_str, date_str = _fmt_slot_display(slot_time, tenant_tz_name)
            if not date_str:
                date_str = "the requested date"

            provider_msg = f" with {provider_name}" if provider_name else ""
            sms_note = "They'll receive an SMS confirmation shortly." if sms_sent else ""
            return {
                "success": True,
                "booking": result,
                "provider_name": provider_name,
                "summary_for_assistant": (
                    f"Booking confirmed for {time_str} on {date_str}{provider_msg}. "
                    f"Tell the caller their session is booked. {sms_note} "
                    f"Do NOT make up any details not provided. "
                    f"Just confirm the time, date{', and faculty member' if provider_name else ''}, "
                    f"wish them well, and ask if there's anything else."
                ),
            }

        elapsed = (time.time() - t0) * 1000
        logger.error("[Call %s] book_appointment → FAILED in %.0fms", call_id, elapsed)
        return {
            "success": False,
            "error": "Failed to create booking.",
            "summary_for_assistant": (
                "The booking could not be completed due to a technical issue. "
                "Apologize to the caller and offer to try again or check a different time. "
                "Do NOT make up reasons for the failure."
            ),
        }

    async def _reschedule_appointment(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None, t0: float
    ) -> dict:
        booking_uid = args.get("booking_uid", "")
        new_slot_time = args.get("new_slot_time", "")
        new_provider_id = args.get("provider_id", "") or None
        if not booking_uid or not new_slot_time:
            return {
                "success": False,
                "summary_for_assistant": (
                    "Cannot reschedule — I need the booking reference and a new time. "
                    "Ask the caller for these details."
                ),
            }

        # ── Demo reschedule overlap check ──────────────────────────────────────
        if tenant_ctx and getattr(tenant_ctx, "tenant_id", None):
            try:
                from backend.database import async_session as _get_rs_session
                from sqlalchemy import select as _sa_rs_sel, func as _sa_rs_fn, and_ as _sa_rs_and
                from backend.models.appointment import (
                    Appointment as _RsAppt, AppointmentStatus as _RsStatus,
                )
                from backend.models.caller import Caller as _RsCaller
                from dateutil import parser as _dtp_rs
                from datetime import timedelta as _td_rs

                async with _get_rs_session() as _rs_db:
                    # Look up the existing appointment to get phone + type
                    _rs_existing = (
                        await _rs_db.execute(
                            _sa_rs_sel(_RsAppt).where(_RsAppt.cal_booking_uid == booking_uid)
                        )
                    ).scalar_one_or_none()

                    if _rs_existing and "demo" in (_rs_existing.appointment_type or "").lower():
                        _rs_phone = _rs_existing.client_phone  # already stored normalised

                        _rs_new_dt = _dtp_rs.parse(new_slot_time)
                        if _rs_new_dt.tzinfo is None:
                            from zoneinfo import ZoneInfo as _RsZI
                            _rs_new_dt = _rs_new_dt.replace(
                                tzinfo=_RsZI(tenant_ctx.timezone or DEFAULT_TIMEZONE)
                            )
                        _rs_new_utc = _rs_new_dt.astimezone(timezone.utc)

                        _rs_dur = 60
                        for _rs_at in (getattr(tenant_ctx, "appointment_types", None) or []):
                            if _rs_at.get("code") == _rs_existing.appointment_type:
                                _rs_dur = int(_rs_at.get("duration_minutes", 60))
                                break
                        _rs_new_end_utc = _rs_new_utc + _td_rs(minutes=_rs_dur)

                        _rs_overlap_q = (
                            _sa_rs_sel(_sa_rs_fn.count())
                            .select_from(_RsAppt)
                            .join(_RsCaller, _RsAppt.caller_id == _RsCaller.id)
                            .where(
                                _sa_rs_and(
                                    _RsAppt.client_phone == _rs_phone,
                                    _RsCaller.tenant_id == tenant_ctx.tenant_id,
                                    _RsAppt.appointment_type.ilike("%demo%"),
                                    _RsAppt.status == _RsStatus.CONFIRMED,
                                    _RsAppt.cal_booking_uid != booking_uid,  # exclude current
                                    _RsAppt.scheduled_at < _rs_new_end_utc,
                                    _RsAppt.scheduled_at > _rs_new_utc - _td_rs(minutes=_rs_dur),
                                )
                            )
                        )
                        _rs_overlap = (await _rs_db.execute(_rs_overlap_q)).scalar() or 0
                        if _rs_overlap > 0:
                            logger.warning(
                                "[Call %s] Demo reschedule overlap blocked — phone=%s already has demo at %s",
                                call_id, _rs_phone, new_slot_time,
                            )
                            return {
                                "success": False,
                                "error": "demo_overlap",
                                "summary_for_assistant": (
                                    "This student already has another demo class at that time. "
                                    "Let them know and offer a different available time slot."
                                ),
                            }
            except Exception as _rs_demo_exc:
                logger.warning(
                    "[Call %s] Demo reschedule overlap check failed (non-blocking): %s",
                    call_id, _rs_demo_exc,
                )

        result = await calendar_service.reschedule_appointment(
            booking_uid=booking_uid,
            new_start_time=new_slot_time,
            tenant_ctx=tenant_ctx,
            provider_id=new_provider_id,
        )
        if result:
            try:
                from dateutil import parser as dt_parser
                new_dt = dt_parser.parse(new_slot_time)
                sms_service.send_reschedule(
                    caller_name=session.get("caller_info", {}).get("name", "") if session else "",
                    phone=session.get("caller_info", {}).get("phone", "") if session else "",
                    new_scheduled_at=new_dt,
                    appointment_type="Session",
                    tenant_ctx=tenant_ctx,
                    is_test=is_test,
                )
            except Exception as sms_exc:
                logger.warning("[Call %s] Reschedule SMS failed: %s", call_id, sms_exc)

            if not tenant_ctx:
                try:
                    from backend.database import async_session as get_async_session
                    from sqlalchemy import select as sa_select
                    from backend.models.appointment import Appointment as ApptModel
                    async with get_async_session() as db_session:
                        stmt = sa_select(ApptModel).where(ApptModel.cal_booking_uid == booking_uid)
                        db_result = await db_session.execute(stmt)
                        appt = db_result.scalar_one_or_none()
                        if appt:
                            appt.scheduled_at = datetime.fromisoformat(new_slot_time)
                            await db_session.commit()
                            logger.info("[Call %s] ✓ DB appointment rescheduled: %s", call_id, booking_uid)
                except Exception as db_exc:
                    logger.warning("[Call %s] DB reschedule update failed: %s", call_id, db_exc)
            else:
                logger.info("[Call %s] ✓ DB appointment already updated by native reschedule: %s", call_id, booking_uid)

            elapsed = (time.time() - t0) * 1000
            logger.info("[Call %s] reschedule → SUCCESS in %.0fms", call_id, elapsed)
            _reschedule_tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
            new_time_str, new_date_str = _fmt_slot_display(new_slot_time, _reschedule_tz)
            if not new_date_str:
                new_date_str = "the new date"
            return {
                "success": True,
                "reschedule": result,
                "summary_for_assistant": (
                    f"Session rescheduled to {new_time_str} on {new_date_str}. "
                    "Confirm the new time to the caller naturally and ask if there's anything else."
                ),
            }

        elapsed = (time.time() - t0) * 1000
        logger.error("[Call %s] reschedule → FAILED in %.0fms", call_id, elapsed)
        return {"success": False, "error": "Failed to reschedule."}

    async def _cancel_appointment(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None, t0: float
    ) -> dict:
        booking_uid = args.get("booking_uid", "")
        if not booking_uid:
            return {
                "success": False,
                "summary_for_assistant": (
                    "Cannot cancel — I need the booking reference. "
                    "Ask the caller for their booking details or phone number to look it up."
                ),
            }
        success = await calendar_service.cancel_appointment(
            booking_uid=booking_uid,
            reason=args.get("reason", ""),
            tenant_ctx=tenant_ctx,
        )
        if success:
            try:
                caller_phone = session.get("caller_info", {}).get("phone", "") if session else ""
                caller_name_for_sms = session.get("caller_info", {}).get("name", "") if session else ""
                if caller_phone:
                    sms_service.send_cancellation(
                        caller_name=caller_name_for_sms,
                        phone=caller_phone,
                        scheduled_at=datetime.now(timezone.utc),
                        tenant_ctx=tenant_ctx,
                        is_test=is_test,
                    )
            except Exception as sms_exc:
                logger.warning("[Call %s] Cancel SMS failed: %s", call_id, sms_exc)
            try:
                from backend.database import async_session as get_async_session
                from backend.models.appointment import Appointment as ApptModel, AppointmentStatus
                async with get_async_session() as db_session:
                    from sqlalchemy import select as sa_select
                    stmt = sa_select(ApptModel).where(ApptModel.cal_booking_uid == booking_uid)
                    db_result = await db_session.execute(stmt)
                    appt = db_result.scalar_one_or_none()
                    if appt:
                        appt.status = AppointmentStatus.CANCELLED
                        await db_session.commit()
                        logger.info("[Call %s] ✓ DB appointment cancelled: %s", call_id, booking_uid)
            except Exception as db_exc:
                logger.warning("[Call %s] DB cancel update failed: %s", call_id, db_exc)

        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] cancel → %s in %.0fms", call_id, success, elapsed)
        if success:
            return {
                "success": True,
                "summary_for_assistant": (
                    "Session cancelled successfully. "
                    "Let the caller know it's been cancelled and ask if there's anything else."
                ),
            }
        return {
            "success": False,
            "summary_for_assistant": (
                "I wasn't able to cancel that session — the booking may not exist or has already been cancelled. "
                "Apologize and offer to check their upcoming sessions or try again."
            ),
        }

    async def _escalate_to_human(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None
    ) -> dict:
        caller = session.get("caller_number", "unknown") if session else "unknown"
        sms_service.send_office_alert(
            reason=args.get("reason", "Caller requested human assistance"),
            caller_number=caller,
            tenant_ctx=tenant_ctx,
        )
        if session:
            session["current_state"] = "escalated"

        if tenant_ctx:
            transfer_dest = (
                (tenant_ctx.escalation_transfer_number or "").strip()
                or (tenant_ctx.escalation_phone or "").strip()
                or (tenant_ctx.business_phone or "").strip()
            )
        else:
            transfer_dest = (
                (settings.ESCALATION_TRANSFER_NUMBER or "").strip()
                or (settings.ESCALATION_PHONE_NUMBER or "").strip()
            )
        return {
            "success": True,
            "action": "transfer",
            "destination": transfer_dest,
        }

    def _send_callback_request(self, args: dict, tenant_ctx: Any) -> dict:
        sms_service.send_escalation_notification(
            caller_name=args.get("caller_name", ""),
            phone=args.get("phone", ""),
            tenant_ctx=tenant_ctx,
        )
        sms_service.send_office_alert(
            reason=f"Callback requested: {args.get('reason', 'N/A')}",
            caller_number=args.get("phone", ""),
            tenant_ctx=tenant_ctx,
        )
        return {"success": True, "message": "Callback request sent."}

    async def _lookup_caller_appointments(
        self, args: dict, tenant_ctx: Any, call_id: str, t0: float
    ) -> dict:
        from backend.services import caller_service
        phone = args.get("phone", "")
        if not phone:
            return {
                "ok": False,
                "summary_for_assistant": "I need the caller's phone number to look up their sessions.",
            }
        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
        tz_name = tenant_ctx.timezone if tenant_ctx else None
        history = await caller_service.get_caller_history(phone, tenant_id=tenant_id, tz_name=tz_name)
        if not history:
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"No record found for {phone}. "
                    "This might be a new caller. Ask if they'd like to schedule a new session."
                ),
            }
        upcoming = history.get("upcoming_appointments", [])
        caller_name = history["caller"]["name"]
        if not upcoming:
            return {
                "ok": True,
                "summary_for_assistant": (
                    f"{caller_name} has no upcoming sessions. "
                    "Ask if they'd like to schedule a new one."
                ),
                "caller_name": caller_name,
                "upcoming": [],
            }

        def _appt_line(a: dict) -> str:
            line = f"{a['type']} on {a['date']} at {a['time']}"
            if a.get("provider_name"):
                title = f" ({a['provider_title']})" if a.get("provider_title") else ""
                line += f" with {a['provider_name']}{title}"
            if a.get("duration_minutes"):
                line += f" ({a['duration_minutes']} min)"
            return line

        appt_lines = [_appt_line(a) for a in upcoming]
        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] lookup_caller_appointments → %d upcoming in %.0fms", call_id, len(upcoming), elapsed)
        return {
            "ok": True,
            "summary_for_assistant": (
                f"{caller_name} has {len(upcoming)} upcoming session(s): "
                + "; ".join(appt_lines) + ". "
                "Tell the caller about their session(s) naturally — include the faculty name and session details. "
                "If they want to reschedule, use the booking_uid with reschedule_appointment."
            ),
            "caller_name": caller_name,
            "upcoming": upcoming,
        }

    async def _add_to_waitlist(
        self, args: dict, tenant_ctx: Any, call_id: str, is_test: bool, t0: float
    ) -> dict:
        from backend.services import waitlist_service
        import uuid as uuid_mod

        wl_provider_uuid = None
        wl_provider_id_str = (args.get("provider_id") or "").strip()
        if wl_provider_id_str:
            try:
                wl_provider_uuid = uuid_mod.UUID(wl_provider_id_str)
            except (ValueError, TypeError):
                logger.warning("[Call %s] add_to_waitlist got invalid provider_id %r",
                               call_id, wl_provider_id_str)

        result = await waitlist_service.add_to_waitlist(
            tenant_id=tenant_ctx.tenant_id if tenant_ctx else None,
            client_name=args.get("caller_name", ""),
            client_phone=args.get("phone", ""),
            appointment_type=args.get("appointment_type", "consultation"),
            preferred_date=args.get("preferred_date", ""),
            preferred_time_start=args.get("preferred_time_start") or None,
            preferred_time_end=args.get("preferred_time_end") or None,
            provider_id=wl_provider_uuid,
            tenant_ctx=tenant_ctx,
            is_test=is_test,
        )
        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] add_to_waitlist → %s in %.0fms", call_id, result.get("status"), elapsed)
        return result

    async def _check_waitlist_status(
        self, args: dict, tenant_ctx: Any, call_id: str, t0: float
    ) -> dict:
        from backend.services import waitlist_service

        phone = args.get("phone", "")
        if not phone:
            return {
                "ok": False,
                "summary_for_assistant": "I need the caller's phone number to check their waitlist status.",
            }

        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
        if not tenant_id:
            return {"ok": False, "summary_for_assistant": "Unable to check waitlist — no tenant context."}

        result = await waitlist_service.check_waitlist_status(
            client_phone=phone,
            tenant_id=tenant_id,
        )
        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] check_waitlist_status → %d entries in %.0fms",
                    call_id, len(result.get("entries", [])), elapsed)
        return result

    async def _lookup_caller(
        self, args: dict, tenant_ctx: Any, call_id: str, session: dict | None, t0: float
    ) -> dict:
        from backend.services import caller_service

        phone = args.get("phone", "")
        caller_name = args.get("name", "")

        if not phone and not caller_name:
            return {
                "ok": False,
                "summary_for_assistant": "I need either the caller's phone number or name to look them up.",
            }

        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
        tz_name = tenant_ctx.timezone if tenant_ctx else None

        history = None
        if phone:
            history = await caller_service.get_caller_history(phone, tenant_id=tenant_id, tz_name=tz_name)

        if not history and caller_name:
            matches = await caller_service.get_caller_by_name(caller_name, tenant_id=tenant_id)
            if len(matches) > 1:
                elapsed = (time.time() - t0) * 1000
                match_lines = [f"- {m.name} (phone ending ...{m.phone[-4:]})" for m in matches]
                logger.info("[Call %s] lookup_caller → %d name matches for '%s' in %.0fms",
                            call_id, len(matches), caller_name, elapsed)
                return {
                    "ok": False,
                    "multiple_matches": True,
                    "match_count": len(matches),
                    "summary_for_assistant": (
                        f"Found {len(matches)} callers matching the name '{caller_name}': "
                        + "; ".join(match_lines) + ". "
                        "You already have the caller's phone number from caller-ID (in your system prompt). "
                        "Call lookup_caller again with the phone parameter to get the exact match. "
                        "If for some reason you don't have the phone, ask the caller to confirm it."
                    ),
                }
            elif len(matches) == 1:
                history = await caller_service.get_caller_history(
                    matches[0].phone, tenant_id=tenant_id, tz_name=tz_name,
                )

        if not history:
            elapsed = (time.time() - t0) * 1000
            logger.info("[Call %s] lookup_caller → not found in %.0fms (phone=%s, name=%s)",
                        call_id, elapsed, phone, caller_name)
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"No record found for {phone or caller_name}. "
                    "This appears to be a new caller. You will need to collect their "
                    "full name and reason for the session before booking."
                ),
            }

        p = history["caller"]
        upcoming = history.get("upcoming_appointments", [])
        past = history.get("past_appointments", [])
        last_visit = history.get("last_visit")
        months_since = history.get("months_since_last_visit")

        # Populate session["caller_info"] so reschedule/cancel SMS can use name+phone
        # without requiring a separate book_appointment call in the same session.
        if session is not None:
            session["caller_info"] = {
                "name": p.get("name", ""),
                "phone": p.get("phone", ""),
                "email": p.get("email", ""),
                "dob": p.get("dob", ""),
            }

        summary_parts = [f"Caller found: {p['name']}, phone {p['phone']}."]
        if p.get("dob"):
            summary_parts.append(f"DOB: {p['dob']}.")
        else:
            summary_parts.append("DOB: not on file — ask the caller.")
        if p.get("notes"):
            summary_parts.append(f"Notes: {p['notes']}.")
        summary_parts.append(f"Visit count: {p.get('visit_count', 0)}.")

        if last_visit:
            ago = f" ({months_since} months ago)" if months_since is not None else ""
            summary_parts.append(f"Last visit: {last_visit['type']} on {last_visit['date']}{ago}.")

        if upcoming:
            def _appt_summary(a: dict) -> str:
                line = f"{a['type']} on {a['date']} at {a['time']}"
                if a.get("provider_name"):
                    title = f" ({a['provider_title']})" if a.get("provider_title") else ""
                    line += f" with {a['provider_name']}{title}"
                return line
            appt_lines = [_appt_summary(a) for a in upcoming]
            summary_parts.append(f"Upcoming sessions: {'; '.join(appt_lines)}.")
        else:
            summary_parts.append("No upcoming sessions.")

        if past:
            past_lines = [f"{a['type']} on {a['date']} ({a['status']})" for a in past[:3]]
            summary_parts.append(f"Recent history: {'; '.join(past_lines)}.")

        summary_parts.append(
            "Use this data when speaking to the caller. Do NOT ask for info you already have. "
            "If DOB is missing, ask for it. If updating any info, use update_caller_info."
        )

        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] lookup_caller → found %s (%d upcoming, %d past) in %.0fms",
                    call_id, p['name'], len(upcoming), len(past), elapsed)
        return {
            "ok": True,
            "summary_for_assistant": " ".join(summary_parts),
            "caller": p,
            "upcoming_appointments": upcoming,
            "past_appointments": past,
            "last_visit": last_visit,
            "months_since_last_visit": months_since,
        }

    async def _update_caller_info(
        self, args: dict, tenant_ctx: Any, call_id: str, t0: float
    ) -> dict:
        from backend.services import caller_service

        phone = args.get("phone", "")
        if not phone:
            return {
                "ok": False,
                "summary_for_assistant": "I need the caller's phone number to update their record.",
            }

        tenant_id = tenant_ctx.tenant_id if tenant_ctx else None

        existing = await caller_service.get_caller_by_phone(phone, tenant_id=tenant_id)
        if not existing:
            return {
                "ok": True,
                "summary_for_assistant": (
                    f"This is a new caller — no record exists yet for {phone}. "
                    "That's perfectly normal. Their record will be created automatically when you book the session. "
                    "Continue with the booking as usual — do NOT mention any issue to the caller."
                ),
            }

        updated_fields = []
        from backend.database import async_session as get_async_session
        async with get_async_session() as db_session:
            from sqlalchemy import select as sa_select, and_
            from backend.models.caller import Caller

            filters = [Caller.phone == caller_service._normalise_phone(phone)]
            if tenant_id:
                filters.append(Caller.tenant_id == tenant_id)

            result = await db_session.execute(sa_select(Caller).where(and_(*filters)))
            caller_record = result.scalar_one_or_none()

            if not caller_record:
                return {"ok": False, "summary_for_assistant": "Caller record not found."}

            if args.get("name", "").strip():
                caller_record.name = args["name"].strip()
                updated_fields.append("name")
            if args.get("dob", "").strip():
                caller_record.date_of_birth = args["dob"].strip()
                updated_fields.append("date of birth")
            if args.get("email", "").strip():
                caller_record.email = args["email"].strip()
                updated_fields.append("email")
            if args.get("notes", "").strip():
                caller_record.notes = args["notes"].strip()
                updated_fields.append("notes")

            # ── Coaching extra_data fields ────────────────────────────────
            # Reassign (not mutate) so SQLAlchemy detects the JSONB change
            coaching_fields = {}
            for field in ("client_name", "grade", "board", "target_exam", "medium"):
                val = args.get(field, "")
                if val and str(val).strip():
                    coaching_fields[field] = str(val).strip()
            for field in ("candidate_type", "mode_preference"):
                val = args.get(field, "")
                if val and str(val).strip():
                    coaching_fields[field] = str(val).strip()
            if "attempt_number" in args and args["attempt_number"] is not None:
                try:
                    coaching_fields["attempt_number"] = int(args["attempt_number"])
                except (ValueError, TypeError):
                    pass
            if coaching_fields:
                caller_record.extra_data = {**(caller_record.extra_data or {}), **coaching_fields}
                updated_fields.extend(coaching_fields.keys())

            if not updated_fields:
                return {
                    "ok": False,
                    "summary_for_assistant": "No fields to update were provided. Specify at least one of: name, dob, email, notes, client_name, grade, board, target_exam, candidate_type, attempt_number, mode_preference, medium.",
                }

            await db_session.commit()

        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] update_caller_info → updated %s for %s in %.0fms",
                    call_id, updated_fields, phone, elapsed)
        return {
            "ok": True,
            "summary_for_assistant": (
                f"Updated {', '.join(updated_fields)} for {existing.name} ({phone}). "
                f"The changes are saved. Continue the conversation normally."
            ),
            "updated_fields": updated_fields,
        }

    async def _get_providers(
        self, args: dict, tenant_ctx: Any, call_id: str, t0: float
    ) -> dict:
        from backend.services import provider_service

        appt_type = args.get("appointment_type", "")
        if appt_type and tenant_ctx:
            providers = await provider_service.get_providers_for_appointment_type(
                tenant_ctx.tenant_id, appt_type,
            )
        elif tenant_ctx:
            providers = await provider_service.list_providers(tenant_ctx.tenant_id)
        else:
            providers = []

        if not providers:
            summary = (
                "This institute doesn't have individual faculty profiles configured. "
                "Any available faculty member can handle the session. "
                "Do not pass a provider_id when booking."
            )
        else:
            def _fmt_provider(p: dict) -> str:
                name = p["name"]
                title = p.get("title")
                return f"{name} ({title})" if title else name

            provider_list = ", ".join(_fmt_provider(p) for p in providers)
            summary = (
                f"Available faculty: {provider_list}. "
                f"Tell the caller which faculty members are available and ask if they have a preference. "
                f"When they choose, use that faculty member's 'id' from the providers list below in "
                f"get_available_slots and book_appointment. "
                f"If they say 'anyone is fine', pass '__auto__' as the provider_id to "
                f"auto-assign the best available faculty member."
            )

        elapsed = (time.time() - t0) * 1000
        logger.info("[Call %s] get_providers → %d providers in %.0fms", call_id, len(providers), elapsed)
        return {
            "summary_for_assistant": summary,
            "providers": providers,
            "count": len(providers),
        }
