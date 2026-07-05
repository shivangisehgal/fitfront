"""
Tenant custom tool management API.

Endpoints:
  GET    /api/tenant-tools          → list active custom tools for the tenant
  POST   /api/tenant-tools          → create a new custom tool
  PATCH  /api/tenant-tools/{id}     → update a tool (name, description, config, active)
  DELETE /api/tenant-tools/{id}     → delete a tool
  POST   /api/tenant-tools/{id}/test → dry-run a webhook tool (webhook only)

Validation rules:
  • name must match ^[a-z][a-z0-9_]{1,49}$ (snake_case, 2–50 chars)
  • name must not shadow any platform tool name (from PlatformToolProvider)
  • handler_type == http_webhook → handler_config.url must be present, https://
  • handler_type == kb_lookup → handler_config.topic must be one of the valid KB topics
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.database import async_session
from backend.models.tenant import Tenant
from backend.models.tenant_tool import TenantTool, HandlerType
from backend.services import auth_service
from backend.tools.platform import PlatformToolProvider
from backend.tools.tenant_custom import invalidate_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant-tools", tags=["Custom Tools"])

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
_KB_TOPICS = {"hours", "location", "services", "faqs", "all"}


# ── Request / Response schemas ────────────────────────────────────────────────

class ToolCreateRequest(BaseModel):
    name: str = Field(..., description="Snake-case tool name, e.g. 'check_scholarship'")
    description: str = Field(..., min_length=10, max_length=1000)
    parameters_schema: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
        description="JSON Schema object describing the tool's parameters",
    )
    handler_type: HandlerType
    handler_config: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _TOOL_NAME_RE.match(v):
            raise ValueError(
                "Tool name must be 2–50 chars, start with a letter, "
                "contain only lowercase letters, digits, and underscores."
            )
        # Cannot shadow platform tool names
        if v in PlatformToolProvider.TOOL_NAMES:
            raise ValueError(
                f"'{v}' is a built-in platform tool name. "
                "Choose a different name to avoid conflicts."
            )
        return v

    @model_validator(mode="after")
    def validate_handler_config(self) -> "ToolCreateRequest":
        cfg = self.handler_config or {}
        if self.handler_type == HandlerType.HTTP_WEBHOOK:
            url = cfg.get("url", "")
            if not url:
                raise ValueError("handler_config.url is required for http_webhook tools.")
            if not url.startswith("https://"):
                raise ValueError("handler_config.url must be an HTTPS URL.")
        elif self.handler_type == HandlerType.KB_LOOKUP:
            topic = cfg.get("topic", "")
            if topic and topic not in _KB_TOPICS:
                raise ValueError(
                    f"handler_config.topic must be one of: {', '.join(sorted(_KB_TOPICS))}"
                )
        return self


class ToolUpdateRequest(BaseModel):
    description: Optional[str] = Field(None, min_length=10, max_length=1000)
    parameters_schema: Optional[dict] = None
    handler_config: Optional[dict] = None
    is_active: Optional[bool] = None


class ToolTestRequest(BaseModel):
    sample_args: dict = Field(default_factory=dict)


def _to_dict(tool: TenantTool) -> dict:
    return {
        "id": str(tool.id),
        "tenant_id": str(tool.tenant_id),
        "name": tool.name,
        "description": tool.description,
        "parameters_schema": tool.parameters_schema,
        "handler_type": tool.handler_type.value,
        "handler_config": tool.handler_config,
        "is_active": tool.is_active,
        "created_at": tool.created_at.isoformat() if tool.created_at else None,
        "updated_at": tool.updated_at.isoformat() if tool.updated_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_tools(
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List all custom tools for the authenticated tenant."""
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(
            select(TenantTool).where(TenantTool.tenant_id == current_user.id).order_by(TenantTool.created_at)
        )
        tools = result.scalars().all()

    return {"tools": [_to_dict(t) for t in tools], "count": len(tools)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tool(
    body: ToolCreateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Create a new custom tool for this tenant."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    async with async_session() as db:
        # Check uniqueness
        existing = await db.execute(
            select(TenantTool).where(
                TenantTool.tenant_id == current_user.id,
                TenantTool.name == body.name,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A tool named '{body.name}' already exists for this tenant.",
            )

        tool = TenantTool(
            tenant_id=current_user.id,
            name=body.name,
            description=body.description,
            parameters_schema=body.parameters_schema,
            handler_type=body.handler_type,
            handler_config=body.handler_config,
            is_active=True,
        )
        db.add(tool)
        try:
            await db.commit()
            await db.refresh(tool)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A tool named '{body.name}' already exists for this tenant.",
            )

    invalidate_cache(current_user)
    logger.info("Created custom tool '%s' for tenant %s", body.name, current_user.id)
    return _to_dict(tool)


@router.patch("/{tool_id}")
async def update_tool(
    tool_id: str,
    body: ToolUpdateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Update an existing custom tool."""
    from sqlalchemy import select
    from datetime import datetime, timezone

    try:
        tid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tool ID.")

    async with async_session() as db:
        result = await db.execute(
            select(TenantTool).where(
                TenantTool.id == tid,
                TenantTool.tenant_id == current_user.id,
            )
        )
        tool = result.scalar_one_or_none()
        if not tool:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")

        if body.description is not None:
            tool.description = body.description
        if body.parameters_schema is not None:
            tool.parameters_schema = body.parameters_schema
        if body.handler_config is not None:
            tool.handler_config = body.handler_config
        if body.is_active is not None:
            tool.is_active = body.is_active
        tool.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(tool)

    invalidate_cache(current_user)
    logger.info("Updated custom tool '%s' for tenant %s", tool.name, current_user.id)
    return _to_dict(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Delete a custom tool."""
    from sqlalchemy import select

    try:
        tid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tool ID.")

    async with async_session() as db:
        result = await db.execute(
            select(TenantTool).where(
                TenantTool.id == tid,
                TenantTool.tenant_id == current_user.id,
            )
        )
        tool = result.scalar_one_or_none()
        if not tool:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")
        tool_name = tool.name
        await db.delete(tool)
        await db.commit()

    invalidate_cache(current_user)
    logger.info("Deleted custom tool '%s' for tenant %s", tool_name, current_user.id)


@router.post("/{tool_id}/test")
async def test_tool(
    tool_id: str,
    body: ToolTestRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Dry-run a webhook tool with sample args. Returns the raw webhook response.
    Only supported for http_webhook tools.
    """
    from sqlalchemy import select
    import httpx

    try:
        tid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tool ID.")

    async with async_session() as db:
        result = await db.execute(
            select(TenantTool).where(
                TenantTool.id == tid,
                TenantTool.tenant_id == current_user.id,
            )
        )
        tool = result.scalar_one_or_none()
        if not tool:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")

    if tool.handler_type != HandlerType.HTTP_WEBHOOK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test is only supported for http_webhook tools.",
        )

    cfg: dict = tool.handler_config or {}
    url = cfg.get("url", "")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tool has no URL configured.",
        )

    payload = {
        "tool_name": tool.name,
        "tool_args": body.sample_args,
        "tenant_id": str(current_user.id),
        "_test": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method=cfg.get("method", "POST"),
                url=url,
                json=payload,
                headers=cfg.get("headers", {}),
            )
            return {
                "status_code": resp.status_code,
                "response": resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text,
                "ok": resp.status_code < 400,
            }
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Webhook timed out.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
