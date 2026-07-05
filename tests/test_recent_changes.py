"""
Regression tests for recent feature additions:
  - send_no_show SMS template
  - Appointment PATCH triggers no-show / followup SMS on status transition
  - TenantContext exposes voice_config
  - escalate_to_human prefers escalation_transfer_number
  - llm_proxy emits Vapi transferCall for live phone hand-off
  - Google Maps URL field on tenant onboarding
  - Prompt has_escalation gating is now consistent

Run:  venv/bin/python tests/test_recent_changes.py
"""

import pytest
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

_passed = 0
_failed = 0
_total = 0


def _assert(condition: bool, desc: str):
    global _passed, _failed, _total
    _total += 1
    if condition:
        _passed += 1
        print(f"  ✅ {desc}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {desc}")


# ── Reusable mock tenant ────────────────────────────────────────────────────

TENANT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _make_mock_tenant(**overrides):
    t = MagicMock()
    t.id = overrides.get("id", TENANT_ID)
    t.slug = overrides.get("slug", "test-institute")
    t.business_name = overrides.get("business_name", "Test Institute")
    t.business_type = MagicMock(value=overrides.get("business_type_val", "fitness_studio"))
    t.business_phone = overrides.get("business_phone", "+15551112222")
    t.business_address = overrides.get("business_address", "100 Test St")
    t.timezone = overrides.get("timezone_val", "America/Chicago")
    t.demo_mode = overrides.get("demo_mode", True)
    t.agent_name = overrides.get("agent_name", "Riley")
    t.greeting_message = overrides.get("greeting_message", "Hello, this is Riley.")
    t.system_prompt_override = None
    t.twilio_account_sid = overrides.get("twilio_account_sid", "AC_test")
    t.twilio_auth_token = overrides.get("twilio_auth_token", "token_test")
    t.twilio_phone_number = overrides.get("twilio_phone_number", "+15550000000")
    t.escalation_phone = overrides.get("escalation_phone", "+15559990000")
    t.escalation_transfer_number = overrides.get("escalation_transfer_number", "")
    t.appointment_types = overrides.get("appointment_types", [
        {"code": "consultation", "name": "Consultation", "duration_minutes": 30},
    ])
    t.business_hours = None
    t.holidays = overrides.get("holidays", [])
    t.knowledge_base = overrides.get("knowledge_base", {})
    t.emergency_guidance = overrides.get("emergency_guidance", "")
    t.owner_name = "Prof. Test"
    t.owner_email = "admin@test.com"
    t.owner_phone = "+15551231234"
    t.plan = MagicMock(value="starter")
    t.status = MagicMock(value="ACTIVE")
    t.google_calendar_connected = False
    t.google_calendar_refresh_token = ""
    t.google_calendar_email = ""
    t.voice_config = overrides.get(
        "voice_config",
        {"provider": "11labs", "voiceId": "21m00Tcm4TlvDq8ikWAM"},
    )
    t.reminder_settings = overrides.get("reminder_settings", {"2h_enabled": True, "confirmation_reply_enabled": True})
    t.review_settings = overrides.get("review_settings", {"enabled": False, "google_review_link": "", "delay_hours": 24, "appointment_types": []})
    t.test_callers = overrides.get("test_callers", [])
    t.test_caller_phone = overrides.get("test_caller_phone", "")
    t.test_caller_phones = overrides.get("test_caller_phones", [])
    t.test_client_name = overrides.get("test_client_name", "Alex Johnson")
    t.test_client_names = overrides.get("test_client_names", ["Alex Johnson"])
    t.feature_twilio_enabled = overrides.get("feature_twilio_enabled", True)
    t.created_at = datetime.now(timezone.utc)
    t.updated_at = datetime.now(timezone.utc)
    return t


def _make_ctx(**overrides):
    from backend.services.tenant_service import _tenant_to_context
    return _tenant_to_context(_make_mock_tenant(**overrides))


# ══════════════════════════════════════════════════════════════════════════════
# TEST: TenantContext exposes voice_config
# ══════════════════════════════════════════════════════════════════════════════

def test_tenant_context_has_voice_config():
    print("\n── TenantContext.voice_config ──")
    ctx = _make_ctx(voice_config={"provider": "11labs", "voiceId": "voice_male_1"})
    _assert(hasattr(ctx, "voice_config"), "voice_config attr present")
    _assert(isinstance(ctx.voice_config, dict), "voice_config is a dict")
    _assert(ctx.voice_config.get("voiceId") == "voice_male_1", "voiceId carried through")
    # Default fallback when None
    ctx2 = _make_ctx(voice_config=None)
    _assert(ctx2.voice_config.get("provider") == "11labs", "default provider when None")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: send_no_show SMS template
# ══════════════════════════════════════════════════════════════════════════════

def test_send_no_show_sms():
    print("\n── sms_service.send_no_show ──")
    from backend.services import sms_service

    ctx = _make_ctx(
        business_name="Bright Future Coaching",
        timezone_val="America/New_York",
        demo_mode=True,
    )

    captured = {}

    def _capture(to, body, tenant_ctx=None):
        captured["to"] = to
        captured["body"] = body
        return True

    with patch.object(sms_service, "_send_sms", side_effect=_capture):
        result = sms_service.send_no_show(
            caller_name="John Doe",
            phone="+15551234567",
            appointment_type="Demo Class",
            scheduled_at=datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc),
            tenant_ctx=ctx,
        )

    _assert(result is True, "send_no_show returns True")
    body = captured.get("body", "")
    _assert("John Doe" in body, "caller name in body")
    _assert("Demo Class" in body, "appointment type in body")
    _assert("Bright Future Coaching" in body, "business name in body")
    _assert("missed" in body.lower() or "reschedule" in body.lower(),
            "reschedule prompt in body")


def test_send_no_show_skipped_when_no_twilio():
    print("\n── sms_service.send_no_show without credentials ──")
    from backend.services import sms_service

    ctx = _make_ctx(
        twilio_account_sid="",
        twilio_auth_token="",
        demo_mode=False,
    )
    # Force LOCAL_CHAT_MODE off + DEMO_MODE off so we go through the real path
    with patch.object(sms_service.settings, "LOCAL_CHAT_MODE", False):
        ok = sms_service.send_no_show(
            caller_name="X",
            phone="+15551231234",
            appointment_type="Visit",
            scheduled_at=datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc),
            tenant_ctx=ctx,
        )
    _assert(ok is False, "returns False when tenant has no Twilio credentials")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: SMS function inventory (all flows covered)
# ══════════════════════════════════════════════════════════════════════════════

def test_sms_flow_coverage():
    print("\n── sms_service flow coverage ──")
    from backend.services import sms_service

    required = [
        "send_confirmation",
        "send_cancellation",
        "send_reschedule",
        "send_reminder",
        "send_followup",
        "send_no_show",
        "send_waitlist_added",
        "send_waitlist_promoted",
        "send_waitlist_cancelled",
        "send_escalation_notification",
        "send_office_alert",
    ]
    for name in required:
        _assert(hasattr(sms_service, name) and callable(getattr(sms_service, name)),
                f"{name}() exists")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: Appointment PATCH triggers no-show / followup SMS
# ══════════════════════════════════════════════════════════════════════════════

def test_appointment_patch_triggers_no_show_sms():
    print("\n── Appointment PATCH → send_no_show on NO_SHOW transition ──")
    from backend.routes import appointments as appt_routes
    from backend.models.appointment import AppointmentStatus

    # Verify the route file references send_no_show / send_followup
    import inspect
    src = inspect.getsource(appt_routes)
    _assert("send_no_show" in src, "appointments.py calls sms_service.send_no_show")
    _assert("send_followup" in src, "appointments.py calls sms_service.send_followup")
    _assert("AppointmentStatus.NO_SHOW" in src, "checks AppointmentStatus.NO_SHOW")
    _assert("AppointmentStatus.COMPLETED" in src, "checks AppointmentStatus.COMPLETED")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: escalate_to_human prefers escalation_transfer_number
# ══════════════════════════════════════════════════════════════════════════════

def test_escalate_uses_transfer_number():
    print("\n── escalate_to_human prefers escalation_transfer_number ──")
    from backend.services import llm_service

    # Tenant with BOTH set — transfer_number should win
    ctx = _make_ctx(
        escalation_phone="+15550000001",
        escalation_transfer_number="+15550000002",
    )

    # Skip the SMS side-effect
    with patch.object(llm_service.sms_service, "send_office_alert", return_value=True):
        async def _run():
            return await llm_service._execute_tool(
                "test_call_id",
                "escalate_to_human",
                {"reason": "human plz", "caller_name": "X", "phone": "+15551231234"},
                tenant_ctx=ctx,
            )
        result = asyncio.run(_run())

    _assert(result.get("action") == "transfer", "action=transfer in tool result")
    _assert(result.get("destination") == "+15550000002",
            f"transfer_number wins over escalation_phone (got {result.get('destination')})")


def test_escalate_falls_back_to_escalation_phone():
    print("\n── escalate_to_human falls back to escalation_phone ──")
    from backend.services import llm_service

    ctx = _make_ctx(
        escalation_phone="+15550000001",
        escalation_transfer_number="",  # not set
    )

    with patch.object(llm_service.sms_service, "send_office_alert", return_value=True):
        async def _run():
            return await llm_service._execute_tool(
                "test_call_id",
                "escalate_to_human",
                {"reason": "help"},
                tenant_ctx=ctx,
            )
        result = asyncio.run(_run())

    _assert(result.get("destination") == "+15550000001",
            "escalation_phone used as fallback")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: llm_proxy transferCall SSE emission
# ══════════════════════════════════════════════════════════════════════════════

def test_llm_proxy_streams_transfer_call():
    print("\n── llm_proxy emits transferCall SSE for escalate_to_human ──")
    from backend.routes import llm_proxy as proxy

    import inspect
    src = inspect.getsource(proxy)

    # Verify the escalation→transferCall logic block exists in the stream func
    _assert("transferCall" in src, "transferCall string emitted")
    _assert("escalate_to_human" in src, "escalate_to_human handled")
    _assert("action\") == \"transfer\"" in src or "action') == 'transfer'" in src,
            "checks tool_result.action == 'transfer'")
    _assert("tool_calls" in src, "emits tool_calls delta")
    _assert("finish_reason=\"tool_calls\"" in src
            or "finish_reason='tool_calls'" in src,
            "emits finish_reason=tool_calls")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: Google Maps URL field on Tenant model + onboarding
# ══════════════════════════════════════════════════════════════════════════════

def test_tenant_model_has_google_maps_url():
    print("\n── Tenant model has google_maps_url column ──")
    from backend.models.tenant import Tenant

    columns = {c.name for c in Tenant.__table__.columns}
    _assert("google_maps_url" in columns, "Tenant.google_maps_url column present")


def test_tenant_register_request_has_google_maps_url():
    print("\n── TenantRegisterRequest accepts google_maps_url ──")
    # The frontend RegisterForm enforces this; verify backend accepts it on
    # the public tenant-register endpoint (whichever schema serves it).
    try:
        from backend.routes.tenants import TenantOnboardRequest
        fields = TenantOnboardRequest.model_fields
        if "google_maps_url" in fields:
            _assert(True, "TenantOnboardRequest exposes google_maps_url")
            return
    except Exception:
        pass

    # If onboard request is read-only of the maps URL, check tenants admin
    # update / register schemas.
    try:
        from backend.routes import tenants as tenants_routes
        import inspect
        src = inspect.getsource(tenants_routes)
        _assert("google_maps_url" in src,
                "google_maps_url referenced somewhere in tenants routes")
    except Exception as exc:
        _assert(False, f"could not import tenants routes: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# TEST: Prompt has_escalation gating
# ══════════════════════════════════════════════════════════════════════════════

def test_prompt_has_escalation_via_phone():
    print("\n── has_escalation triggers off escalation_phone alone ──")
    from backend.prompts.agent_prompt import build_system_prompt

    ctx = _make_ctx(
        business_type_val="fitness_studio",
        emergency_guidance="",
        escalation_phone="+15559990000",  # phone but no guidance text
    )
    prompt = build_system_prompt(tenant_ctx=ctx)
    _assert("transfer" in prompt.lower() or "escalate" in prompt.lower(),
            "escalation block present when escalation_phone is set")


def test_prompt_no_escalation_when_nothing_set():
    print("\n── No human-transfer block when tenant has nothing configured ──")
    from backend.prompts.agent_prompt import build_system_prompt

    ctx = _make_ctx(
        business_type_val="fitness_studio",
        emergency_guidance="",
        escalation_phone="",
        escalation_transfer_number="",
        business_phone="",
    )
    prompt = build_system_prompt(tenant_ctx=ctx)
    _assert("has not configured" in prompt.lower() or "not configured" in prompt.lower(),
            "prompt states escalation not configured when no channels set")


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════
# ── Waitlist: PromoteRequest accepts force + ConflictCheckRequest exists ────
def test_waitlist_promote_request_supports_force():
    print("\n── PromoteRequest exposes force + ConflictCheckRequest defined ──")
    from backend.routes.waitlist import PromoteRequest, ConflictCheckRequest

    req = PromoteRequest(scheduled_at="2026-05-28T10:30:00")
    _assert(req.force is False, "force defaults to False")

    req2 = PromoteRequest(scheduled_at="2026-05-28T10:30:00", force=True)
    _assert(req2.force is True, "force can be set to True")

    cc = ConflictCheckRequest(scheduled_at="2026-05-28T10:30:00")
    _assert(cc.scheduled_at.startswith("2026-05-28"), "ConflictCheckRequest accepts scheduled_at")


# ── Waitlist service: find_promote_conflicts + force-promote in source ──────
def test_waitlist_service_force_promote_wired():
    print("\n── waitlist_service exposes force-promote + find_promote_conflicts ──")
    from backend.services import waitlist_service
    import inspect

    _assert(hasattr(waitlist_service, "find_promote_conflicts"),
            "find_promote_conflicts function exists")

    src = inspect.getsource(waitlist_service.promote_to_appointment)
    _assert("force" in src, "promote_to_appointment accepts force flag")
    _assert("timedelta" in src or "offset" in src,
            "force-promote logic nudges the timestamp on CONFLICT")


# ── Waitlist routes: /check-conflicts endpoint mounted ──────────────────────
def test_waitlist_check_conflicts_route():
    print("\n── /api/waitlist/{id}/check-conflicts endpoint mounted ──")
    from backend.routes.waitlist import router

    paths = {route.path for route in router.routes}
    _assert("/api/waitlist/{entry_id}/check-conflicts" in paths,
            "check-conflicts route registered")
    _assert("/api/waitlist/{entry_id}/promote" in paths,
            "promote route still registered")


# ── Prompt update: says trainer not provider when speaking to caller ────────
def test_prompt_uses_trainer_terminology():
    print("\n── system prompt directs the AI to say 'trainer' to callers ──")
    from backend.prompts.agent_prompt import build_system_prompt

    prompt = build_system_prompt(tenant_ctx=None)
    _assert("trainer" in prompt.lower(),
            "prompt uses 'trainer' terminology for caller-facing speech")
    _assert("doctor" not in prompt.lower() and "physician" not in prompt.lower(),
            "prompt does NOT use medical terminology")


# ── Past-date guard: waitlist service refuses past preferred_date ───────────
def test_waitlist_rejects_past_date():
    print("\n── waitlist_service.add_to_waitlist refuses past dates ──")
    from backend.services import waitlist_service

    ctx = _make_ctx(timezone_val="America/Chicago")
    # 2000-01-01 is unambiguously in the past
    result = asyncio.run(waitlist_service.add_to_waitlist(
        tenant_id=ctx.tenant_id,
        client_name="Past Person",
        client_phone="+15550000001",
        appointment_type="consultation",
        preferred_date="2000-01-01",
        tenant_ctx=ctx,
    ))
    _assert(result.get("ok") is False, "ok=False for past date")
    _assert(result.get("error") == "past_date", "error code is past_date")
    summary = (result.get("summary_for_assistant") or "").lower()
    _assert("past" in summary or "already" in summary,
            "summary explains the date is in the past")
    _assert("waitlist" in summary or "upcoming" in summary,
            "summary instructs LLM not to waitlist / ask for upcoming day")


# ── Past-date guard: prompt mentions the rule ───────────────────────────────
def test_prompt_includes_past_date_guard():
    print("\n── system prompt forbids booking/waitlisting past dates ──")
    from backend.prompts.agent_prompt import build_system_prompt

    prompt = build_system_prompt(tenant_ctx=None)
    lower = prompt.lower()
    _assert("past date" in lower or "already passed" in lower or "in the past" in lower,
            "prompt mentions past-date concept")
    _assert("add_to_waitlist" in prompt, "prompt names add_to_waitlist tool in past-date rule")
    _assert("get_available_slots" in prompt, "prompt names get_available_slots tool in past-date rule")


# ── Past-date guard: get_available_slots refuses past dates ─────────────────
def test_get_available_slots_rejects_past_date():
    print("\n── _execute_tool('get_available_slots') refuses past dates ──")
    from backend.services import llm_service

    ctx = _make_ctx(timezone_val="America/Chicago")

    result = asyncio.run(llm_service._execute_tool(
        call_id="past-date-call",
        name="get_available_slots",
        args={"date": "2000-01-01", "appointment_type": "consultation"},
        tenant_ctx=ctx,
    ))
    _assert(result.get("ok") is False, "ok=False when date is in the past")
    _assert(result.get("error") == "past_date", "error code is past_date")
    _assert(result.get("count") == 0, "no slots returned for past date")
    summary = (result.get("summary_for_assistant") or "").lower()
    _assert("past" in summary or "already" in summary,
            "summary explains date already passed")
    _assert("add_to_waitlist" in summary or "waitlist" in summary,
            "summary tells LLM NOT to fall through to waitlist")


# ── Holidays: TenantContext exposes holidays list ───────────────────────────
def test_tenant_context_has_holidays():
    print("\n── TenantContext.holidays ──")
    holidays = [
        {"date": "2026-12-25", "name": "Christmas Day"},
        {"date": "2026-11-26", "name": "Thanksgiving"},
    ]
    ctx = _make_ctx(holidays=holidays)
    _assert(hasattr(ctx, "holidays"), "holidays attr present")
    _assert(isinstance(ctx.holidays, list), "holidays is a list")
    _assert(len(ctx.holidays) == 2, "two holidays carried through")
    dates = {h["date"] for h in ctx.holidays}
    _assert("2026-12-25" in dates, "Christmas date carried through")


# ── Holidays: waitlist service refuses holiday dates ────────────────────────
def test_waitlist_rejects_holiday():
    print("\n── waitlist_service.add_to_waitlist refuses holiday dates ──")
    from backend.services import waitlist_service
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    # Pick a date well in the future so the past-date guard doesn't fire.
    future = (_dt.now(_tz.utc) + _td(days=400)).date().isoformat()
    holidays = [{"date": future, "name": "Founder's Day"}]
    ctx = _make_ctx(timezone_val="America/Chicago", holidays=holidays)

    result = asyncio.run(waitlist_service.add_to_waitlist(
        tenant_id=ctx.tenant_id,
        client_name="Holiday Person",
        client_phone="+15550000002",
        appointment_type="consultation",
        preferred_date=future,
        tenant_ctx=ctx,
    ))
    _assert(result.get("ok") is False, "ok=False for holiday date")
    _assert(result.get("error") == "holiday", "error code is holiday")
    _assert(result.get("holiday", {}).get("name") == "Founder's Day",
            "holiday name included in result")
    summary = (result.get("summary_for_assistant") or "").lower()
    _assert("closed" in summary, "summary says office is closed")
    _assert("founder" in summary, "summary names the holiday")
    _assert("waitlist" in summary, "summary instructs LLM not to waitlist holidays")


# ── Holidays: get_available_slots refuses holiday dates ─────────────────────
def test_get_available_slots_rejects_holiday():
    print("\n── _execute_tool('get_available_slots') refuses holiday dates ──")
    from backend.services import llm_service
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    future = (_dt.now(_tz.utc) + _td(days=400)).date().isoformat()
    holidays = [{"date": future, "name": "Christmas Day"}]
    ctx = _make_ctx(timezone_val="America/Chicago", holidays=holidays)

    result = asyncio.run(llm_service._execute_tool(
        call_id="holiday-call",
        name="get_available_slots",
        args={"date": future, "appointment_type": "consultation"},
        tenant_ctx=ctx,
    ))
    _assert(result.get("ok") is False, "ok=False when date is a holiday")
    _assert(result.get("error") == "holiday", "error code is holiday")
    _assert(result.get("count") == 0, "no slots returned for holiday")
    _assert(result.get("holiday", {}).get("name") == "Christmas Day",
            "holiday name surfaced for the LLM")
    summary = (result.get("summary_for_assistant") or "").lower()
    _assert("closed" in summary, "summary says we are closed")
    _assert("christmas" in summary, "summary names the holiday")
    _assert("waitlist" in summary or "add_to_waitlist" in summary,
            "summary tells LLM NOT to offer waitlist for holiday")


# ── Holidays: native_scheduling short-circuits on holiday ────────────────────
def test_native_scheduling_short_circuits_on_holiday():
    print("\n── native_scheduling.get_provider_aware_slots returns holiday info ──")
    from backend.services import native_scheduling
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    future = (_dt.now(_tz.utc) + _td(days=400)).date().isoformat()
    holidays = [{"date": future, "name": "Independence Day"}]

    result = asyncio.run(native_scheduling.get_provider_aware_slots(
        date_str=future,
        duration_minutes=30,
        tenant_id=TENANT_ID,
        business_hours=None,
        tz_name="America/Chicago",
        provider_id=None,
        holidays=holidays,
    ))
    _assert(result.get("slots") == [], "no slots when date is a holiday")
    holiday = result.get("holiday") or {}
    _assert(holiday.get("name") == "Independence Day",
            "holiday name returned by slot generator")
    _assert(holiday.get("date") == future, "holiday date echoed back")


# ── Holidays: prompt mentions the holiday rule ──────────────────────────────
def test_prompt_includes_holiday_guidance():
    print("\n── system prompt forbids booking/waitlisting on configured holidays ──")
    from backend.prompts.agent_prompt import build_system_prompt

    prompt = build_system_prompt(tenant_ctx=None)
    lower = prompt.lower()
    _assert("holiday" in lower, "prompt mentions holiday concept")
    _assert("closed" in lower, "prompt mentions office closures")


# ── Holidays: tenant_service helpers ─────────────────────────────────────────
def test_tenant_service_holiday_helpers():
    print("\n── tenant_service holiday helpers (find / upcoming / normalize) ──")
    from backend.services import tenant_service
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    future1 = (_dt.now(_tz.utc) + _td(days=10)).date().isoformat()
    future2 = (_dt.now(_tz.utc) + _td(days=20)).date().isoformat()
    past1 = "2000-01-01"
    ctx = _make_ctx(
        timezone_val="America/Chicago",
        holidays=[
            {"date": future2, "name": "Holiday B"},
            {"date": future1, "name": "Holiday A"},
            {"date": past1, "name": "Old"},
        ],
    )

    found = tenant_service.find_holiday(ctx, future1)
    _assert(found is not None and found["name"] == "Holiday A",
            "find_holiday returns the matching entry")
    _assert(tenant_service.find_holiday(ctx, "1999-01-01") is None,
            "find_holiday returns None for non-matching date")

    upcoming = tenant_service.upcoming_holidays(ctx, limit=10)
    _assert(all(h["date"] >= _dt.now(_tz.utc).date().isoformat() for h in upcoming),
            "upcoming_holidays excludes past dates")
    upcoming_dates = [h["date"] for h in upcoming]
    _assert(upcoming_dates == sorted(upcoming_dates),
            "upcoming_holidays returns chronological order")

    # Normalization — dedupes by date and validates format
    raw = [
        {"date": "2026-12-25", "name": "Christmas"},
        {"date": "2026-12-25", "name": "Christmas Day"},  # dup
        {"date": "bad-format", "name": "Bogus"},
        {"date": "2026-11-26", "name": "Thanksgiving"},
    ]
    normalized = tenant_service._normalize_holidays(raw)
    _assert(len(normalized) == 2, "_normalize_holidays drops dups + invalid")
    dates_n = [h["date"] for h in normalized]
    _assert(dates_n == sorted(dates_n), "_normalize_holidays sorts ASC")


# ── Prompt update: explicit waitlist time-preference guidance ───────────────
def test_prompt_includes_waitlist_time_guidance():
    print("\n── waitlist directive in prompt covers time preferences ──")
    from backend.prompts.agent_prompt import build_system_prompt

    prompt = build_system_prompt(tenant_ctx=None)
    _assert("preferred_time_start" in prompt and "preferred_time_end" in prompt,
            "prompt instructs LLM to pass preferred_time_start/end")
    _assert("morning" in prompt.lower() and "afternoon" in prompt.lower(),
            "prompt explains how to convert vague windows (morning/afternoon)")


# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  REGRESSION TEST SUITE — RECENT CHANGES")
    print("=" * 70)

    tests = [
        test_tenant_context_has_voice_config,
        test_send_no_show_sms,
        test_send_no_show_skipped_when_no_twilio,
        test_sms_flow_coverage,
        test_appointment_patch_triggers_no_show_sms,
        test_escalate_uses_transfer_number,
        test_escalate_falls_back_to_escalation_phone,
        test_llm_proxy_streams_transfer_call,
        test_tenant_model_has_google_maps_url,
        test_tenant_register_request_has_google_maps_url,
        test_prompt_has_escalation_via_phone,
        test_prompt_no_escalation_when_nothing_set,
        test_waitlist_promote_request_supports_force,
        test_waitlist_service_force_promote_wired,
        test_waitlist_check_conflicts_route,
        test_prompt_uses_trainer_terminology,
        test_waitlist_rejects_past_date,
        test_prompt_includes_past_date_guard,
        test_get_available_slots_rejects_past_date,
        test_tenant_context_has_holidays,
        test_waitlist_rejects_holiday,
        test_get_available_slots_rejects_holiday,
        test_native_scheduling_short_circuits_on_holiday,
        test_prompt_includes_holiday_guidance,
        test_tenant_service_holiday_helpers,
        test_prompt_includes_waitlist_time_guidance,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as exc:
            global _failed, _total
            _total += 1
            _failed += 1
            print(f"  ❌ EXCEPTION in {test_fn.__name__}: {exc}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"  RESULTS: {_passed}/{_total} checks passed, {_failed} failed")
    print(f"  TESTS:   {len(tests)} test functions")
    print("=" * 70)

    if _failed > 0:
        sys.exit(1)
    else:
        print("\n  ✅ ALL REGRESSION TESTS PASSED\n")


if __name__ == "__main__":
    main()
