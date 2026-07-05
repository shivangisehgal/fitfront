"""
Google Calendar OAuth routes — connect, callback, disconnect, status.

These routes handle the OAuth 2.0 flow for linking a tenant's Google Calendar.
The platform owns one OAuth app; each tenant gets their own refresh_token.

Flow:
  1. Tenant clicks "Connect Google Calendar" → frontend opens /api/integrations/google/connect
  2. Browser redirects to Google consent screen
  3. Google redirects back to /api/integrations/google/callback with ?code=...
  4. We exchange the code for tokens, store refresh_token + email on the tenant
  5. Frontend polls /api/integrations/google/status to confirm connection

Routes:
  GET  /api/integrations/google/connect     → redirect to Google consent
  GET  /api/integrations/google/callback    → handle OAuth callback
  POST /api/integrations/google/disconnect  → revoke + clear tokens
  GET  /api/integrations/google/status      → check connection status
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from backend.database import async_session
from backend.models.tenant import Tenant
from backend.services import auth_service, tenant_service
from backend.services import google_calendar as gcal
from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/google", tags=["Google Calendar"])


@router.get("/connect")
async def google_connect(
    current_user: Tenant = Depends(auth_service.get_current_user),
    redirect_uri: str = Query(None, description="Frontend URL to redirect back to after OAuth"),
):
    """
    Start the Google OAuth flow. Returns the Google consent URL as JSON
    so the frontend can redirect the browser (with the JWT auth handled
    via the fetch call, not via a plain <a href> navigation).
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google Calendar integration is not configured on the platform. "
                   "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.",
        )

    # Encode tenant ID and frontend redirect URI in state (separated by |)
    # This ensures we redirect back to the same origin the user came from
    state = str(current_user.id)
    if redirect_uri:
        state = f"{current_user.id}|{redirect_uri}"
    auth_url = gcal.build_auth_url(state=state)
    logger.info("[GoogleOAuth] Generated auth URL for tenant %s (redirect=%s)", current_user.slug, redirect_uri or "default")
    return {"auth_url": auth_url}


@router.get("/callback")
async def google_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="Tenant ID (and optional redirect URI) passed through OAuth state"),
):
    """
    OAuth callback — Google redirects here after the user grants consent.
    We exchange the code for tokens and store the refresh_token on the tenant.
    Then redirect to the frontend settings page.
    """
    logger.info("[GoogleOAuth] Callback received for state=%s", state)

    # Parse state: "tenant_id" or "tenant_id|redirect_uri"
    frontend_redirect = None
    if "|" in state:
        tenant_id_str, frontend_redirect = state.split("|", 1)
    else:
        tenant_id_str = state

    # Validate tenant ID
    try:
        tenant_id = uuid.UUID(tenant_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state parameter.")

    # Exchange authorization code for tokens
    try:
        token_data = await gcal.exchange_code(code)
    except Exception as exc:
        logger.error("[GoogleOAuth] Token exchange failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to exchange authorization code: {exc}",
        )

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")

    if not refresh_token:
        logger.error("[GoogleOAuth] No refresh_token in response — user may have already granted access. "
                     "Revoke and reconnect.")
        raise HTTPException(
            status_code=400,
            detail="Google did not return a refresh token. Please revoke access at "
                   "https://myaccount.google.com/permissions and try again.",
        )

    # Fetch the user's email for display
    try:
        email = await gcal.get_user_email(access_token)
    except Exception:
        email = ""

    # Save to tenant record
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        tenant.google_calendar_refresh_token = refresh_token
        tenant.google_calendar_email = email
        tenant.google_calendar_connected = True
        await session.commit()

    # Invalidate cache so the new credentials are picked up
    tenant_service.invalidate_cache(str(tenant_id))

    logger.info("[GoogleOAuth] Connected Google Calendar for tenant %s (email=%s)",
                tenant_id_str, email)

    # Redirect to the frontend settings page with a success flag.
    # If a frontend_redirect was provided (full URL), use it to ensure we go back
    # to the same origin the user came from (important for tunnel URLs where
    # localStorage/JWT is origin-specific).
    if frontend_redirect:
        # Ensure it ends with the settings path and has the success param
        redirect_url = frontend_redirect.rstrip("/")
        if not redirect_url.endswith("/settings"):
            redirect_url += "/settings"
        redirect_url += "?google_connected=true"
        logger.info("[GoogleOAuth] Redirecting to frontend: %s", redirect_url)
        return RedirectResponse(url=redirect_url)

    # Fallback: relative path (works when callback and frontend are same origin)
    return RedirectResponse(url="/settings?google_connected=true")


@router.post("/disconnect")
async def google_disconnect(
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Disconnect Google Calendar — clear the stored tokens from the tenant.
    The user should also revoke access at https://myaccount.google.com/permissions.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == current_user.id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        tenant.google_calendar_refresh_token = None
        tenant.google_calendar_email = None
        tenant.google_calendar_connected = False
        await session.commit()

    tenant_service.invalidate_cache(str(current_user.id))
    logger.info("[GoogleOAuth] Disconnected Google Calendar for tenant %s", current_user.slug)

    return {"status": "disconnected", "message": "Google Calendar has been disconnected."}


@router.get("/status")
async def google_status(
    current_user: Tenant = Depends(auth_service.get_current_user),
):
    """
    Check whether Google Calendar is connected for the current tenant.
    Returns connection status and the connected email address.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == current_user.id)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

    return {
        "connected": bool(tenant.google_calendar_connected),
        "email": tenant.google_calendar_email or "",
        "platform_configured": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
    }
