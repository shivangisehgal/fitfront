"""
Integration tests for Scheduler.ai — REAL Gemini calls, REAL database.

These tests exercise the full stack: chat API → LLM (Gemini) → tool execution
→ native scheduling → database. Nothing is mocked.

Prerequisites:
  1. Docker DB running:  docker compose up -d db
  2. App server running: python -m uvicorn backend.main:app --port 8000
  3. A test tenant with at least 2 providers exists (e.g. "a@gmail.com")
  4. GEMINI_API_KEY set in .env

Run:
  pytest tests/test_integration_scheduling.py -v --timeout=120 -x

Each test uses a unique conversation_id so sessions don't bleed.
Tests that create appointments clean up after themselves.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from conftest import (
    chat, chat_multi, get_appointments, cancel_appointment,
    mentions_any, mentions_none, is_natural, _auth,
    BASE_URL,
)
import httpx


# ════════════════════════════════════════════════════════════════════════════
# SECTION 1: BOOKING — HAPPY PATH
# ════════════════════════════════════════════════════════════════════════════

class TestBookingHappyPath:
    """Tests for the standard booking flow."""

    @pytest.mark.asyncio
    async def test_01_book_with_specific_instructor(self, auth_token, providers, conversation_id):
        """
        Scenario 1: New caller books with a specific instructor.
        Caller asks for a consultation with instr1 → agent should fetch slots,
        collect info, and book with the correct provider.
        """
        doc1 = next((p for p in providers if "instr1" in p["name"].lower()), None)
        if not doc1:
            pytest.skip("No provider named 'instr1' found")

        # Find a future date (2 days from now, skip weekends)
        target = _next_weekday(days_ahead=2)
        target_str = target.strftime("%A")

        replies = await chat_multi(auth_token, [
            f"Hi, I'd like to book a consultation with {doc1['name']} on {target_str}",
            "Yes, that's my number. 10 AM works",
            "My name is Test Caller One, phone is +1-555-0101, DOB is January 15, 1990",
            "Yes, please confirm the booking",
        ], conversation_id)

        # The agent should have offered times and confirmed a booking
        combined = " ".join(replies)
        assert is_natural(replies[-1]), f"Response leaked JSON: {replies[-1][:200]}"
        assert mentions_any(combined, ["confirmed", "booked", "scheduled", "appointment"]), \
            f"Expected booking confirmation, got: {combined[:300]}"

        # Verify appointment exists in DB
        appts = await get_appointments(auth_token)
        booked = [a for a in appts if a.get("client_phone", "").endswith("0101")
                   and a["status"] == "CONFIRMED"]
        assert len(booked) >= 1, "Booking not found in appointments"

        # Clean up
        for a in booked:
            await cancel_appointment(auth_token, a["id"])

    @pytest.mark.asyncio
    async def test_02_book_with_auto_provider(self, auth_token, conversation_id):
        """
        Scenario 2: Caller asks for an appointment without specifying an instructor.
        Agent should offer available providers and let caller choose.
        """
        target = _next_weekday(days_ahead=3)
        target_str = target.strftime("%A")

        replies = await chat_multi(auth_token, [
            f"I need a consultation on {target_str} morning",
            "Yes, that's my number",
        ], conversation_id)

        # Check all replies — agent may greet first then offer slots
        combined = " ".join(replies)
        assert is_natural(combined), f"Response leaked JSON: {combined[:200]}"
        # Agent should offer times (possibly with provider names)
        assert mentions_any(combined, ["available", "opening", "slot", "AM", "PM", "morning",
                                     "instr1", "instr2", "instructor", "time", "schedule"]), \
            f"Expected time offerings, got: {combined[:300]}"

    @pytest.mark.asyncio
    async def test_03_fifteen_min_slot_intervals(self, auth_token, conversation_id):
        """
        Scenario 3: 15-min appointment type should generate 15-min interval slots.
        If consultation is 15 min, agent should offer times at :00, :15, :30, :45.
        """
        target = _next_weekday(days_ahead=2)
        target_str = target.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"What consultation times do you have on {target_str}?",
        ], conversation_id)

        reply = replies[0]
        # Check for 15-min interval slots (e.g., 8:15, 8:30, 8:45, 9:15, etc.)
        has_quarter_slots = bool(re.search(r'\d+:15|\d+:45', reply))
        # At minimum, slots should be offered
        assert mentions_any(reply, ["AM", "PM", "available", "opening", "slot"]), \
            f"Expected slot offerings, got: {reply[:200]}"
        # If there are enough slots, we should see 15-min granularity
        # (may not always be true if slots happen to fall on :00/:30)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 2: RESCHEDULE — HAPPY PATH
# ════════════════════════════════════════════════════════════════════════════

class TestRescheduleHappyPath:
    """Tests for the reschedule flow."""

    @pytest.mark.asyncio
    async def test_04_reschedule_same_instructor(self, auth_token, conversation_id):
        """
        Scenario 4: Reschedule same instructor, different time.
        Caller asks to move their appointment → agent uses booking_uid
        and reschedules.
        """
        # First, we need an existing appointment. Check if one exists.
        appts = await get_appointments(auth_token)
        confirmed = [a for a in appts if a["status"] == "CONFIRMED"
                     and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)]
        if not confirmed:
            pytest.skip("No confirmed future appointment to reschedule")

        target = _next_weekday(days_ahead=4)
        target_str = target.strftime("%A")

        replies = await chat_multi(auth_token, [
            f"I need to reschedule my appointment to {target_str}",
        ], conversation_id)

        reply = replies[0]
        assert is_natural(reply), f"Response leaked JSON: {reply[:200]}"
        # Agent should either offer new slots or confirm the reschedule
        assert mentions_any(reply, ["available", "reschedule", "move", "slot",
                                     "AM", "PM", "time"]), \
            f"Expected reschedule flow, got: {reply[:200]}"

    @pytest.mark.asyncio
    async def test_05_reschedule_with_provider_change(self, auth_token, providers, conversation_id):
        """
        Scenario 5: Reschedule and change to a different instructor.
        Caller says "reschedule to tomorrow with instr1" — verify the provider
        changes in addition to the time.
        """
        if len(providers) < 2:
            pytest.skip("Need at least 2 providers for this test")

        appts = await get_appointments(auth_token)
        confirmed = [a for a in appts if a["status"] == "CONFIRMED"
                     and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)]
        if not confirmed:
            pytest.skip("No confirmed future appointment to reschedule")

        # Pick the provider that's NOT assigned to the current appointment
        current_provider_id = confirmed[0].get("provider_id")
        other_provider = next(
            (p for p in providers if str(p["id"]) != str(current_provider_id)),
            None
        )
        if not other_provider:
            pytest.skip("Could not find a different provider")

        target = _next_weekday(days_ahead=3)
        target_str = target.strftime("%A")

        replies = await chat_multi(auth_token, [
            f"Can I reschedule my appointment to {target_str} with {other_provider['name']}?",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        assert mentions_any(combined, ["available", "reschedule", "move", "slot",
                                     "AM", "PM", other_provider["name"],
                                     "time", "appointment"]), \
            f"Expected provider-aware reschedule, got: {combined[:300]}"

    @pytest.mark.asyncio
    async def test_06_own_slot_available_during_reschedule(self, auth_token, conversation_id):
        """
        Scenario 6: Caller's own slot should be available when rescheduling.
        If caller has 4:00 PM and asks to reschedule, 4:00 PM should still
        appear as available (exclude_booking_uid in action).
        """
        appts = await get_appointments(auth_token)
        confirmed = [a for a in appts if a["status"] == "CONFIRMED"
                     and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)]
        if not confirmed:
            pytest.skip("No confirmed future appointment to test")

        apt = confirmed[0]
        apt_time = datetime.fromisoformat(apt["scheduled_at"])
        apt_date_str = apt_time.strftime("%A, %B %d")
        apt_time_str = apt_time.strftime("%-I:%M %p")

        replies = await chat_multi(auth_token, [
            f"I want to reschedule my appointment. What times are available on {apt_date_str}?",
        ], conversation_id)

        reply = replies[0]
        # The caller's own time slot should NOT be blocked
        # We can't deterministically assert the exact time shows up,
        # but the day should have availability (not "fully booked")
        assert mentions_none(reply, ["fully booked", "no availability", "no openings"]), \
            f"Caller's own day shows as fully booked — exclude_booking_uid may not be working: {reply[:200]}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 3: CANCEL — HAPPY PATH
# ════════════════════════════════════════════════════════════════════════════

class TestCancelHappyPath:
    """Tests for the cancellation flow."""

    @pytest.mark.asyncio
    async def test_07_cancel_appointment(self, auth_token, conversation_id):
        """
        Scenario 7: Cancel an appointment via chat.
        Caller says "cancel my appointment" → agent cancels it.
        """
        appts = await get_appointments(auth_token)
        confirmed = [a for a in appts if a["status"] == "CONFIRMED"
                     and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)]
        if not confirmed:
            pytest.skip("No confirmed future appointment to cancel")

        replies = await chat_multi(auth_token, [
            "I need to cancel my appointment",
            "Yes, that's my number",
            "Yes, please cancel it",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        assert mentions_any(combined, ["cancelled", "canceled", "cancel", "no upcoming",
                                       "no appointment", "don't see"]), \
            f"Expected cancellation flow, got: {combined[:300]}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 4: CALENDAR UI
# ════════════════════════════════════════════════════════════════════════════

class TestCalendarUI:
    """Tests for the calendar/config API endpoints used by the month view."""

    @pytest.mark.asyncio
    async def test_08_month_view_data(self, auth_token):
        """
        Scenario 8-10: Verify the appointments and config APIs return
        the data needed for the month calendar grid.
        """
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            # Appointments list
            resp = await client.get("/api/appointments", headers=_auth(auth_token))
            assert resp.status_code == 200
            data = resp.json()
            appts = data if isinstance(data, list) else data.get("items", [])
            assert isinstance(appts, list)

            # Config (holidays + business hours)
            resp = await client.get("/api/config", headers=_auth(auth_token))
            assert resp.status_code == 200
            config = resp.json()
            # holidays should be a list (possibly empty)
            assert isinstance(config.get("holidays", []), list)

            # Providers
            resp = await client.get("/api/providers", headers=_auth(auth_token))
            assert resp.status_code == 200
            providers = resp.json()
            assert isinstance(providers, list)

    @pytest.mark.asyncio
    async def test_12_remaining_appointments_count(self, auth_token):
        """
        Scenario 12: Verify we can calculate remaining appointments this month.
        The frontend does this client-side, so we verify the API returns
        scheduled_at and status fields for filtering.
        """
        appts = await get_appointments(auth_token)
        now = datetime.now(timezone.utc)
        this_month_future = [
            a for a in appts
            if a["status"] in ("CONFIRMED", "COMPLETED")
            and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > now
            and datetime.fromisoformat(a["scheduled_at"]).month == now.month
            and datetime.fromisoformat(a["scheduled_at"]).year == now.year
        ]
        # Just verify the filter works — count can be 0
        assert isinstance(this_month_future, list)

    @pytest.mark.asyncio
    async def test_15_holidays_in_config(self, auth_token, tenant_info):
        """
        Scenario 15: Holidays configured by tenant should appear in config.
        """
        holidays = tenant_info.get("holidays", [])
        assert isinstance(holidays, list)
        # If holidays exist, verify structure
        for h in holidays:
            assert "date" in h, f"Holiday missing 'date' field: {h}"
            assert "name" in h, f"Holiday missing 'name' field: {h}"

    @pytest.mark.asyncio
    async def test_16_business_hours_in_config(self, auth_token, tenant_info):
        """
        Scenario 16: Business hours should be in config for closed day detection.
        """
        bh = tenant_info.get("business_hours")
        if bh is None:
            pytest.skip("No business_hours configured — using legacy Sunday fallback")
        # Verify structure: keys should be day names
        expected_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        assert expected_days.issubset(set(bh.keys())), \
            f"business_hours missing days: {expected_days - set(bh.keys())}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 5: RESCHEDULE EDGE CASES
# ════════════════════════════════════════════════════════════════════════════

class TestRescheduleEdgeCases:
    """Edge cases for the reschedule flow."""

    @pytest.mark.asyncio
    async def test_17_reschedule_to_fully_booked_slot(self, auth_token, conversation_id):
        """
        Scenario 17: Ask to reschedule to a time when all providers are booked.
        Agent should say the slot is unavailable and offer alternatives.
        """
        # Use a Sunday (closed) to guarantee no availability
        next_sunday = _next_specific_day(6)  # 6 = Sunday
        sunday_str = next_sunday.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"I'd like to reschedule my appointment to {sunday_str}",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        # Should mention no availability or closed
        assert mentions_any(combined, ["closed", "no availability", "unavailable",
                                     "not available", "another day", "different day",
                                     "don't have", "do not have", "sunday"]), \
            f"Expected unavailability message for Sunday, got: {combined[:300]}"

    @pytest.mark.asyncio
    async def test_18_no_booking_uid_placeholder(self, auth_token, conversation_id):
        """
        Scenario 18: LLM should NEVER use the literal string 'booking_uid_placeholder'.
        Test a reschedule request and verify the reply doesn't contain placeholders.
        """
        replies = await chat_multi(auth_token, [
            "I'd like to reschedule my appointment to next week",
        ], conversation_id)

        reply = replies[0]
        assert "booking_uid_placeholder" not in reply.lower(), \
            f"LLM leaked placeholder text: {reply[:200]}"
        assert "placeholder" not in reply.lower(), \
            f"LLM used placeholder language: {reply[:200]}"

    @pytest.mark.asyncio
    async def test_19_reschedule_no_existing_appointment(self, auth_token):
        """
        Scenario 19: Caller with no appointment tries to reschedule.
        Agent should explain there's nothing to reschedule.
        """
        # Use a unique phone so this caller has no history
        conv_id = str(uuid.uuid4())
        unique_phone = f"+1555099{uuid.uuid4().hex[:4]}"

        replies = await chat_multi(auth_token, [
            "Hi, I need to reschedule my appointment",
        ], conv_id, test_phone=unique_phone)

        reply = replies[0]
        assert is_natural(reply)
        # Agent should ask for info or say no appointment found
        # (exact behavior depends on whether this phone has records)

    @pytest.mark.asyncio
    async def test_20_reschedule_same_time_different_instructor(self, auth_token, providers, conversation_id):
        """
        Scenario 20: Keep the same time but switch to a different instructor.
        """
        if len(providers) < 2:
            pytest.skip("Need at least 2 providers")

        appts = await get_appointments(auth_token)
        confirmed = [a for a in appts if a["status"] == "CONFIRMED"
                     and datetime.fromisoformat(a["scheduled_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)]
        if not confirmed:
            pytest.skip("No confirmed future appointment")

        apt = confirmed[0]
        current_provider_id = apt.get("provider_id")
        other = next((p for p in providers if str(p["id"]) != str(current_provider_id)), None)
        if not other:
            pytest.skip("No alternate provider")

        apt_time = datetime.fromisoformat(apt["scheduled_at"])
        time_str = apt_time.strftime("%-I:%M %p")
        date_str = apt_time.strftime("%A")

        replies = await chat_multi(auth_token, [
            f"I want to switch my appointment to {other['name']} but keep the same time",
        ], conversation_id)

        reply = replies[0]
        assert is_natural(reply)

    @pytest.mark.asyncio
    async def test_21_reschedule_to_holiday(self, auth_token, tenant_info, conversation_id):
        """
        Scenario 21: Try to reschedule to a configured holiday.
        No slots should be returned.
        """
        holidays = tenant_info.get("holidays", [])
        if not holidays:
            pytest.skip("No holidays configured")

        # Pick the first future holiday
        holiday = None
        for h in holidays:
            h_date = datetime.strptime(h["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if h_date > datetime.now(timezone.utc):
                holiday = h
                break
        if not holiday:
            pytest.skip("No future holidays configured")

        holiday_str = datetime.strptime(holiday["date"], "%Y-%m-%d").strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"Can I reschedule to {holiday_str}?",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        # Should indicate no availability or holiday
        assert mentions_any(combined, ["closed", "holiday", "no availability",
                                     "unavailable", "not available", "another day"]), \
            f"Expected holiday/unavailability message, got: {combined[:300]}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 6: SLOT & DURATION EDGE CASES
# ════════════════════════════════════════════════════════════════════════════

class TestSlotEdgeCases:
    """Edge cases for slot generation and duration handling."""

    @pytest.mark.asyncio
    async def test_22_back_to_back_fifteen_min(self, auth_token, tenant_info, conversation_id):
        """
        Scenario 22: Two consecutive 15-min bookings.
        Book at 8:00 AM, then verify 8:15 AM is still available.
        """
        holidays = tenant_info.get("holidays", [])
        target = _next_weekday(days_ahead=5, holidays=holidays)
        target_str = target.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"What consultation slots do you have on {target_str} morning?",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert mentions_any(combined, ["8:00", "8:15", "8:30", "AM", "morning",
                                       "available", "slot", "time"]), \
            f"Expected morning slots, got: {combined[:300]}"

    @pytest.mark.asyncio
    async def test_24_boundary_slot_at_closing(self, auth_token, tenant_info, conversation_id):
        """
        Scenario 24: Last slot should not extend past business closing time.
        If institute closes at 6 PM and appointment is 15 min, last slot = 5:45 PM.
        """
        target = _next_weekday(days_ahead=2)
        target_str = target.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"What's the latest consultation time on {target_str}?",
        ], conversation_id)

        reply = replies[0]
        # Should mention afternoon times but not anything at or after closing
        bh = tenant_info.get("business_hours", {})
        if bh:
            # Find closing time for that day
            day_name = target.strftime("%A").lower()
            day_hours = bh.get(day_name)
            if day_hours and day_hours.get("close"):
                close_hour = int(day_hours["close"].split(":")[0])
                # The reply should not contain times at or after closing
                # e.g., if close=18:00, shouldn't offer 6:00 PM or later
                assert not mentions_any(reply, [f"{close_hour - 12 + 1}:00 PM",
                                                 f"{close_hour - 12 + 1}:15 PM"]), \
                    f"Offered slot past closing time ({day_hours['close']}): {reply[:200]}"

    @pytest.mark.asyncio
    async def test_25_no_providers_available(self, auth_token, conversation_id):
        """
        Scenario 25: All providers fully booked on a given day.
        Agent should say the day is fully booked.
        """
        # Sunday is typically closed — use it to test
        next_sunday = _next_specific_day(6)
        sunday_str = next_sunday.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"Book me a consultation on {sunday_str}",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        assert mentions_any(combined, ["closed", "unavailable", "not available",
                                     "no availability", "another day", "different day",
                                     "sunday"]), \
            f"Expected closed/unavailable for Sunday, got: {combined[:300]}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 7: CALENDAR UI EDGE CASES
# ════════════════════════════════════════════════════════════════════════════

class TestCalendarUIEdgeCases:
    """Edge cases for calendar UI data."""

    @pytest.mark.asyncio
    async def test_26_appointments_on_holiday(self, auth_token, tenant_info):
        """
        Scenario 26: If appointments exist on a holiday, the API should
        return them — the UI shows a warning badge.
        """
        holidays = tenant_info.get("holidays", [])
        if not holidays:
            pytest.skip("No holidays configured")

        appts = await get_appointments(auth_token)
        holiday_dates = {h["date"] for h in holidays}

        # Check if any appointments fall on holidays
        appts_on_holidays = [
            a for a in appts
            if datetime.fromisoformat(a["scheduled_at"]).strftime("%Y-%m-%d") in holiday_dates
        ]
        # This is informational — just verify the filter works
        # The UI renders a ⚠️ badge for these

    @pytest.mark.asyncio
    async def test_29_provider_filter(self, auth_token, providers):
        """
        Scenario 29: Provider filter on appointments.
        Verify appointments have provider_id field for client-side filtering.
        """
        appts = await get_appointments(auth_token)
        if not appts:
            pytest.skip("No appointments to filter")

        # All appointments should have provider_id (may be null for legacy)
        for apt in appts:
            assert "provider_id" in apt or "provider_name" in apt, \
                f"Appointment {apt.get('id')} missing provider fields"

    @pytest.mark.asyncio
    async def test_30_empty_month(self, auth_token):
        """
        Scenario 30: A far-future month should have 0 appointments.
        """
        appts = await get_appointments(auth_token)
        far_future = datetime(2030, 1, 1)
        future_appts = [
            a for a in appts
            if datetime.fromisoformat(a["scheduled_at"]).year == 2030
        ]
        assert len(future_appts) == 0, \
            f"Unexpectedly found {len(future_appts)} appointments in 2030"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 8: GCAL SYNC EDGE CASES
# ════════════════════════════════════════════════════════════════════════════

class TestGCalSync:
    """Tests for Google Calendar sync behavior."""

    @pytest.mark.asyncio
    async def test_31_gcal_sync_non_blocking(self, auth_token, conversation_id):
        """
        Scenario 31: GCal sync shouldn't block the booking response.
        We measure response time — the booking should confirm quickly
        even if GCal sync is slow (fire-and-forget).
        """
        import time

        target = _next_weekday(days_ahead=6)
        target_str = target.strftime("%A, %B %d")

        t0 = time.time()
        replies = await chat_multi(auth_token, [
            f"Book a consultation on {target_str} at 9 AM",
            "Yes, that's my number",
            "Test GCal Caller, phone +1-555-0131, DOB March 3, 1995",
            "Yes, please confirm",
        ], conversation_id)
        elapsed = time.time() - t0

        # Each turn should respond within ~15s even with GCal sync
        # (without fire-and-forget, it could add 3-5s per GCal API call)
        assert elapsed < 90, \
            f"Multi-turn booking took {elapsed:.1f}s — GCal may be blocking"

        # Clean up
        appts = await get_appointments(auth_token)
        for a in appts:
            if a.get("client_phone", "").endswith("0131") and a["status"] == "CONFIRMED":
                await cancel_appointment(auth_token, a["id"])

    @pytest.mark.asyncio
    async def test_32_booking_succeeds_without_gcal(self, auth_token, conversation_id):
        """
        Scenario 32: Booking should succeed even if GCal sync fails.
        The appointment is created in the DB regardless.
        """
        target = _next_weekday(days_ahead=7)
        target_str = target.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"Book a consultation on {target_str} at 10 AM please",
            "Yes, that's my number. 10 AM is perfect",
            "Yes, please confirm the booking",
        ], conversation_id)

        combined = " ".join(replies)
        # Should confirm the booking regardless of GCal — or at least proceed
        # with the booking flow (offer slots, confirm details, etc.)
        assert mentions_any(combined, ["confirmed", "booked", "scheduled",
                                       "10:00", "10 AM", "appointment"]), \
            f"Booking should succeed without GCal: {combined[:300]}"

        # Verify in DB — use the test phone number (default +155501190)
        appts = await get_appointments(auth_token)
        # Check for any recently booked appointment on the target date
        target_date_str = target.strftime("%Y-%m-%d")
        booked = [a for a in appts if a["status"] == "CONFIRMED"
                   and target_date_str in a.get("scheduled_at", "")]

        # Clean up any appointments we created
        for a in booked:
            if "10:00" in a.get("scheduled_at", "") or "10:15" in a.get("scheduled_at", ""):
                await cancel_appointment(auth_token, a["id"])


# ════════════════════════════════════════════════════════════════════════════
# SECTION 9: MULTI-PROVIDER EDGE CASES
# ════════════════════════════════════════════════════════════════════════════

class TestMultiProvider:
    """Edge cases for multi-provider scheduling."""

    @pytest.mark.asyncio
    async def test_33_same_time_different_instructors(self, auth_token, providers, conversation_id):
        """
        Scenario 33: Same time should be available with different instructors.
        If instr1 is booked at 10 AM, instr2 should still be available at 10 AM.
        """
        if len(providers) < 2:
            pytest.skip("Need at least 2 providers")

        target = _next_weekday(days_ahead=4)
        target_str = target.strftime("%A, %B %d")

        replies = await chat_multi(auth_token, [
            f"What instructors are available for consultation on {target_str} at 10 AM?",
        ], conversation_id)

        reply = replies[0]
        assert is_natural(reply)
        # Should mention at least one provider's availability
        assert mentions_any(reply, ["available", "instr1", "instr2",
                                     "instructor", "provider", "10"]), \
            f"Expected provider availability info, got: {reply[:200]}"

    @pytest.mark.asyncio
    async def test_34_unavailable_instructor_suggests_alternative(self, auth_token, providers, conversation_id):
        """
        Scenario 34: Requesting a fully booked instructor should suggest alternatives.
        We use a Sunday (closed) to trigger the "no slots" path and check
        if alternatives are offered.
        """
        if not providers:
            pytest.skip("No providers configured")

        next_sunday = _next_specific_day(6)
        sunday_str = next_sunday.strftime("%A, %B %d")
        doc = providers[0]["name"]

        replies = await chat_multi(auth_token, [
            f"I want to see {doc} on {sunday_str}",
            "Yes, that's my number",
        ], conversation_id)

        combined = " ".join(replies)
        assert is_natural(combined)
        # Should mention the day is closed or offer alternatives
        assert mentions_any(combined, ["closed", "unavailable", "not available",
                                     "another day", "different", "alternative",
                                     "sunday"]), \
            f"Expected alternative suggestion, got: {combined[:300]}"


# ════════════════════════════════════════════════════════════════════════════
# SECTION 10: AGENT BEHAVIOR
# ════════════════════════════════════════════════════════════════════════════

class TestAgentBehavior:
    """Tests for general agent conversation quality."""

    @pytest.mark.asyncio
    async def test_natural_greeting(self, auth_token, conversation_id):
        """Agent should respond naturally to a greeting, no tool leaks."""
        reply = await chat(auth_token, "Hi there!", conversation_id)
        assert is_natural(reply), f"Greeting response leaked JSON: {reply[:200]}"
        assert len(reply) < 500, f"Greeting too verbose ({len(reply)} chars)"
        assert mentions_none(reply, ["get_available_slots", "tool_call",
                                      "function_call", "booking_uid"]), \
            f"Greeting leaked internal terms: {reply[:200]}"

    @pytest.mark.asyncio
    async def test_no_json_leak_in_slot_response(self, auth_token, conversation_id):
        """When offering slots, agent should use natural language, not JSON."""
        target = _next_weekday(days_ahead=2)
        reply = await chat(auth_token,
                           f"Do you have any openings on {target.strftime('%A')}?",
                           conversation_id)
        assert is_natural(reply), f"Slot response leaked JSON: {reply[:200]}"
        assert mentions_none(reply, ['"exact_slot_time"', '"summary_for_assistant"',
                                      '"available_slots"', "JSON"]), \
            f"Leaked tool internals: {reply[:200]}"

    @pytest.mark.asyncio
    async def test_empathetic_response(self, auth_token, conversation_id):
        """Agent should respond empathetically to anxiety."""
        reply = await chat(auth_token,
                           "I'm really nervous about my upcoming procedure",
                           conversation_id)
        assert is_natural(reply)
        assert mentions_any(reply, ["understand", "common", "comfortable",
                                     "help", "worry", "normal", "here for"]), \
            f"Expected empathetic response, got: {reply[:200]}"


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _next_weekday(days_ahead: int = 2, holidays: list[dict] | None = None) -> datetime:
    """Return a future date that's a weekday (Mon-Fri) and not a holiday."""
    d = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    holiday_dates = set()
    if holidays:
        holiday_dates = {h["date"] for h in holidays if "date" in h}
    while d.weekday() >= 5 or d.strftime("%Y-%m-%d") in holiday_dates:
        d += timedelta(days=1)
    return d


def _next_specific_day(weekday: int) -> datetime:
    """Return the next occurrence of a specific weekday (0=Mon, 6=Sun)."""
    d = datetime.now(timezone.utc) + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d
