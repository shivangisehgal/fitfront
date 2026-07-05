"""
Bolna AI outbound calling service.

Bolna is an AI voice calling platform that supports Indian numbers (+91...).
API base: https://api.bolna.ai
Auth: Bearer <api_key>

Call flow: POST /call with agent_id + recipient_phone_number → Bolna queues
and places the call using its own AI (or a custom LLM if configured in the
Bolna dashboard with an OpenAI-compatible endpoint).
"""
from __future__ import annotations


import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BOLNA_API_BASE = "https://api.bolna.ai"


async def call_contact(
    api_key: str,
    agent_id: str,
    phone: str,
    name: str = "Student",
    from_phone_number: str | None = None,
    user_data: dict | None = None,
) -> dict[str, Any]:
    """
    Trigger an immediate outbound call via Bolna AI.

    Args:
        api_key:           Bolna API key (Bearer token).
        agent_id:          UUID of the Bolna voice agent.
        phone:             Recipient phone in E.164 format (e.g. +919876543210).
        name:              Contact name passed as user_data variable.
        from_phone_number: Caller ID to use (leave None to use agent's default number).
        user_data:         Dynamic variables injected into the agent prompt.

    Returns:
        dict with keys: message, status, execution_id
    Raises:
        Exception on HTTP or API-level error.
    """
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "recipient_phone_number": phone,
    }
    if from_phone_number:
        payload["from_phone_number"] = from_phone_number

    # Pass caller name so agent can address them by name
    payload["user_data"] = user_data or {"name": name}

    logger.info("[Bolna] Initiating call to %s (agent=%s)", phone, agent_id)

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{BOLNA_API_BASE}/call",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        # Non-200 → surface the error body
        if resp.status_code != 200:
            body = resp.text[:300] if resp.text else "(empty)"
            logger.error("[Bolna] Call failed HTTP %d: %s", resp.status_code, body)
            raise Exception(f"Bolna API returned HTTP {resp.status_code}: {body}")

        data = resp.json()

        # API-level error in the response body
        if data.get("error"):
            raise Exception(f"Bolna error: {data.get('message', data)}")

        logger.info(
            "[Bolna] Call queued — execution_id=%s status=%s",
            data.get("execution_id"),
            data.get("status"),
        )
        return data


async def test_connection(api_key: str, agent_id: str) -> dict:
    """
    Validate Bolna credentials without triggering a call.

    Calls GET /agent/{agent_id} — read-only, free, no billing impact.

    Returns:
        {"ok": True, "agent_name": "...", "message": "..."}  on success
        {"ok": False, "message": "..."}                       on failure
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BOLNA_API_BASE}/agent/{agent_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )

        if resp.status_code == 401:
            return {"ok": False, "message": "Invalid API key — Bolna rejected the credentials."}
        if resp.status_code == 404:
            return {"ok": False, "message": "Agent ID not found — check the agent UUID in your Bolna dashboard."}
        if resp.status_code != 200:
            return {"ok": False, "message": f"Bolna returned HTTP {resp.status_code}."}

        data = resp.json()
        agent_name = data.get("agent_name") or data.get("name") or "Unknown agent"
        logger.info("[Bolna] Connection test passed — agent: %s", agent_name)
        return {"ok": True, "agent_name": agent_name, "message": f"Connected — agent '{agent_name}' found."}

    except httpx.TimeoutException:
        return {"ok": False, "message": "Request timed out — Bolna API may be unreachable."}
    except Exception as exc:
        logger.error("[Bolna] Connection test error: %s", exc)
        return {"ok": False, "message": f"Connection error: {exc}"}


async def get_call_logs(api_key: str, agent_id: str | None = None) -> list[dict]:
    """
    Fetch recent call execution logs from Bolna.

    Returns a list of execution dicts (empty list on error).
    """
    try:
        if agent_id:
            url = f"{BOLNA_API_BASE}/agent/{agent_id}/executions"
        else:
            url = f"{BOLNA_API_BASE}/executions"

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                params={"page": 1, "page_size": 50},
            )
            if resp.status_code != 200:
                logger.warning("[Bolna] Logs returned HTTP %d", resp.status_code)
                return []

            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", data.get("executions", []))
            return []

    except Exception as exc:
        logger.error("[Bolna] Failed to fetch call logs: %s", exc)
        return []
