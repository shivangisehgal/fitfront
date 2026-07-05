"""
Application configuration — loads all settings from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Central configuration loaded from .env file."""

    # ── LLM Backend ───────────────────────────────────────────────────────
    # LLM_PROVIDER: "ollama" (local) or "gemini" (Google free tier)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")

    # Ollama (local LLM)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    # Gemini (Google free tier — 15 RPM, 1500/day for gemini-3.5-flash)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    # Max chat-history messages sent to Gemini per request. The full transcript
    # is still saved in the session — only what's *forwarded* to the model is
    # trimmed. Default is intentionally generous (200) because each tool round
    # adds 2–3 history entries; a heavy booking call can easily hit 30+
    # entries by turn 3. Set lower only if you're truly hitting context limits.
    GEMINI_HISTORY_MAX_MESSAGES: int = int(os.getenv("GEMINI_HISTORY_MAX_MESSAGES", "200"))
    # Enable explicit context caching for the system prompt + tools. When True,
    # we create a CachedContent per tenant and reuse it across calls. Cuts
    # per-call cost ~75% on the cached prefix. May not be supported by all
    # models — leave False if you see "caching not supported" errors.
    GEMINI_USE_CONTEXT_CACHE: bool = os.getenv("GEMINI_USE_CONTEXT_CACHE", "false").lower() == "true"
    # TTL (seconds) for explicit context caches. Default 1 hour.
    GEMINI_CACHE_TTL_SECONDS: int = int(os.getenv("GEMINI_CACHE_TTL_SECONDS", "3600"))

    # ── Bolna AI (global outbound calling — admin-configured) ─────────────
    # These are fallback env-var values.  The live values are stored in the
    # platform_config DB table and editable via the admin portal at runtime.
    BOLNA_API_KEY: str = os.getenv("BOLNA_API_KEY", "")
    BOLNA_AGENT_ID: str = os.getenv("BOLNA_AGENT_ID", "")

    # ── ElevenLabs (voice preview) ───────────────────────────────────────
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")

    # ── Twilio ────────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")

    # ── PostgreSQL ────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://scheduler_user:scheduler_pass@localhost:5432/scheduler_ai",
    )

    # ── App ───────────────────────────────────────────────────────────────
    # ESCALATION_PHONE_NUMBER → number that receives the SMS alert when a
    #   caller asks for a human. Set this to the office cell / manager's phone.
    # ESCALATION_TRANSFER_NUMBER → if set, Vapi will live-transfer the caller
    #   to this number via the `transferCall` predefined tool. Leave blank to
    #   instead end the call gracefully with a "we'll call you back" message
    #   (recommended unless you have a real staff member on the other end).
    ESCALATION_PHONE_NUMBER: str = os.getenv("ESCALATION_PHONE_NUMBER", "")
    ESCALATION_TRANSFER_NUMBER: str = os.getenv("ESCALATION_TRANSFER_NUMBER", "")
    OFFICE_TIMEZONE: str = os.getenv("OFFICE_TIMEZONE", "America/Chicago")
    OFFICE_NAME: str = os.getenv("OFFICE_NAME", "FitFront")
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"
    SERVER_BASE_URL: str = os.getenv("SERVER_BASE_URL", "http://localhost:8000")

    # ── Google Calendar OAuth (platform-level — ONE app for all tenants) ──
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/integrations/google/callback"
    )

    # ── Feature flags (global kill switches) ────────────────────────────
    # When False, the feature is unavailable for ALL tenants regardless of
    # their own per-tenant setting. Useful for running the platform in
    # text-only mode when SMS isn't licensed.
    FEATURE_TWILIO_ENABLED: bool = os.getenv("FEATURE_TWILIO_ENABLED", "true").lower() == "true"

    # ── Local chat mode ───────────────────────────────────────────────────
    # When True, the frontend exposes a /chat page that talks to the same
    # LLM + tool pipeline, but via text instead of voice. Useful for local
    # dev. Responses are streamed back as SSE.
    LOCAL_CHAT_MODE: bool = os.getenv("LOCAL_CHAT_MODE", "false").lower() == "true"

    # ── Derived ───────────────────────────────────────────────────────────
    @property
    def ollama_openai_base(self) -> str:
        """OpenAI-compatible base URL for Ollama."""
        return f"{self.OLLAMA_BASE_URL}/v1"



settings = Settings()
