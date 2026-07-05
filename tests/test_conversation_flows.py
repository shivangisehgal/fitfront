"""
Conversation flow simulator.

Exercises the LLM proxy end-to-end against a real Ollama backend, but with
calendar / Twilio tool execution mocked. Validates every common caller
interaction so we don't have to discover bugs through real Vapi calls.

Run:  venv/bin/python tests/test_conversation_flows.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI

from backend.prompts.agent_prompt import build_system_prompt
from backend.routes import llm_proxy
from backend.services.llm_service import TOOLS


# ─── Mock tool executor ──────────────────────────────────────────────────────
# Returns the same shape as the real _execute_tool, without hitting external APIs.

# Dynamically generate mock slots relative to today so tests don't break
# when the date changes.
from datetime import datetime as _dt, timedelta as _td

_today = _dt.now()
_today_str = _today.strftime("%Y-%m-%d")
_tomorrow_str = (_today + _td(days=1)).strftime("%Y-%m-%d")
_day_after_str = (_today + _td(days=2)).strftime("%Y-%m-%d")

# Find next Sunday (closed day) and next Tuesday (open day with slots)
_next_sunday = _today + _td(days=(6 - _today.weekday()) % 7 or 7)
_next_sunday_str = _next_sunday.strftime("%Y-%m-%d")
# Next Tuesday from today
_next_tuesday = _today + _td(days=(1 - _today.weekday()) % 7 or 7)
_next_tuesday_str = _next_tuesday.strftime("%Y-%m-%d")
# Next Monday from today
_next_monday = _today + _td(days=(0 - _today.weekday()) % 7 or 7)
_next_monday_str = _next_monday.strftime("%Y-%m-%d")
# If today IS Monday, use today
if _today.weekday() == 0:
    _next_monday_str = _today_str

def _make_slots(date_str, hours):
    return [f"{date_str}T{h:02d}:00:00.000-05:00" for h in hours]

MOCK_SLOTS_BY_DATE = {
    _today_str: _make_slots(_today_str, [9, 10, 14]),
    _tomorrow_str: _make_slots(_tomorrow_str, [9, 10, 14]),
    _next_tuesday_str: _make_slots(_next_tuesday_str, [9, 11, 15]),
    _day_after_str: _make_slots(_day_after_str, [8, 13]),
    _next_monday_str: _make_slots(_next_monday_str, [9, 10, 14]),
    _next_sunday_str: [],  # Sunday: closed
}

# Always ensure Sunday returns empty even if dates overlap
for k, v in list(MOCK_SLOTS_BY_DATE.items()):
    try:
        if _dt.strptime(k, "%Y-%m-%d").weekday() == 6:
            MOCK_SLOTS_BY_DATE[k] = []
    except ValueError:
        pass


TOOL_CALL_LOG = []  # captured per-flow for debugging


async def mock_execute_tool(name, args, tenant_ctx=None):
    TOOL_CALL_LOG.append((name, dict(args)))
    if name == "get_available_slots":
        date = args.get("date") or "2026-05-04"
        slots = MOCK_SLOTS_BY_DATE.get(date, [])
        formatted = []
        from dateutil import parser as dt_parser
        for s in slots:
            dt = dt_parser.parse(s)
            formatted.append({
                "time_label": dt.strftime("%I:%M %p").lstrip("0"),
                "exact_slot_time": s,
            })
        try:
            friendly = dt_parser.parse(date).strftime("%A, %B %d")
        except Exception:
            friendly = date
        if not formatted:
            summary = (
                f"There are no available appointment slots on {friendly}. "
                f"Tell the caller politely that day is fully booked or closed, "
                f"and offer to check a different day. Do NOT read this message verbatim."
            )
        else:
            time_list = ", ".join(s["time_label"] for s in formatted[:3])
            summary = (
                f"Available times on {friendly}: {time_list}. "
                f"Offer these to the caller in natural conversation. "
                f"Do NOT read JSON or field names aloud."
            )
        return {
            "summary_for_assistant": summary,
            "available_slots": formatted,
            "date": date,
            "count": len(formatted),
        }

    if name == "book_appointment":
        # Mirror the same validation logic as the real _execute_tool
        required = {
            "caller_name": "the caller's full name",
            "phone": "the caller's phone number",
            "dob": "the caller's date of birth",
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
            return {
                "ok": False,
                "error": "missing_required_fields",
                "missing": [f for f, _ in missing],
                "summary_for_assistant": (
                    f"Cannot book yet — missing: {missing_list}. "
                    f"Do NOT tell the caller the booking is confirmed. "
                    f"Ask the caller ONLY for the missing items in a natural sentence."
                ),
            }
        return {
            "ok": True,
            "booking_id": "MOCK-12345",
            "summary_for_assistant": (
                f"Booking confirmed for {args.get('caller_name')} at {args.get('slot_time')}. "
                f"Tell the caller briefly that the booking is confirmed and they will get an SMS."
            ),
        }

    if name == "escalate_to_human":
        return {
            "ok": True,
            "summary_for_assistant": "Transfer initiated. Tell the caller briefly to please hold.",
        }

    if name == "lookup_caller_appointments":
        phone = args.get("phone", "")
        # Mock: known returning caller
        if "555-1234" in phone or "5551234" in phone:
            return {
                "ok": True,
                "summary_for_assistant": (
                    "Sarah Johnson has 1 upcoming appointment: Follow-up Visit on Monday, May 11, 2026 at 10:00 AM. "
                    "Tell the caller about their appointment naturally. "
                    "If they want to reschedule, use the booking_uid with reschedule_appointment."
                ),
                "caller_name": "Sarah Johnson",
                "upcoming": [{
                    "type": "Follow-up Visit",
                    "date": "Monday, May 11, 2026",
                    "time": "10:00 AM",
                    "booking_uid": "MOCK-UID-789",
                }],
            }
        return {
            "ok": False,
            "summary_for_assistant": f"No record found for {phone}. Ask if they'd like to schedule a new appointment.",
        }

    if name == "reschedule_appointment":
        return {
            "ok": True,
            "summary_for_assistant": (
                "Appointment rescheduled successfully. "
                "Tell the caller their appointment has been moved to the new time."
            ),
        }

    return {"ok": False, "error": f"Unknown tool: {name}"}


# ─── Test harness ────────────────────────────────────────────────────────────

class FlowResult:
    def __init__(self, name):
        self.name = name
        self.checks = []      # list of (label, passed, detail)
        self.transcript = []  # list of (role, content)

    def check(self, label, passed, detail=""):
        self.checks.append((label, passed, detail))

    @property
    def passed(self):
        return all(c[1] for c in self.checks)


async def run_flow(name, turns, system_prompt, model="llama3.2:latest", caller_context=None):
    """
    Run one multi-turn conversation. `turns` is a list of dicts:
      {"user": str, "checks": [(label, predicate(reply)) ...]}
    If caller_context is provided, builds a caller-aware system prompt.
    Returns FlowResult.
    """
    result = FlowResult(name)
    client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

    # If caller context is provided, rebuild prompt with it
    if caller_context:
        system_prompt = build_system_prompt(caller_context=caller_context)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant",
         "content": "Thank you for calling. This is your AI assistant. How can I help you today?"},
    ]

    for turn in turns:
        user_msg = turn["user"]
        messages.append({"role": "user", "content": user_msg})
        result.transcript.append(("user", user_msg))

        TOOL_CALL_LOG.clear()
        # Use the actual proxy logic with mocked tool execution
        with patch.object(llm_proxy, "_execute_tool", side_effect=mock_execute_tool):
            t0 = time.time()
            reply = await llm_proxy._call_with_tools(client, model, list(messages), t0)
            elapsed_ms = (time.time() - t0) * 1000

        # Capture tool calls made this turn
        turn_tool_calls = list(TOOL_CALL_LOG)
        if turn_tool_calls:
            tool_summary = "; ".join(f"{n}({json.dumps(a)})" for n, a in turn_tool_calls)
            result.transcript.append(("tools", tool_summary))

        # Apply tool-call checks if specified
        for label, predicate in turn.get("tool_checks", []):
            try:
                passed = predicate(turn_tool_calls)
                result.check(f"{name} | tool:{label}", passed,
                             f"tool_calls={turn_tool_calls}")
            except Exception as exc:
                result.check(f"{name} | tool:{label}", False, f"check raised: {exc}")

        # Add the assistant reply to history (matching what the real flow keeps)
        messages.append({"role": "assistant", "content": reply})
        result.transcript.append(("assistant", reply))

        # Run all checks for this turn
        for label, predicate in turn.get("checks", []):
            try:
                passed = predicate(reply)
                result.check(f"{name} | {label}", passed,
                             f"reply={reply[:120]!r}, time={elapsed_ms:.0f}ms")
            except Exception as exc:
                result.check(f"{name} | {label}", False, f"check raised: {exc}")

        # Latency check
        result.check(f"{name} | latency<15s ({user_msg[:30]!r})",
                     elapsed_ms < 15000,
                     f"{elapsed_ms:.0f}ms")

    return result


# ─── Mock caller contexts for returning-caller flows ────────────────────────

MOCK_RETURNING_CALLER = {
    "caller": {
        "name": "Sarah Johnson",
        "phone": "+15125551234",
        "dob": "03/15/1985",
        "is_new": False,
        "visit_count": 4,
        "preferred_type": "follow_up",
        "notes": None,
    },
    "upcoming_appointments": [
        {
            "type": "Follow-up Visit",
            "date": "Monday, May 11, 2026",
            "time": "10:00 AM",
            "booking_uid": "MOCK-UID-789",
        }
    ],
    "past_appointments": [
        {"type": "Follow-up Visit", "date": "November 10, 2025", "status": "COMPLETED"},
        {"type": "Consultation", "date": "May 5, 2025", "status": "COMPLETED"},
    ],
    "last_visit": {"type": "Follow-up Visit", "date": "November 10, 2025"},
    "months_since_last_visit": 6,
}

MOCK_RETURNING_CALLER_NO_UPCOMING = {
    "caller": {
        "name": "John Smith",
        "phone": "+15125559876",
        "dob": "05/01/1990",
        "is_new": False,
        "visit_count": 2,
        "preferred_type": "consultation",
        "notes": "Prefers morning sessions",
    },
    "upcoming_appointments": [],
    "past_appointments": [
        {"type": "Consultation", "date": "December 15, 2025", "status": "COMPLETED"},
    ],
    "last_visit": {"type": "Consultation", "date": "December 15, 2025"},
    "months_since_last_visit": 5,
}


# ─── Predicates (helpers for readable checks) ────────────────────────────────

def is_natural(reply: str) -> bool:
    """Reply is natural language: non-empty, no JSON-like markers."""
    if not reply or not reply.strip():
        return False
    bad = ["{", "}", '"available_slots"', '"exact_slot_time"',
           '"summary_for_assistant"', "field", "JSON"]
    return not any(b in reply for b in bad)


def is_short(reply: str, max_chars: int = 350) -> bool:
    return len(reply or "") <= max_chars


def mentions_any(reply: str, keywords: list[str]) -> bool:
    r = (reply or "").lower()
    return any(k.lower() in r for k in keywords)


def mentions_none(reply: str, keywords: list[str]) -> bool:
    r = (reply or "").lower()
    return not any(k.lower() in r for k in keywords)


# ─── The flows ───────────────────────────────────────────────────────────────

def all_flows():
    return [
        # Flow 1: simple greeting
        ("Greeting", [
            {"user": "Hi.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("short", is_short),
                 ("no_tool_leak", lambda r: mentions_none(r, ["get_available_slots", "tool_call", "JSON"])),
                 ("no_date_dump", lambda r: mentions_none(r, ["2026-05", "T09:00"])),
                 ("no_escalation_leak",
                  lambda r: mentions_none(r, ["connect you with", "team members", "please hold", "transfer"])),
             ]},
        ]),

        # Flow 2: hours question — must answer from KB, no tool
        ("Hours", [
            {"user": "What are your hours?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_hours", lambda r: mentions_any(r, ["8", "monday", "friday"])),
                 ("no_tool_leak", lambda r: mentions_none(r, ["get_available_slots", "tool_call"])),
             ]},
        ]),

        # Flow 3: pricing question
        ("Pricing", [
            {"user": "How much is a consultation?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_price", lambda r: mentions_any(r, ["50", "100", "$"])),
             ]},
        ]),

        # Flow 4: anxiety — empathetic answer
        ("Anxiety", [
            {"user": "I'm really nervous about coming in.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("empathy", lambda r: mentions_any(r, ["common", "comfortable", "understand", "help", "relax", "anxiety", "gentle"])),
             ]},
        ]),

        # Flow 5: scheduling — check tool call → natural response
        ("Schedule_Tuesday", [
            {"user": "Do you have any slots Tuesday?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_a_time", lambda r: mentions_any(r, ["9", "11", "3", "am", "pm", "morning", "afternoon"])),
                 ("no_tool_leak", lambda r: mentions_none(r, ["get_available_slots", "exact_slot_time", "summary_for_assistant"])),
                 ("no_json_braces", lambda r: "{" not in r and "}" not in r),
             ]},
        ]),

        # Flow 6: Sunday closed — must reject naturally
        ("Schedule_Sunday", [
            {"user": f"Can I book on Sunday, {_next_sunday.strftime('%B %d')}?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_unavailable", lambda r: mentions_any(r, ["sorry", "closed", "no availability", "not available", "unavailable", "another", "different", "sunday"])),
                 ("no_tool_leak", lambda r: mentions_none(r, ["summary_for_assistant", "available_slots", "get_available_slots"])),
             ]},
        ]),

        # Flow 7: emergency — urgent pain
        ("Emergency", [
            {"user": "I'm in really bad pain and I need to be seen right away.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("acknowledges_pain", lambda r: mentions_any(r, ["sorry", "pain", "understand", "help", "emergency", "same day", "urgent"])),
             ]},
        ]),

        # Flow: location / parking
        ("Location", [
            {"user": "Where are you located?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_address", lambda r: mentions_any(r, ["oak", "austin", "78701", "address"])),
             ]},
        ]),

        # Flow: services for kids
        ("Children", [
            {"user": "Do you treat children?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("affirmative", lambda r: mentions_any(r, ["yes", "all ages", "absolutely", "we do"])),
             ]},
        ]),

        # Flow: wants tomorrow → date corrector should resolve correctly
        ("Schedule_Tomorrow", [
            {"user": "Got any time tomorrow morning?",
             "tool_checks": [
                 ("tomorrow_date_correct",
                  lambda calls: any(
                      n == "get_available_slots" and a.get("date") == _tomorrow_str
                      for n, a in calls
                  )),
             ],
             "checks": [
                 ("natural_reply", is_natural),
                 ("mentions_a_time", lambda r: mentions_any(r, ["9", "10", "8", "am", "morning"])),
             ]},
        ]),

        # Flow: explicit human request → should escalate
        ("RequestHuman", [
            {"user": "Can I just speak to a real person please?",
             "tool_checks": [
                 ("escalates",
                  lambda calls: any(n == "escalate_to_human" for n, _ in calls)),
             ],
             "checks": [
                 ("natural_reply", is_natural),
                 ("brief_acknowledgment",
                  lambda r: mentions_any(r, ["hold", "transfer", "moment", "connect", "of course", "sure"])),
             ]},
        ]),

        # ─── TIER 1: Caller Recognition & Returning Caller Flows ─────────

        # Flow: returning caller — agent should greet by name, NOT ask for info
        ("ReturningCaller_Greeting", [
            {"user": "Hi, I'd like to schedule a follow-up.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("knows_name_or_context", lambda r: mentions_any(r, ["Sarah", "welcome back", "follow", "schedule", "happy to help"])),
                 ("doesnt_ask_name", lambda r: mentions_none(r, ["what is your name", "may I have your name", "can I get your name"])),
             ]},
        ], MOCK_RETURNING_CALLER),

        # Flow: returning caller mentions upcoming appointment
        ("ReturningCaller_ExistingAppt", [
            {"user": "Hi, I'm calling about my upcoming appointment.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("references_appointment", lambda r: mentions_any(r, [
                     "follow", "may 11", "monday", "10", "upcoming",
                 ])),
             ]},
        ], MOCK_RETURNING_CALLER),

        # Flow: returning caller with no upcoming — should suggest scheduling
        ("ReturningCaller_NoUpcoming", [
            {"user": "Hi there.",
             "checks": [
                 ("natural_reply", is_natural),
                 # LLM sometimes says "John" and sometimes "Hi there! Welcome back"
                 ("knows_name_or_greeting", lambda r: mentions_any(r, ["John", "welcome", "back", "help", "how"])),
             ]},
        ], MOCK_RETURNING_CALLER_NO_UPCOMING),

        # Flow: returning caller wants to reschedule
        ("ReturningCaller_Reschedule", [
            {"user": "I need to move my appointment to a different day.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("acknowledges_reschedule", lambda r: mentions_any(r, [
                     "reschedule", "move", "change", "different", "may 11", "follow", "which day", "when",
                 ])),
             ]},
        ], MOCK_RETURNING_CALLER),

        # Flow: returning caller — pre-filled booking (doesn't ask for DOB/phone again)
        ("ReturningCaller_QuickBook", [
            {"user": "Can I schedule an appointment for Tuesday?",
             "checks": [
                 ("natural_reply", is_natural),
                 ("offers_times", lambda r: mentions_any(r, ["9", "11", "3", "am", "pm", "morning", "afternoon"])),
                 ("doesnt_ask_dob", lambda r: mentions_none(r, ["date of birth", "DOB", "when were you born"])),
                 ("doesnt_ask_phone", lambda r: mentions_none(r, ["phone number", "what's your number"])),
             ]},
        ], MOCK_RETURNING_CALLER),

        # ─── Original multi-turn booking flow ────────────────────────────

        # Flow 9: multi-turn booking
        # Critical: turn 1 must NOT falsely claim a booking is confirmed
        # because no caller info or chosen slot has been provided yet.
        ("Booking_Multiturn", [
            {"user": "I'd like to book an appointment for Monday.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("offers_times_or_asks_info",
                  lambda r: mentions_any(r, ["9", "10", "2", "am", "pm", "name", "date of birth", "phone"])),
                 ("no_false_confirm",
                  lambda r: mentions_none(r, ["confirmed", "is booked", "you're booked", "you are booked", "appointment is set"])),
                 ("no_json", lambda r: "{" not in r),
             ],
             "tool_checks": [
                 ("monday_date_correct",
                  lambda calls: any(
                      n == "get_available_slots" and a.get("date") == _next_monday_str
                      for n, a in calls
                  )),
             ]},
            {"user": "10 AM works. My name is John Smith, DOB May 1 1990, phone 512-555-1234.",
             "checks": [
                 ("natural_reply", is_natural),
                 ("confirms_or_asks_more",
                  lambda r: mentions_any(r, ["confirm", "booked", "book", "scheduled", "got it", "monday", "see you"])),
                 ("no_email_ask",
                  lambda r: mentions_none(r, ["email address", "your email", "what is your email"])),
                 ("no_wrong_day_label",
                  # The model often says "Tuesday May 4" — May 4 is Monday
                  lambda r: not (
                      ("tuesday" in r.lower() and "may 4" in r.lower())
                      or ("tuesday" in r.lower() and "may 04" in r.lower())
                  )),
             ]},
        ]),
    ]


# ─── Runner ──────────────────────────────────────────────────────────────────

async def main():
    print("=" * 78)
    print("  CONVERSATION FLOW VALIDATION")
    print(f"  System prompt size: {len(build_system_prompt())} chars")
    print("=" * 78)

    sp = build_system_prompt()
    flows = all_flows()
    results = []

    for flow_tuple in flows:
        # Support (name, turns) or (name, turns, caller_context)
        name = flow_tuple[0]
        turns = flow_tuple[1]
        caller_ctx = flow_tuple[2] if len(flow_tuple) > 2 else None

        print(f"\n▸ Running flow: {name}" + (" [returning caller]" if caller_ctx else ""))
        try:
            result = await run_flow(name, turns, sp, caller_context=caller_ctx)
        except Exception as exc:
            print(f"   FLOW CRASHED: {exc}")
            r = FlowResult(name)
            r.check(f"{name} | crash", False, str(exc))
            results.append(r)
            continue

        for role, msg in result.transcript:
            if role == "user":
                indicator = "🧑"
            elif role == "tools":
                indicator = "🔧"
            else:
                indicator = "🤖"
            print(f"   {indicator} {role:>9}: {msg[:240]}")

        for label, passed, detail in result.checks:
            sym = "✓" if passed else "✗"
            print(f"     {sym} {label}")
            if not passed:
                print(f"        ↳ {detail}")
        results.append(result)

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    total = sum(len(r.checks) for r in results)
    passed = sum(1 for r in results for c in r.checks if c[1])
    failed_flows = [r.name for r in results if not r.passed]

    print(f"  Checks passed: {passed} / {total}")
    print(f"  Flows passed:  {sum(1 for r in results if r.passed)} / {len(results)}")
    if failed_flows:
        print(f"  Failing flows: {', '.join(failed_flows)}")
    print("=" * 78)

    return 0 if not failed_flows else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
