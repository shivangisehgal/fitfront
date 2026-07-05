"""
Shared httpx.AsyncClient — connection pooling for all outbound HTTP calls.

Before this module, every service function created a new httpx.AsyncClient per
request (``async with httpx.AsyncClient() as client: ...``). Each instantiation
opens fresh TCP connections, negotiates TLS, etc. — easily adding 50-200ms of
overhead per call.

A single persistent client reuses connections (HTTP keep-alive) and amortises
TLS handshakes across the lifetime of the process. For Google Calendar alone
this shaves ~100ms off every API call after the first.

Usage in service modules::

    from backend.services.http_client import http

    resp = await http.get("https://...", headers=..., timeout=15)
    resp = await http.post("https://...", json=body)

The ``http`` object is a module-level ``httpx.AsyncClient`` that is lazily
created on first access and closed on application shutdown.
"""
from __future__ import annotations


import logging

import httpx

logger = logging.getLogger(__name__)

# ── Lazy singleton ──────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared client, creating it on first call."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=15,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=120,
            ),
            # Follow redirects (Google OAuth occasionally 302s)
            follow_redirects=True,
        )
        logger.info("[HttpClient] Shared httpx.AsyncClient created (pool: 100/20)")
    return _client


class _ClientProxy:
    """Transparent proxy so ``http.get(...)`` works without calling a function.

    This avoids forcing every call-site to write ``(await get_client()).get(...)``.
    Instead, service modules import ``http`` and use it like a normal AsyncClient.
    """

    def __getattr__(self, name):
        return getattr(_get_client(), name)


http: httpx.AsyncClient = _ClientProxy()  # type: ignore[assignment]


async def close_http_client() -> None:
    """Gracefully close the shared client (call during app shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        logger.info("[HttpClient] Shared httpx.AsyncClient closed.")
    _client = None
