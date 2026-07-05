"""
Shared fixtures for Scheduler.ai integration tests.

These tests run against the REAL database and call Gemini for real LLM responses.
Make sure the Docker DB and app are running before executing:

    docker compose up -d db
    ./venv/bin/python -m uvicorn backend.main:app --port 8000
    ./venv/bin/python -m pytest tests/test_integration_scheduling.py -v
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Gemini rate-limiter (15 RPM free tier) ──────────────────────────────────
# Each chat turn can trigger multiple Gemini calls (initial + tool round-trips),
# so we pace test-level requests to stay well under the limit.

GEMINI_RPM = int(os.getenv("GEMINI_RPM", "15"))
_MIN_INTERVAL = 60.0 / GEMINI_RPM  # ~4s between chat calls at 15 RPM
_last_chat_time = 0.0


async def _rate_limit():
    """Sleep if needed to stay under Gemini's RPM limit."""
    global _last_chat_time
    now = time.monotonic()
    elapsed = now - _last_chat_time
    if elapsed < _MIN_INTERVAL:
        await asyncio.sleep(_MIN_INTERVAL - elapsed)
    _last_chat_time = time.monotonic()


# ── Configuration ───────────────────────────────────────────────────────────

# The running app base URL — tests hit the real server.
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")

# Test tenant credentials (must exist in the DB).
# Override via env vars: TEST_EMAIL=x TEST_PASSWORD=y pytest ...
TEST_EMAIL = os.getenv("TEST_EMAIL", "a@gmail.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "12345678")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def auth_token():
    """
    Obtain a JWT for the test tenant.

    Strategy (in order):
      1. Try login with TEST_EMAIL + TEST_PASSWORD (if password provided)
      2. Fall back to minting a JWT directly from the DB tenant record
         (requires the app to share the same JWT_SECRET)
    """
    # ── Strategy 1: login via API ──────────────────────────────────────
    if TEST_PASSWORD:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            resp = await client.post("/api/auth/login", json={
                "email": TEST_EMAIL,
                "password": TEST_PASSWORD,
            })
            if resp.status_code == 200:
                return resp.json()["access_token"]
            # Password didn't work — fall through to strategy 2

    # ── Strategy 2: mint JWT directly (DB → tenant_id → JWT) ──────────
    # This bypasses the login endpoint entirely — useful when you don't
    # know the plaintext password. Requires the test to run on the same
    # machine as the app (shared JWT_SECRET).
    from backend.services.auth_service import create_access_token
    from backend.database import async_session
    from backend.models.tenant import Tenant
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.owner_email == TEST_EMAIL)
        )
        tenant = result.scalar_one_or_none()

    assert tenant is not None, (
        f"No tenant found with email={TEST_EMAIL}. "
        f"Set TEST_EMAIL env var to match a tenant in the DB."
    )
    assert tenant.status.value in ("active", "approved"), (
        f"Tenant {TEST_EMAIL} status is {tenant.status.value} — must be active."
    )

    token = create_access_token(tenant.id, tenant.owner_email, tenant.is_admin)
    return token


@pytest_asyncio.fixture(scope="session")
async def tenant_info(auth_token):
    """Fetch tenant config so tests know about providers, appointment types, etc."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        resp = await client.get("/api/config", headers=_auth(auth_token))
        assert resp.status_code == 200, f"Config fetch failed: {resp.text}"
        return resp.json()


@pytest_asyncio.fixture(scope="session")
async def providers(auth_token):
    """Fetch list of providers for this tenant."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        resp = await client.get("/api/providers", headers=_auth(auth_token))
        assert resp.status_code == 200, f"Providers fetch failed: {resp.text}"
        return resp.json()


@pytest.fixture
def conversation_id():
    """Generate a unique conversation ID for each test."""
    return str(uuid.uuid4())


# ── Helpers ─────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def chat(
    token: str,
    message: str,
    conversation_id: str,
    test_phone: str = "+155501190",
    timeout: float = 60,
) -> str:
    """
    Send a message to the chat API and collect the full streamed response.
    Returns the assistant's complete reply text.
    """
    await _rate_limit()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "message": message,
                "conversation_id": conversation_id,
                "test_phone": test_phone,
            },
            headers=_auth(token),
        ) as resp:
            assert resp.status_code == 200, f"Chat failed: {resp.status_code}"
            full_reply = ""
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_reply += content
                except json.JSONDecodeError:
                    continue
            return full_reply.strip()


async def chat_multi(
    token: str,
    messages: list[str],
    conversation_id: str,
    test_phone: str = "+155501190",
) -> list[str]:
    """
    Send multiple messages in sequence (multi-turn conversation).
    Returns list of assistant replies.
    """
    replies = []
    for msg in messages:
        reply = await chat(token, msg, conversation_id, test_phone)
        replies.append(reply)
    return replies


async def get_appointments(token: str) -> list[dict]:
    """Fetch all appointments from the API."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        resp = await client.get("/api/appointments", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])


async def cancel_appointment(token: str, appointment_id: str):
    """Cancel an appointment by ID."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        resp = await client.post(
            f"/api/appointments/{appointment_id}/cancel",
            headers=_auth(token),
        )
        return resp.status_code


def mentions_any(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)."""
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def mentions_none(text: str, keywords: list[str]) -> bool:
    """Check that text contains NONE of the keywords."""
    t = text.lower()
    return not any(k.lower() in t for k in keywords)


def is_natural(text: str) -> bool:
    """Reply is natural language — no JSON leaks."""
    if not text or not text.strip():
        return False
    bad = ["{", "}", '"available_slots"', '"exact_slot_time"',
           '"summary_for_assistant"', "field_name", "JSON"]
    return not any(b in text for b in bad)
