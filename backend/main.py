"""
FitFront — Multi-tenant AI front desk for fitness studios — FastAPI entry point.

Startup sequence:
  1. Connect to PostgreSQL and auto-create tables
  2. Load knowledge base (per-tenant from DB, fallback to default_kb.json)
  3. Mount API routes and serve React dashboard as static files
  4. Start on port 8000
"""
from __future__ import annotations


import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import init_db
from backend.services.knowledge_service import load_knowledge_base
from backend.services.reminder_service import run_reminder_loop

# ── Routes ────────────────────────────────────────────────────────────────────
from backend.routes.calls import router as calls_router
from backend.routes.appointments import router as appointments_router
from backend.routes.dashboard import router as dashboard_router
from backend.routes.llm_proxy import router as llm_proxy_router
from backend.routes.tenants import router as tenants_router
from backend.routes.auth import router as auth_router
from backend.routes.chat import router as chat_router
from backend.routes.google_oauth import router as google_oauth_router
from backend.routes.providers import router as providers_router
from backend.routes.waitlist import router as waitlist_router
from backend.routes.sms_webhook import router as sms_webhook_router
from backend.routes.sms_messages import router as sms_messages_router
from backend.routes.callers import router as callers_router
from backend.routes.support_tickets import router as support_tickets_router
from backend.routes.tenant_tools import router as tenant_tools_router
from backend.routes.bolna import router as bolna_router
from backend.routes.platform_admin import router as platform_admin_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fitfront")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

def _log_config_status():
    """Log which integrations are configured vs missing at startup."""
    logger.info("─── Configuration Check ───")

    # Build checks list based on LLM provider
    checks = [
        ("Database URL", settings.DATABASE_URL, True),
    ]

    # Add provider-specific checks
    if settings.LLM_PROVIDER == "ollama":
        checks.extend([
            ("Ollama URL", settings.OLLAMA_BASE_URL, True),
            ("Ollama Model", settings.OLLAMA_MODEL, True),
        ])
    elif settings.LLM_PROVIDER == "gemini":
        checks.extend([
            ("Gemini API Key", settings.GEMINI_API_KEY, True),
            ("Gemini Model", settings.GEMINI_MODEL, True),
        ])

    # Add common optional checks
    checks.extend([
        ("Twilio Account SID", settings.TWILIO_ACCOUNT_SID, False),
        ("Twilio Auth Token", settings.TWILIO_AUTH_TOKEN, False),
        ("Twilio Phone Number", settings.TWILIO_PHONE_NUMBER, False),
        ("Bolna API Key", settings.BOLNA_API_KEY, False),
        ("Bolna Agent ID", settings.BOLNA_AGENT_ID, False),
        ("Escalation Phone", settings.ESCALATION_PHONE_NUMBER, False),
    ])

    missing = []
    for name, value, required in checks:
        if value:
            # Mask sensitive values in logs
            masked = value[:8] + "..." if len(value) > 12 else "***set***"
            logger.info("  ✓ %-30s %s", name, masked)
        elif required:
            logger.error("  ✗ %-30s MISSING (required!)", name)
            missing.append(name)
        else:
            logger.warning("  ○ %-30s not set (optional)", name)

    logger.info("  %-30s %s", "LLM Provider", settings.LLM_PROVIDER.upper())
    logger.info("  %-30s %s", "Demo Mode", "ON" if settings.DEMO_MODE else "OFF")
    logger.info("  %-30s %s", "Google Calendar OAuth",
                "ON" if (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET) else "OFF (set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET)")
    logger.info("  %-30s %s", "Local Chat Mode", "ON" if settings.LOCAL_CHAT_MODE else "OFF")
    logger.info("  %-30s %s", "Server Base URL", settings.SERVER_BASE_URL)
    logger.info("  %-30s %s", "Office Timezone", settings.OFFICE_TIMEZONE)
    logger.info("─── End Configuration Check ───")

    if missing:
        logger.error("FATAL: Required config missing: %s", ", ".join(missing))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks before the server accepts requests."""
    logger.info("=" * 60)
    logger.info("  FitFront — AI Front Desk — Starting up")
    logger.info("=" * 60)

    # 0. Config validation
    _log_config_status()

    # 1. Database
    logger.info("Connecting to PostgreSQL...")
    try:
        await init_db()
        logger.info("✓ Database connected and tables verified.")
    except Exception as exc:
        logger.error("✗ Database connection failed: %s", exc)
        logger.error("  → Is PostgreSQL running? Try: docker-compose up -d")
        raise

    # 1a. Seed default admin user
    try:
        from backend.services.auth_service import ensure_admin_exists
        await ensure_admin_exists()
    except Exception as exc:
        logger.error("⚠ Admin seeding failed: %s", exc)

    # 2. Knowledge base
    kb = load_knowledge_base()
    logger.info("✓ Knowledge base loaded (%d sections).", len(kb))

    # 3. LLM provider check
    if settings.LLM_PROVIDER == "ollama":
        logger.info("Checking Ollama at %s ...", settings.OLLAMA_BASE_URL)
        try:
            from backend.services.http_client import http
            resp = await http.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                if any(settings.OLLAMA_MODEL in m for m in models):
                    logger.info("✓ Ollama running — model '%s' available.", settings.OLLAMA_MODEL)
                else:
                    logger.warning("⚠ Ollama running but model '%s' not found. Available: %s",
                                   settings.OLLAMA_MODEL, models)
                    logger.warning("  → Run: ollama pull %s", settings.OLLAMA_MODEL)
            else:
                logger.warning("⚠ Ollama responded with HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("⚠ Cannot reach Ollama at %s: %s", settings.OLLAMA_BASE_URL, exc)
            logger.warning("  → Is Ollama running? Start it with: ollama serve")
    elif settings.LLM_PROVIDER == "gemini":
        if settings.GEMINI_API_KEY:
            logger.info("✓ Using Gemini API — model: %s", settings.GEMINI_MODEL)
        else:
            logger.error("✗ LLM_PROVIDER is 'gemini' but GEMINI_API_KEY is not set!")
            logger.error("  → Get your key at: https://aistudio.google.com/app/apikey")
    else:
        logger.warning("⚠ Unknown LLM_PROVIDER: %s", settings.LLM_PROVIDER)

    logger.info("=" * 60)
    logger.info("  %s AI Agent running at %s", settings.OFFICE_NAME, settings.SERVER_BASE_URL)
    if settings.DEMO_MODE:
        logger.info("  ⚡ DEMO MODE active — SMS and calendar calls are simulated")
    else:
        logger.info("  🔴 LIVE MODE — real API calls to Google Calendar, Twilio, Bolna")
    logger.info("")
    logger.info("  Dashboard:  %s", settings.SERVER_BASE_URL)
    logger.info("  API Docs:   %s/docs", settings.SERVER_BASE_URL)
    logger.info("  Health:     %s/health", settings.SERVER_BASE_URL)
    logger.info("  SMS Webhook: %s/webhook/sms", settings.SERVER_BASE_URL)
    logger.info("=" * 60)

    # ── Startup health checks — warn about missing / insecure config ─────
    _jwt_secret = os.getenv("JWT_SECRET", "")
    if not _jwt_secret or "change-me" in _jwt_secret:
        logger.warning("⚠️  JWT_SECRET is not set or uses the insecure default! "
                        "Set a strong secret in .env before going to production.")

    if not settings.DEMO_MODE:
        if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
            logger.warning("⚠️  Twilio credentials missing — outbound SMS will silently fail.")
        if not settings.TWILIO_PHONE_NUMBER:
            logger.warning("⚠️  TWILIO_PHONE_NUMBER is empty — inbound SMS won't match a tenant.")
        if settings.SERVER_BASE_URL.startswith("http://localhost"):
            logger.warning("⚠️  SERVER_BASE_URL is localhost — Twilio webhooks won't reach "
                            "this server. Use a tunnel (ngrok) or set a public URL.")
    if settings.LLM_PROVIDER == "gemini" and not settings.GEMINI_API_KEY:
        logger.warning("⚠️  LLM_PROVIDER=gemini but GEMINI_API_KEY is empty — LLM calls will fail.")
    if settings.LLM_PROVIDER == "ollama":
        logger.info("  LLM: Ollama (%s) at %s", settings.OLLAMA_MODEL, settings.OLLAMA_BASE_URL)

    # 5. Start background reminder/follow-up scheduler
    reminder_task = asyncio.create_task(run_reminder_loop())
    logger.info("✓ Reminder scheduler started (2h reminders + post-visit follow-ups)")

    yield  # Server is running

    # Shut down background tasks
    reminder_task.cancel()
    try:
        await reminder_task
    except asyncio.CancelledError:
        pass

    # Close shared HTTP client pool
    from backend.services.http_client import close_http_client
    await close_http_client()

    logger.info("FitFront shutting down.")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="FitFront API",
    description="Multi-tenant AI voice agent platform with scheduling, SMS, and admin dashboard.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the React dev server and production frontend.
# NOTE: allow_origins=["*"] with allow_credentials=True is invalid per the
# CORS spec (browsers will reject it). Build a safe list from SERVER_BASE_URL.
_cors_origins: list[str] = [
    "http://localhost:5173",   # Vite dev server
    "http://localhost:3000",   # Alternate dev server
]
if settings.SERVER_BASE_URL and settings.SERVER_BASE_URL not in _cors_origins:
    _cors_origins.append(settings.SERVER_BASE_URL)
# Production frontend on Vercel (or any separate domain)
_frontend_url = os.getenv("FRONTEND_URL", "")
if _frontend_url and _frontend_url not in _cors_origins:
    _cors_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request logging middleware ─────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every incoming request with method, path, and response time."""
    start = time.time()
    method = request.method
    path = request.url.path

    # Skip noisy static asset requests
    if path.startswith("/assets/") or path.endswith((".js", ".css", ".ico", ".svg", ".png")):
        return await call_next(request)

    logger.info("→ %s %s", method, path)

    try:
        response = await call_next(request)
        elapsed = (time.time() - start) * 1000
        logger.info("← %s %s → %d (%.0fms)", method, path, response.status_code, elapsed)
        return response
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.error("← %s %s → ERROR (%.0fms): %s", method, path, elapsed, exc)
        raise


# ── API routes ────────────────────────────────────────────────────────────────

app.include_router(calls_router)
app.include_router(appointments_router)
app.include_router(dashboard_router)
app.include_router(llm_proxy_router)
app.include_router(tenants_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(google_oauth_router)
app.include_router(providers_router, prefix="/api/trainers")
app.include_router(providers_router, prefix="/api/providers")
app.include_router(waitlist_router)
app.include_router(sms_webhook_router)
app.include_router(sms_messages_router)
app.include_router(callers_router)
app.include_router(support_tickets_router)
app.include_router(tenant_tools_router)
app.include_router(bolna_router)
app.include_router(platform_admin_router)


@app.get("/health")
async def health_check():
    """Simple health check for uptime monitors."""
    return {
        "status": "healthy",
        "service": "FitFront",
        "demo_mode": settings.DEMO_MODE,
    }


# ── Serve React dashboard ────────────────────────────────────────────────────

FRONTEND_BUILD = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_BUILD.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_BUILD / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA — all non-API routes return index.html."""
        file_path = FRONTEND_BUILD / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_BUILD / "index.html")
else:
    @app.get("/")
    async def root():
        return {
            "message": "FitFront API",
            "docs": "/docs",
            "dashboard": "Build the frontend first: cd frontend && npm run build",
        }


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
