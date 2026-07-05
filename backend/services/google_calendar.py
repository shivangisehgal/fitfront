"""
Google Calendar service — OAuth 2.0 token management and Calendar API operations.

Tenants can connect their Google Calendar via OAuth and the AI agent will
check availability and book directly into their calendar.

Architecture:
  - Platform owns ONE Google OAuth app (client_id + client_secret in .env).
  - Each tenant stores their own refresh_token after granting consent.
  - We exchange/refresh tokens as needed and call Google Calendar API.

No Google SDK dependency — we use plain httpx against the REST endpoints to
keep the dependency tree small.
"""
from __future__ import annotations


import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.config import settings
from backend.defaults import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    DEFAULT_BUSINESS_HOURS,
    DEFAULT_TIMEZONE,
)
from backend.services.http_client import http

logger = logging.getLogger(__name__)

# ── Google OAuth endpoints ───────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

# Scopes: read/write calendar events + read user email for display
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
]


# ── OAuth flow helpers ───────────────────────────────────────────────────────


def build_auth_url(state: str) -> str:
    """
    Build the Google OAuth consent URL.

    Args:
        state: opaque string (typically tenant_id) passed through the OAuth
               redirect so we know which tenant initiated the flow.

    Returns:
        Full URL to redirect the user's browser to.
    """
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",    # gives us a refresh_token
        "prompt": "consent",          # force consent to always get refresh_token
        "state": state,
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    logger.info("[GoogleCal] Built auth URL for state=%s", state)
    return url


async def exchange_code(code: str) -> dict[str, Any]:
    """
    Exchange the authorization code for access + refresh tokens.

    Returns:
        Dict with access_token, refresh_token, expires_in, token_type.
    """
    body = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
    }
    resp = await http.post(GOOGLE_TOKEN_URL, data=body)
    resp.raise_for_status()
    data = resp.json()

    logger.info("[GoogleCal] Token exchange successful (has_refresh=%s)",
                bool(data.get("refresh_token")))
    return data


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """
    Use a stored refresh_token to get a fresh access_token.

    Returns:
        Dict with access_token, expires_in, token_type.
    """
    body = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    resp = await http.post(GOOGLE_TOKEN_URL, data=body)
    resp.raise_for_status()
    data = resp.json()

    logger.debug("[GoogleCal] Token refresh successful")
    return data


async def get_user_email(access_token: str) -> str:
    """Fetch the Google account email address for display purposes."""
    resp = await http.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("email", "")


# ── Calendar API operations ──────────────────────────────────────────────────


def _cal_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


# ── Access token cache ─────────────────────────────────────────────────────
# Google access tokens are valid for ~3600 seconds. Caching avoids a 5-10
# second round-trip to oauth2.googleapis.com on EVERY Calendar API call.
# We expire cached tokens 5 minutes early to avoid edge-case 401s.

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}  # refresh_token → (access_token, expires_at)
_TOKEN_CACHE_MARGIN = 300  # refresh 5 minutes before actual expiry


async def _get_access_token(refresh_token: str) -> str:
    """Return a valid access token, using the cache when possible.

    On a cache miss (first call or expired token), refreshes from Google
    and caches the result. This turns a 5-10 second network round-trip
    into a ~0ms dict lookup for subsequent calls within the hour.
    """
    now = time.time()
    cached = _TOKEN_CACHE.get(refresh_token)
    if cached:
        token, expires_at = cached
        if now < expires_at:
            logger.debug("[GoogleCal] Using cached access token (%.0fs remaining)",
                         expires_at - now)
            return token

    data = await refresh_access_token(refresh_token)
    access_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    _TOKEN_CACHE[refresh_token] = (access_token, now + expires_in - _TOKEN_CACHE_MARGIN)
    logger.info("[GoogleCal] Refreshed + cached access token (expires_in=%ds)", expires_in)
    return access_token


_DOW_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Default business hours — use centralized defaults
_DEFAULT_BUSINESS_HOURS: dict[str, Any] = DEFAULT_BUSINESS_HOURS


def _parse_hour_minute(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' or 'H:MM AM/PM' → (hour_24, minute)."""
    time_str = time_str.strip().upper()
    # Handle "HH:MM" 24-hour format
    if "AM" not in time_str and "PM" not in time_str:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    # Handle "H:MM AM/PM" format
    is_pm = "PM" in time_str
    time_str = time_str.replace("AM", "").replace("PM", "").strip()
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    if is_pm and hour != 12:
        hour += 12
    elif not is_pm and hour == 12:
        hour = 0
    return hour, minute


async def get_available_slots(
    refresh_token: str,
    date_from: str,
    date_to: str,
    timezone: str = DEFAULT_TIMEZONE,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    business_hours: dict[str, Any] | None = None,
    calendar_id: str = "primary",
    slot_capacity: int = 1,
) -> list[str]:
    """
    Check Google Calendar availability using the Events List API, then generate
    open slots based on the tenant's configured business hours.

    Supports multiple bookings per slot: instead of treating any overlap as "busy"
    (like FreeBusy does), we count how many events overlap each candidate slot
    and only mark it unavailable when the count reaches ``slot_capacity``.

    Args:
        refresh_token: Tenant's stored Google OAuth refresh token.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        timezone: IANA timezone (e.g. "America/Chicago").
        duration_minutes: Appointment length in minutes.
        business_hours: Tenant's business hours dict, e.g.
            {"monday": {"open": "08:00", "close": "18:00"}, ...}.
            Falls back to 9 AM – 5 PM Mon–Fri if not provided.
        calendar_id: Google Calendar ID (default "primary").
        slot_capacity: How many overlapping bookings are allowed per slot
            before it's considered full. Default 1 (classic single-booking).

    Returns:
        List of available slot ISO datetime strings.
    """
    access_token = await _get_access_token(refresh_token)
    hours_config = business_hours or _DEFAULT_BUSINESS_HOURS

    # Google requires RFC3339 timestamps (with timezone offset) — naive
    # timestamps like "2026-05-09T00:00:00" cause a 400 Bad Request.
    from zoneinfo import ZoneInfo
    tz_obj = ZoneInfo(timezone)
    time_min = datetime.fromisoformat(f"{date_from}T00:00:00").replace(tzinfo=tz_obj)
    time_max = datetime.fromisoformat(f"{date_to}T23:59:59").replace(tzinfo=tz_obj)

    logger.info("[GoogleCal] Events List query: %s to %s (%s, slot_capacity=%d)",
                date_from, date_to, timezone, slot_capacity)

    try:
        # ── Fetch actual events (replaces FreeBusy) ─────────────────────
        # Events List gives us start/end of each event so we can COUNT
        # overlaps per slot, unlike FreeBusy which merges them into binary.
        params: dict[str, Any] = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": "true",       # expand recurring events
            "orderBy": "startTime",
            "maxResults": 500,
        }

        resp = await http.get(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events",
            headers=_cal_headers(access_token),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract event time ranges (skip all-day events and cancelled)
        event_ranges: list[tuple[datetime, datetime]] = []
        for event in data.get("items", []):
            if event.get("status") == "cancelled":
                continue
            start_raw = event.get("start", {})
            end_raw = event.get("end", {})
            # Skip all-day events (have "date" instead of "dateTime")
            if "dateTime" not in start_raw or "dateTime" not in end_raw:
                continue
            ev_start = datetime.fromisoformat(start_raw["dateTime"].replace("Z", "+00:00"))
            ev_end = datetime.fromisoformat(end_raw["dateTime"].replace("Z", "+00:00"))
            event_ranges.append((ev_start, ev_end))

        logger.info("[GoogleCal] Found %d timed events in range", len(event_ranges))

        # ── Generate available slots using tenant's business hours ───────
        tz = ZoneInfo(timezone)
        base_date = datetime.fromisoformat(date_from)
        slots = []

        current_date = base_date
        end_date = datetime.fromisoformat(date_to)
        while current_date <= end_date:
            dow = _DOW_KEYS[current_date.weekday()]
            day_hours = hours_config.get(dow)

            # Skip closed days (None or missing or not a dict or open=False)
            if not day_hours or not isinstance(day_hours, dict) or not day_hours.get("open"):
                current_date += timedelta(days=1)
                continue

            open_h, open_m = _parse_hour_minute(day_hours["open"])
            close_h, close_m = _parse_hour_minute(day_hours["close"])

            # Generate 15-minute interval slots within business hours
            slot_hour, slot_min = open_h, open_m
            while True:
                slot_start = current_date.replace(
                    hour=slot_hour, minute=slot_min, second=0, microsecond=0
                )
                slot_start_tz = slot_start.replace(tzinfo=tz)
                slot_end_tz = slot_start_tz + timedelta(minutes=duration_minutes)

                # Check if slot_end exceeds closing time
                if (slot_end_tz.hour > close_h or
                        (slot_end_tz.hour == close_h and slot_end_tz.minute > close_m)):
                    break

                # Skip slots that are in the past (with 5-minute buffer)
                now_tz = datetime.now(tz)
                if slot_start_tz < now_tz - timedelta(minutes=5):
                    slot_min += 15
                    if slot_min >= 60:
                        slot_hour += 1
                        slot_min -= 60
                    continue

                # Count how many existing events overlap this candidate slot
                overlap_count = 0
                for ev_start, ev_end in event_ranges:
                    if slot_start_tz < ev_end and slot_end_tz > ev_start:
                        overlap_count += 1
                        # Early exit: no need to keep counting past the limit
                        if overlap_count >= slot_capacity:
                            break

                if overlap_count < slot_capacity:
                    slots.append(slot_start_tz.isoformat())

                # Advance by 15 minutes
                slot_min += 15
                if slot_min >= 60:
                    slot_hour += 1
                    slot_min -= 60

                # Safety: break if we've gone past closing hour
                if slot_hour > close_h or (slot_hour == close_h and slot_min > 0):
                    break

            current_date += timedelta(days=1)

        logger.info("[GoogleCal] Generated %d available slots for %s to %s (slot_capacity=%d)",
                    len(slots), date_from, date_to, slot_capacity)
        return slots

    except httpx.HTTPStatusError as exc:
        logger.error("[GoogleCal] Events List error (HTTP %s): %s",
                     exc.response.status_code, exc.response.text[:500])
        return []
    except Exception as exc:
        logger.error("[GoogleCal] Events List failed: %s", exc, exc_info=True)
        return []


async def book_appointment(
    refresh_token: str,
    caller_info: dict[str, Any],
    start_time: str,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    timezone: str = DEFAULT_TIMEZONE,
    calendar_id: str = "primary",
    provider_name: str | None = None,
) -> dict[str, Any] | None:
    """
    Create a Google Calendar event for the appointment.

    Args:
        refresh_token: Tenant's stored Google OAuth refresh token.
        caller_info: Dict with name, email, phone, etc.
        start_time: ISO datetime string for the slot.
        duration_minutes: Appointment length.
        timezone: IANA timezone.
        calendar_id: Google Calendar ID.
        provider_name: Optional name of the provider — embedded into the
            event title and description so it's visible inside Google Calendar.

    Returns:
        Dict with id, uid (htmlLink), status on success; None on failure.
    """
    access_token = await _get_access_token(refresh_token)

    # Parse start time and compute end time
    start_dt = datetime.fromisoformat(start_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    caller_name = caller_info.get("name", "Caller")
    caller_email = caller_info.get("email", "")
    caller_phone = caller_info.get("phone", "")

    provider_line = f"Provider: {provider_name}\n" if provider_name else ""
    summary = (
        f"Appointment: {caller_name} ({provider_name})"
        if provider_name
        else f"Appointment: {caller_name}"
    )

    event_body = {
        "summary": summary,
        "description": (
            f"Caller: {caller_name}\n"
            f"Phone: {caller_phone}\n"
            f"DOB: {caller_info.get('dob', 'N/A')}\n"
            f"{provider_line}"
            f"\nBooked by FitFront AI front desk"
        ),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": timezone,
        },
        "attendees": (
            [{"email": caller_email}]  # attendees takes email — variable name doesn't matter
            if caller_email and "@" in caller_email and "noemail" not in caller_email
            else []
        ),
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 30},
            ],
        },
    }

    logger.info("[GoogleCal] Creating event: %s at %s (%d min)",
                caller_name, start_time, duration_minutes)

    try:
        resp = await http.post(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events",
            headers=_cal_headers(access_token),
            json=event_body,
        )
        resp.raise_for_status()
        data = resp.json()

        event_id = data.get("id", "")
        result = {
            "id": event_id,
            "uid": f"gcal-{event_id}",
            "status": "ACCEPTED",
            "html_link": data.get("htmlLink", ""),
        }
        logger.info("[GoogleCal] Event created: id=%s", event_id)
        return result

    except httpx.HTTPStatusError as exc:
        logger.error("[GoogleCal] Create event error (HTTP %s): %s",
                     exc.response.status_code, exc.response.text[:500])
        return None
    except Exception as exc:
        logger.error("[GoogleCal] Create event failed: %s", exc, exc_info=True)
        return None


async def list_events(
    refresh_token: str,
    time_min: str,
    time_max: str | None = None,
    calendar_id: str = "primary",
    max_results: int = 250,
    show_deleted: bool = False,
) -> list[dict[str, Any]]:
    """
    List upcoming events from Google Calendar.

    Args:
        refresh_token: Tenant's stored Google OAuth refresh token.
        time_min: ISO datetime string — only events starting at/after this.
        time_max: ISO datetime string — only events starting before this (optional).
        calendar_id: Google Calendar ID (default "primary").
        max_results: Max events to return (default 250).
        show_deleted: If True, include cancelled/deleted events (for sync).

    Returns:
        List of Google Calendar event dicts with id, summary, start, end, etc.
    """
    access_token = await _get_access_token(refresh_token)

    params: dict[str, Any] = {
        "timeMin": time_min if time_min.endswith("Z") else f"{time_min}Z",
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if time_max:
        params["timeMax"] = time_max if time_max.endswith("Z") else f"{time_max}Z"
    if show_deleted:
        params["showDeleted"] = "true"

    logger.info("[GoogleCal] Listing events: timeMin=%s timeMax=%s max=%d",
                time_min, time_max, max_results)

    try:
        resp = await http.get(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events",
            headers=_cal_headers(access_token),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        events = data.get("items", [])
        logger.info("[GoogleCal] Listed %d events", len(events))
        return events

    except httpx.HTTPStatusError as exc:
        logger.error("[GoogleCal] List events error (HTTP %s): %s",
                     exc.response.status_code, exc.response.text[:500])
        return []
    except Exception as exc:
        logger.error("[GoogleCal] List events failed: %s", exc, exc_info=True)
        return []


async def cancel_appointment(
    refresh_token: str,
    event_id: str,
    calendar_id: str = "primary",
) -> bool:
    """
    Cancel (delete) a Google Calendar event.

    Args:
        refresh_token: Tenant's stored Google OAuth refresh token.
        event_id: The Google Calendar event ID (from booking response).
        calendar_id: Google Calendar ID.

    Returns:
        True on success, False on failure.
    """
    # Strip the "gcal-" prefix if present
    if event_id.startswith("gcal-"):
        event_id = event_id[5:]

    access_token = await _get_access_token(refresh_token)

    logger.info("[GoogleCal] Cancelling event: %s", event_id)

    try:
        resp = await http.delete(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers=_cal_headers(access_token),
        )
        resp.raise_for_status()

        logger.info("[GoogleCal] Event cancelled: %s", event_id)
        return True

    except httpx.HTTPStatusError as exc:
        logger.error("[GoogleCal] Cancel error (HTTP %s): %s",
                     exc.response.status_code, exc.response.text[:500])
        return False
    except Exception as exc:
        logger.error("[GoogleCal] Cancel failed: %s", exc, exc_info=True)
        return False


async def reschedule_appointment(
    refresh_token: str,
    event_id: str,
    new_start_time: str,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    timezone: str = DEFAULT_TIMEZONE,
    calendar_id: str = "primary",
) -> dict[str, Any] | None:
    """
    Reschedule a Google Calendar event by updating its start/end time.

    Args:
        refresh_token: Tenant's stored Google OAuth refresh token.
        event_id: The Google Calendar event ID.
        new_start_time: New ISO datetime string.
        duration_minutes: Appointment length.
        timezone: IANA timezone.
        calendar_id: Google Calendar ID.

    Returns:
        Dict with uid, new_start, status on success; None on failure.
    """
    # Strip the "gcal-" prefix if present
    if event_id.startswith("gcal-"):
        event_id = event_id[5:]

    access_token = await _get_access_token(refresh_token)

    new_start_dt = datetime.fromisoformat(new_start_time)
    new_end_dt = new_start_dt + timedelta(minutes=duration_minutes)

    patch_body = {
        "start": {
            "dateTime": new_start_dt.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": new_end_dt.isoformat(),
            "timeZone": timezone,
        },
    }

    logger.info("[GoogleCal] Rescheduling event %s → %s", event_id, new_start_time)

    try:
        resp = await http.patch(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
            headers=_cal_headers(access_token),
            json=patch_body,
        )
        resp.raise_for_status()

        result = {
            "uid": f"gcal-{event_id}",
            "new_start": new_start_time,
            "status": "RESCHEDULED",
        }
        logger.info("[GoogleCal] Event rescheduled: %s → %s", event_id, new_start_time)
        return result

    except httpx.HTTPStatusError as exc:
        logger.error("[GoogleCal] Reschedule error (HTTP %s): %s",
                     exc.response.status_code, exc.response.text[:500])
        return None
    except Exception as exc:
        logger.error("[GoogleCal] Reschedule failed: %s", exc, exc_info=True)
        return None
