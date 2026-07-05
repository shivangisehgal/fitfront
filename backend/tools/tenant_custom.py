"""
TenantCustomToolProvider — loads tool definitions from the tenant_tools DB table
and dispatches calls to webhook / KB / SMS handlers.

Tenant admins define tools from the CustomToolsManager UI. This provider
reads active tools at list_tools() time and executes them in call_tool().

A simple 60-second per-tenant cache avoids a DB hit on every message turn.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.tools.base import ToolNotFoundError

logger = logging.getLogger(__name__)

# Per-tenant cache: tenant_id (str) → (tools_list, expires_at_unix)
_cache: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 60  # seconds


def _cache_key(tenant_ctx: Any) -> str:
    return str(getattr(tenant_ctx, "id", None) or getattr(tenant_ctx, "tenant_id", "anon"))


def invalidate_cache(tenant_ctx: Any) -> None:
    """Call after CREATE / PATCH / DELETE to clear the cached tool list."""
    key = _cache_key(tenant_ctx)
    _cache.pop(key, None)


class TenantCustomToolProvider:
    """
    Provides tenant-defined custom tools loaded from the DB.

    Checked last in the registry — after platform + coaching — so custom
    tools only handle names that no built-in provider owns.
    """

    def handles(self, tool_name: str) -> bool:
        # Can't know without a tenant context; always return True and let
        # call_tool() raise ToolNotFoundError if the tool doesn't exist.
        return True

    async def list_tools(self, tenant_ctx: Any) -> list[dict]:
        """
        Return active custom tools for this tenant in OpenAI function format.
        Results are cached for _CACHE_TTL seconds.
        """
        if not tenant_ctx:
            return []

        key = _cache_key(tenant_ctx)
        now = time.time()
        cached = _cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        tools = await self._load_from_db(tenant_ctx)
        _cache[key] = (tools, now + _CACHE_TTL)
        return tools

    async def _load_from_db(self, tenant_ctx: Any) -> list[dict]:
        from backend.database import async_session
        from backend.models.tenant_tool import TenantTool
        from sqlalchemy import select

        tenant_id = getattr(tenant_ctx, "id", None) or getattr(tenant_ctx, "tenant_id", None)
        if not tenant_id:
            return []

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(TenantTool).where(
                        TenantTool.tenant_id == tenant_id,
                        TenantTool.is_active == True,  # noqa: E712
                    )
                )
                rows = result.scalars().all()
        except Exception as exc:
            logger.warning("TenantCustomToolProvider: DB load failed — %s", exc)
            return []

        return [self._to_openai_format(row) for row in rows]

    @staticmethod
    def _to_openai_format(row: Any) -> dict:
        return {
            "type": "function",
            "function": {
                "name": row.name,
                "description": row.description,
                "parameters": row.parameters_schema or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    async def call_tool(self, name: str, args: dict, session_ctx: dict) -> dict:
        tenant_ctx = session_ctx.get("tenant_ctx")
        if not tenant_ctx:
            raise ToolNotFoundError(name)

        row = await self._fetch_tool(name, tenant_ctx)
        if row is None:
            raise ToolNotFoundError(name)

        from backend.models.tenant_tool import HandlerType
        if row.handler_type == HandlerType.HTTP_WEBHOOK:
            return await self._call_webhook(row, args, session_ctx)
        elif row.handler_type == HandlerType.KB_LOOKUP:
            return await self._call_kb_lookup(row, args, session_ctx)
        elif row.handler_type == HandlerType.SMS_SEND:
            return await self._call_sms(row, args, session_ctx)
        else:
            raise ToolNotFoundError(name)

    async def _fetch_tool(self, name: str, tenant_ctx: Any) -> Any | None:
        from backend.database import async_session
        from backend.models.tenant_tool import TenantTool
        from sqlalchemy import select

        tenant_id = getattr(tenant_ctx, "id", None) or getattr(tenant_ctx, "tenant_id", None)
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(TenantTool).where(
                        TenantTool.tenant_id == tenant_id,
                        TenantTool.name == name,
                        TenantTool.is_active == True,  # noqa: E712
                    )
                )
                return result.scalar_one_or_none()
        except Exception as exc:
            logger.warning("TenantCustomToolProvider: fetch_tool failed — %s", exc)
            return None

    async def _call_webhook(self, row: Any, args: dict, session_ctx: dict) -> dict:
        """POST args to the tenant's configured webhook URL."""
        import httpx

        cfg: dict = row.handler_config or {}
        url = cfg.get("url", "")
        if not url:
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"The tool '{row.name}' is misconfigured (no URL). "
                    "Please ask the user to try again later."
                ),
            }

        tenant_ctx = session_ctx.get("tenant_ctx")
        payload = {
            "tool_name": row.name,
            "tool_args": args,
            "tenant_id": str(getattr(tenant_ctx, "id", "") if tenant_ctx else ""),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.request(
                    method=cfg.get("method", "POST"),
                    url=url,
                    json=payload,
                    headers=cfg.get("headers", {}),
                )
                resp.raise_for_status()
                data = resp.json()
                # Expect caller to return {"summary_for_assistant": "...", ...}
                if "summary_for_assistant" not in data:
                    data["summary_for_assistant"] = (
                        f"Tool '{row.name}' responded: {str(data)[:200]}"
                    )
                return data
        except httpx.TimeoutException:
            logger.warning("Webhook timeout for tool '%s' at %s", row.name, url)
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"The '{row.name}' service is taking too long to respond. "
                    "Please try again in a moment."
                ),
            }
        except Exception as exc:
            logger.warning("Webhook error for tool '%s': %s", row.name, exc)
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"The '{row.name}' service returned an error. "
                    "Please try again or contact the office directly."
                ),
            }

    async def _call_kb_lookup(self, row: Any, args: dict, session_ctx: dict) -> dict:
        """Look up a topic in the tenant's knowledge base."""
        from backend.tools.platform import _build_office_info

        tenant_ctx = session_ctx.get("tenant_ctx")
        cfg: dict = row.handler_config or {}
        topic = cfg.get("topic", "all")
        return _build_office_info(topic, tenant_ctx)

    async def _call_sms(self, row: Any, args: dict, session_ctx: dict) -> dict:
        """Send a templated SMS via the tenant's Twilio config."""
        from backend.services import sms_service

        tenant_ctx = session_ctx.get("tenant_ctx")
        cfg: dict = row.handler_config or {}
        template: str = cfg.get("template", "")

        phone = args.get("phone", "")
        if not phone:
            return {
                "ok": False,
                "summary_for_assistant": "I need a phone number to send the SMS.",
            }

        try:
            message = template.format(**args) if template else str(args)
        except KeyError as exc:
            logger.warning("SMS template format error for tool '%s': %s", row.name, exc)
            message = template  # send raw template if formatting fails

        try:
            sms_service.send_custom_sms(to=phone, message=message, tenant_ctx=tenant_ctx)
            return {
                "ok": True,
                "summary_for_assistant": (
                    f"SMS sent to {phone}. "
                    "Let the caller know they'll receive a message shortly."
                ),
            }
        except Exception as exc:
            logger.warning("SMS send failed for tool '%s': %s", row.name, exc)
            return {
                "ok": False,
                "summary_for_assistant": (
                    "I wasn't able to send the SMS right now. "
                    "Please ask them to contact the office directly."
                ),
            }
