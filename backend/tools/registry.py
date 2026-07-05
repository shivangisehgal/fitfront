"""
ToolRegistry — aggregates all tool providers and routes dispatch.

Provider check order:
  1. PlatformToolProvider  — always active, no DB required
  2. FitnessToolProvider   — active for fitness studio tenants
  3. TenantCustomToolProvider — active per tenant, loaded from DB (checked last)
"""

from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import ToolNotFoundError
from backend.tools.platform import PlatformToolProvider
from backend.tools.fitness import FitnessToolProvider
from backend.tools.tenant_custom import TenantCustomToolProvider

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._platform = PlatformToolProvider()
        self._fitness = FitnessToolProvider()
        self._custom = TenantCustomToolProvider()

        self._providers = [self._platform, self._fitness, self._custom]

    async def get_tools(self, tenant_ctx: Any) -> list[dict]:
        merged: list[dict] = []
        seen: set[str] = set()

        for provider in self._providers:
            for tool in await provider.list_tools(tenant_ctx):
                name = tool.get("function", {}).get("name", "")
                if name and name not in seen:
                    merged.append(tool)
                    seen.add(name)

        logger.info(
            "[Registry] get_tools → %d tools (tenant=%s)",
            len(merged),
            getattr(tenant_ctx, "slug", None) or "global",
        )
        return merged

    async def dispatch(self, name: str, args: dict, session_ctx: dict) -> dict:
        if self._platform.handles(name):
            return await self._platform.call_tool(name, args, session_ctx)

        if self._fitness.handles(name):
            return await self._fitness.call_tool(name, args, session_ctx)

        try:
            return await self._custom.call_tool(name, args, session_ctx)
        except ToolNotFoundError:
            logger.warning("[Registry] Unknown tool dispatched: %s", name)
            return {"error": f"Unknown tool: {name}"}

    @property
    def platform(self) -> PlatformToolProvider:
        return self._platform

    @property
    def fitness(self) -> FitnessToolProvider:
        return self._fitness

    @property
    def custom(self) -> TenantCustomToolProvider:
        return self._custom


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        logger.info("[Registry] Initialized ToolRegistry (platform + fitness + custom)")
    return _registry
