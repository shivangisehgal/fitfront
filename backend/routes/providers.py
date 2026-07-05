"""
Trainer management API routes.

GET    /api/trainers               -> list trainers for the authenticated tenant
POST   /api/trainers               -> create a new trainer
PUT    /api/trainers/{provider_id} -> update a trainer
DELETE /api/trainers/{provider_id} -> soft-delete (deactivate) a trainer

Legacy alias: /api/providers (same handlers).
"""
from __future__ import annotations


import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.models.tenant import Tenant
from backend.services import auth_service, provider_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Trainers"])

_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


# -- Request / Response schemas ------------------------------------------------


class TrialSessionSlot(BaseModel):
    """A fixed trial session time window, e.g. {"start": "08:00", "end": "10:00"}."""
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError("Time must be in HH:MM format (e.g. '08:00')")
        return v


class ProviderCreateRequest(BaseModel):
    """Payload for creating a new provider."""
    name: str = Field(..., min_length=1, max_length=255)
    title: Optional[str] = None
    appointment_types: Optional[list[str]] = None
    calendar_id: Optional[str] = None
    slot_capacity: int = Field(default=1, ge=1, le=10)
    business_hours_override: Optional[dict[str, Any]] = None
    specialty: Optional[str] = None
    # Day-keyed trial session windows: {"monday": [{"start": "08:00", "end": "10:00"}], ...}
    trial_session_slots: Optional[dict[str, Optional[list[TrialSessionSlot]]]] = None


class ProviderUpdateRequest(BaseModel):
    """Partial update for a provider."""
    name: Optional[str] = None
    title: Optional[str] = None
    appointment_types: Optional[list[str]] = None
    calendar_id: Optional[str] = None
    slot_capacity: Optional[int] = Field(default=None, ge=1, le=10)
    business_hours_override: Optional[dict[str, Any]] = None
    specialty: Optional[str] = None
    trial_session_slots: Optional[dict[str, Optional[list[TrialSessionSlot]]]] = None


# -- Routes --------------------------------------------------------------------


@router.get("")
async def list_providers(
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """List all providers for the authenticated tenant."""
    logger.info("[Providers] Listing providers for tenant=%s", current_user.slug)
    try:
        providers = await provider_service.list_providers(current_user.id)
        return providers
    except Exception as exc:
        logger.error("[Providers] Failed to list providers for tenant=%s: %s", current_user.slug, exc)
        raise HTTPException(status_code=500, detail="Failed to list providers.")


@router.post("", status_code=201)
async def create_provider(
    req: ProviderCreateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Create a new provider for the authenticated tenant."""
    logger.info("[Providers] Creating provider '%s' for tenant=%s", req.name, current_user.slug)
    try:
        provider = await provider_service.create_provider(
            tenant_id=current_user.id,
            name=req.name,
            title=req.title,
            appointment_types=req.appointment_types,
            calendar_id=req.calendar_id,
            business_hours_override=req.business_hours_override,
            slot_capacity=req.slot_capacity,
            specialty=req.specialty,
            trial_session_slots={
                day: [s.model_dump() for s in slots] if slots else None
                for day, slots in req.trial_session_slots.items()
            } if req.trial_session_slots else None,
        )
        return provider
    except Exception as exc:
        logger.error("[Providers] Failed to create provider for tenant=%s: %s", current_user.slug, exc)
        raise HTTPException(status_code=500, detail="Failed to create provider.")


@router.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    req: ProviderUpdateRequest,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Update an existing provider."""
    logger.info("[Providers] Updating provider %s for tenant=%s", provider_id, current_user.slug)

    # Only include explicitly set fields
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        provider = await provider_service.update_provider(provider_id, update_data)
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found.")
        return provider
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Providers] Failed to update provider %s: %s", provider_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update provider.")


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """Soft-delete (deactivate) a provider."""
    logger.info("[Providers] Deactivating provider %s for tenant=%s", provider_id, current_user.slug)
    try:
        result = await provider_service.delete_provider(provider_id)
        if not result:
            raise HTTPException(status_code=404, detail="Provider not found.")
        return {"status": "deactivated", "provider_id": provider_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Providers] Failed to delete provider %s: %s", provider_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete provider.")
