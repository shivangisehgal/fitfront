"""
LLM service — stateful conversation manager using Ollama
via the OpenAI-compatible API (model configured in .env as OLLAMA_MODEL).

Maintains per-call session state in an in-memory dict keyed by vapi_call_id.
Implements tool-calling for appointment operations and escalation.

Multi-tenant: when a TenantContext is provided, tool definitions are filtered
based on what integrations the tenant has configured (e.g. hide escalation
tools when Twilio isn't set up) and all downstream service calls receive the
tenant context so they use the correct credentials.
"""
from __future__ import annotations


import asyncio
import json
import logging
import re as _re
import time
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI, OpenAI

# New `google-genai` SDK — supports ThinkingConfig with thinking_level=MINIMAL
# which turns OFF Gemma's chain-of-thought reasoning at the source.
# This replaces the deprecated `google-generativeai` v0.8.6 SDK.
from google import genai
from google.genai import types as gtypes

from backend.config import settings
from backend.defaults import DEFAULT_TIMEZONE
from backend.services import calendar_service, sms_service, knowledge_service
from backend.prompts.agent_prompt import build_system_prompt

logger = logging.getLogger(__name__)


# ── Tool-call-as-text detection ───────────────────────────────────────────────
# Some models (Qwen3, etc.) output tool calls as text tags instead of using
# the proper function calling API. These helpers detect, strip, and parse them.

_TOOL_TAG_PATTERNS = [
    ("<tool_call>", "</tool_call>"),
    ("<function_call>", "</function_call>"),
    ("<|tool_call|>", "<|/tool_call|>"),
    ("<|function|>", "<|/function|>"),
    ("<tool>", "</tool>"),
    ("<function>", "</function>"),
]


def _has_tool_tag_start(text: str) -> bool:
    return any(start in text for start, _ in _TOOL_TAG_PATTERNS)


def _has_tool_tag_end(text: str) -> bool:
    return any(end in text for _, end in _TOOL_TAG_PATTERNS)


def _strip_tool_block(text: str) -> tuple[str, str]:
    """Strip a tool call block from text, returning (before, after)."""
    for start_tag, end_tag in _TOOL_TAG_PATTERNS:
        if start_tag in text and end_tag in text:
            before = text.split(start_tag, 1)[0]
            after = text.split(end_tag, 1)[1] if end_tag in text else ""
            return before, after
    return text, ""


def _parse_tool_call_text(text: str) -> dict | None:
    """
    Parse a tool call from text-based output (e.g., <tool_call>{...}</tool_call>).
    Returns dict with 'name' and 'arguments' or None if parsing fails.
    """
    for start_tag, end_tag in _TOOL_TAG_PATTERNS:
        pat = _re.escape(start_tag) + r"\s*(\{.*?\})\s*" + _re.escape(end_tag)
        match = _re.search(pat, text, _re.DOTALL)
        if match:
            try:
                tc_json = json.loads(match.group(1))
                fn_name = tc_json.get("name", "")
                fn_args = tc_json.get("arguments", {})
                if isinstance(fn_args, str):
                    fn_args = json.loads(fn_args)
                return {"name": fn_name, "arguments": fn_args}
            except (json.JSONDecodeError, KeyError):
                continue
    return None


# ── In-memory session store ──────────────────────────────────────────────────
# Keyed by vapi_call_id. Each session holds conversation state for one call.

sessions: dict[str, dict[str, Any]] = {}

# Maximum age (seconds) for a session before it's considered abandoned.
# Vapi calls rarely last more than 30 minutes; 2 hours is very generous.
_SESSION_MAX_AGE = 2 * 60 * 60  # 2 hours


def _cleanup_stale_sessions() -> int:
    """Remove sessions older than _SESSION_MAX_AGE. Returns count removed."""
    now = time.time()
    stale = [
        cid for cid, s in sessions.items()
        if now - s.get("call_start_time", now) > _SESSION_MAX_AGE
    ]
    for cid in stale:
        sessions.pop(cid, None)
    if stale:
        logger.warning("Cleaned up %d stale session(s): %s", len(stale),
                        ", ".join(stale[:5]))
    return len(stale)


def _refresh_system_time(session: dict[str, Any]) -> None:
    """Update the CURRENT TIME line in the system prompt so the agent always
    knows the real time, not the time when the session was created."""
    import re
    from zoneinfo import ZoneInfo
    msgs = session.get("messages")
    if not msgs or msgs[0].get("role") != "system":
        return
    tenant_ctx = session.get("tenant_ctx")
    tz_name = getattr(tenant_ctx, "timezone", None) or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    time_str_24 = now.strftime("%H:%M")
    time_str_12 = now.strftime("%I:%M %p").lstrip("0")
    new_line = f"CURRENT TIME is {time_str_24} ({time_str_12}) in {tz_name}."
    msgs[0]["content"] = re.sub(
        r"CURRENT TIME is .+?\.",
        new_line,
        msgs[0]["content"],
        count=1,
    )

# ── OpenAI client pointed at Ollama ──────────────────────────────────────────

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        logger.info("Initializing OpenAI client → Ollama at %s (model: %s)",
                     settings.ollama_openai_base, settings.OLLAMA_MODEL)
        _client = OpenAI(
            base_url=settings.ollama_openai_base,
            api_key="ollama",  # Ollama ignores the key but the SDK requires one
            timeout=45.0,  # 45s timeout to prevent indefinite hangs
        )
    return _client


_async_client: AsyncOpenAI | None = None


def _get_async_client() -> AsyncOpenAI:
    """Async client for true token-by-token streaming from Ollama."""
    global _async_client
    if _async_client is None:
        logger.info("Initializing AsyncOpenAI (streaming) → Ollama at %s",
                     settings.ollama_openai_base)
        _async_client = AsyncOpenAI(
            base_url=settings.ollama_openai_base,
            api_key="ollama",
            timeout=45.0,  # 45s timeout to prevent indefinite hangs
        )
    return _async_client


# ── Gemini client (google-genai SDK) ─────────────────────────────────────────

_gemini_client: genai.Client | None = None


def _get_gemini_client() -> genai.Client:
    """Lazily create a singleton google-genai Client."""
    global _gemini_client
    if _gemini_client is None:
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set in .env")
        _gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini client initialized — model: %s", settings.GEMINI_MODEL)
    return _gemini_client


def _build_gemini_config(
    system_instruction: str = "",
    tools: list | None = None,
    cached_content: str | None = None,
) -> gtypes.GenerateContentConfig:
    """
    Build a GenerateContentConfig with ThinkingLevel.MINIMAL.

    For Gemma 4 (and other thinking-capable models) MINIMAL turns the
    chain-of-thought OFF, so the model returns just the final response
    without reasoning leakage.

    If `cached_content` is provided, system_instruction and tools come from
    the cache (cheaper) and we pass only the dynamic per-call portions.
    """
    kwargs: dict[str, Any] = {
        "temperature": 0.7,
        "max_output_tokens": 1024,
        "thinking_config": gtypes.ThinkingConfig(
            thinking_level=gtypes.ThinkingLevel.MINIMAL,
        ),
    }
    if cached_content:
        kwargs["cached_content"] = cached_content
    else:
        # Only set these when NOT using a cache — caches already contain them.
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if tools:
            kwargs["tools"] = tools
    return gtypes.GenerateContentConfig(**kwargs)


# ── Gemini history trimming ──────────────────────────────────────────────────

def _trim_gemini_history(
    history: list["gtypes.Content"],
    max_messages: int,
) -> list["gtypes.Content"]:
    """
    Trim chat history to the last `max_messages` entries.

    Preserves function_call ↔ function_response pairs: if the trim boundary
    falls in the middle of a tool-call/tool-response pair, we drop the
    dangling function_response(s) from the front so Gemini doesn't error
    with "function_response without preceding function_call".
    """
    if max_messages <= 0 or len(history) <= max_messages:
        return history

    trimmed = history[-max_messages:]

    # Drop any function_response(s) at the head with no matching call.
    while trimmed:
        first = trimmed[0]
        has_fn_response = (
            getattr(first, "role", None) == "user"
            and getattr(first, "parts", None)
            and any(getattr(p, "function_response", None) for p in first.parts)
        )
        if has_fn_response:
            trimmed = trimmed[1:]
        else:
            break

    logger.info(
        "[Gemini] Trimmed history %d → %d messages (max=%d)",
        len(history), len(trimmed), max_messages,
    )
    return trimmed


# ── Gemini explicit context cache pool ───────────────────────────────────────

# Per-tenant cache: tenant_id (or "default") → (cache_name, expires_at_unix)
# The cache stores the static system prompt + tool declarations so we don't
# re-send ~5–8K tokens of prefix on every call. Gemini bills cached input at
# ~25% of the regular rate.
_gemini_cache_pool: dict[str, tuple[str, float]] = {}


def _gemini_cache_key(tenant_ctx: Any | None, model: str) -> str:
    """Stable cache pool key per (tenant, model)."""
    if tenant_ctx is None:
        return f"default::{model}"
    return f"{getattr(tenant_ctx, 'id', 'default')}::{model}"


async def _get_or_create_gemini_cache(
    system_prompt: str,
    tools: list | None,
    tenant_ctx: Any | None,
) -> str | None:
    """
    Look up or lazily create a CachedContent for this (tenant, model).

    Returns the cache resource name if successful, or None if caching
    isn't supported / failed (so callers can fall back to inline send).
    """
    if not settings.GEMINI_USE_CONTEXT_CACHE:
        return None
    if not system_prompt and not tools:
        return None

    key = _gemini_cache_key(tenant_ctx, settings.GEMINI_MODEL)
    now = time.time()
    cached = _gemini_cache_pool.get(key)
    # 60s safety buffer — refresh before TTL actually expires
    if cached and cached[1] > now + 60:
        return cached[0]

    try:
        client = _get_gemini_client()
        cache = await client.aio.caches.create(
            model=settings.GEMINI_MODEL,
            config=gtypes.CreateCachedContentConfig(
                system_instruction=system_prompt or None,
                tools=tools,
                ttl=f"{settings.GEMINI_CACHE_TTL_SECONDS}s",
            ),
        )
        expires_at = now + settings.GEMINI_CACHE_TTL_SECONDS
        _gemini_cache_pool[key] = (cache.name, expires_at)
        logger.info(
            "[Gemini] Created context cache %s for %s (TTL=%ds)",
            cache.name, key, settings.GEMINI_CACHE_TTL_SECONDS,
        )
        return cache.name
    except Exception as exc:
        # Common failure modes: model doesn't support caching, prompt too
        # small (<1024 tokens), API quota. Disable for this key so we don't
        # retry hot in a loop.
        logger.warning(
            "[Gemini] Context cache creation failed for %s — falling back to "
            "inline send. (%s: %s)",
            key, type(exc).__name__, exc,
        )
        # Cache the failure for a minute so we don't hammer the API.
        _gemini_cache_pool[key] = ("", now + 60)
        return None


# ── Tool definitions (sent to the LLM for function calling) ──────────────────
#
# Tool schemas and dispatch logic live in backend/tools/platform.py.
# ALL_TOOLS and get_tools() are kept here as backward-compat shims so that
# llm_proxy.py and other importers continue to work without changes.
#
# For new code, use get_registry() from backend.tools.registry directly.

from backend.tools.platform import TOOL_SCHEMAS as ALL_TOOLS  # noqa: E402 (re-export)
from backend.tools.platform import _build_office_info, _resolve_dow_to_date  # noqa: F401 (re-export)

# llm_proxy.py also imports TOOLS — forward it
TOOLS = ALL_TOOLS



def get_tools(tenant_ctx: Any | None = None) -> list[dict]:
    """
    Return the tool definitions appropriate for `tenant_ctx`.

    Backward-compatible shim — delegates to PlatformToolProvider.list_tools_sync().
    Platform tools only (no custom tools — those require an async call to the registry).

    For the full tool list including tenant custom tools, use:
        await get_registry().get_tools(tenant_ctx)
    """
    from backend.tools.platform import PlatformToolProvider
    return PlatformToolProvider().list_tools_sync(tenant_ctx)


# ── Simple-chat detection (suppress tools for greetings / small talk) ─────────

def _looks_like_simple_chat(user_message: str, session_messages: list | None = None) -> bool:
    """
    Heuristic: when True, suppress tool definitions for this turn so the
    model replies conversationally instead of hallucinating a tool call on
    a bare 'hi' or 'how are you'.

    CRITICAL: Only applies to the FIRST user message in a conversation.
    Once a multi-turn flow is underway (e.g. booking), tools must ALWAYS
    be available — the user might respond with short answers like "doc1",
    "yes", "2pm", etc. that don't match any keyword but still need tools.
    """
    # If we're past the first exchange, NEVER suppress tools.
    # The user could be responding to a provider selection, time slot question, etc.
    if session_messages and len(session_messages) > 2:
        # More than system prompt + first user message → conversation in progress
        return False

    text = user_message.strip().lower()
    if not text or len(text) > 80:
        return False
    tool_keywords = [
        # Scheduling
        "book", "schedule", "appointment", "slot", "available", "availability",
        "reschedule", "cancel", "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday", "tomorrow", "today", "next week",
        "this week", "morning", "afternoon", "evening", "am", "pm",
        ":", "o'clock", "9", "10", "11", "12", "1pm", "2pm", "3pm", "4pm", "5pm",
        # Emergency / escalation
        "emergency", "urgent", "pain", "broken", "knocked",
        "human", "person", "transfer", "callback", "call back",
        # Office info (triggers get_office_info tool)
        "hour", "hours", "open", "close", "location", "address", "where",
        "phone", "number", "service", "pricing",
        "price", "cost", "how much", "faq", "question",
        "do you", "can you", "what do", "offer",
        # Procedure/treatment questions (trigger FAQ lookup)
        "how long", "procedure", "treatment", "duration", "take",
        "rct", "root canal", "filling", "crown", "extraction", "cleaning",
        # Caller lookup / CRM
        "my appointment", "existing", "upcoming", "check on",
        "do i have", "any appointment", "look up", "find my",
        "my record", "my info", "my details", "date of birth", "dob",
        "update my",
    ]
    return not any(kw in text for kw in tool_keywords)


# ── Session management ────────────────────────────────────────────────────────


def create_session(
    call_id: str,
    caller_number: str = "",
    tenant_ctx: Any | None = None,
    caller_context: dict | None = None,  # deprecated — ignored; caller data fetched lazily via lookup_caller
    caller_name: str = "",
    is_test: bool = False,
    providers: list[dict] | None = None,
) -> dict[str, Any]:
    """Initialise a new conversation session for a call.

    caller_name is a lightweight pre-fetch (name only) used for the greeting.
    Full caller profile is fetched lazily via lookup_caller when needed.

    Args:
        caller_context: DEPRECATED — no longer injected into the system prompt.
            Kept for backwards-compat with call sites that still pass it.
        caller_name: Caller's name from a quick DB lookup — used only for
            the personalised opening greeting ("Hi Ravi!").
        is_test: When True, all data created during this session (callers,
            appointments, waitlist entries, SMS logs) will be flagged as test
            data so it can be filtered out of production views.
        providers: Pre-fetched provider list for injecting the authoritative
            demo subjects section. Pass from async callers to avoid sync/async mismatch.
    """
    session = {
        "messages": [
            {
                "role": "system",
                "content": build_system_prompt(
                    tenant_ctx=tenant_ctx,
                    caller_phone=caller_number,
                    caller_name=caller_name,
                    providers=providers,
                ),
            }
        ],
        "caller_info": {},  # populated lazily when lookup_caller tool fires
        "current_state": "greeting",
        "call_start_time": time.time(),
        "caller_number": caller_number,
        "tenant_ctx": tenant_ctx,  # stored so _execute_tool can use it
        "is_test": is_test,        # True for Test Agent chat sessions
    }
    # Housekeeping: clean up any abandoned sessions before adding a new one.
    # Cheap check — only runs the sweep when session count grows beyond 50.
    if len(sessions) > 50:
        _cleanup_stale_sessions()

    sessions[call_id] = session
    logger.info("Session created for call %s (tenant=%s, caller=%s [%s], active=%d)",
                call_id,
                tenant_ctx.slug if tenant_ctx else "global",
                caller_name or "new caller",
                caller_number or "no caller-ID",
                len(sessions))
    return session


def get_session(call_id: str) -> dict[str, Any] | None:
    return sessions.get(call_id)


def end_session(call_id: str) -> dict[str, Any] | None:
    """Remove and return the session for final persistence."""
    session = sessions.pop(call_id, None)
    if session:
        logger.info("Session ended for call %s (duration %.0fs).",
                     call_id, time.time() - session["call_start_time"])
    return session


# ── Gemini helpers (format conversion) ──────────────────────────────────────


def _extract_gemini_text(response) -> str:
    """Extract only the spoken-text parts from a Gemini response,
    skipping 'thought' parts that contain chain-of-thought reasoning."""
    try:
        parts = response.candidates[0].content.parts
    except (IndexError, AttributeError):
        return ""

    text_parts = []
    for part in parts:
        # Skip thought/thinking parts (Gemma thinking_config)
        if hasattr(part, "thought") and part.thought:
            continue
        # Skip function calls
        if part.function_call and part.function_call.name:
            continue
        # Collect actual text
        if hasattr(part, "text") and part.text:
            text_parts.append(part.text)

    return "".join(text_parts).strip()


def _openai_tools_to_gemini(tools: list[dict]) -> list | None:
    """
    Convert OpenAI tool format to google-genai FunctionDeclaration objects.

    OpenAI: {"type": "function", "function": {"name": "...", "parameters": {...}}}
    google-genai: types.Tool(function_declarations=[types.FunctionDeclaration(...)])

    The new SDK accepts raw JSON Schema via `parameters_json_schema` so we
    don't need to convert types to uppercase enums.
    """
    function_declarations = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool["function"]
            params = func.get("parameters", {})

            function_declarations.append(
                gtypes.FunctionDeclaration(
                    name=func["name"],
                    description=func.get("description", ""),
                    parameters_json_schema=params,
                )
            )

    if not function_declarations:
        return None
    return [gtypes.Tool(function_declarations=function_declarations)]


# ── Core LLM conversation ────────────────────────────────────────────────────


async def process_message(
    call_id: str,
    user_message: str,
    tenant_ctx: Any | None = None,
    caller_number: str = "",
) -> str:
    """
    Process an inbound user message and return the agent's response.
    Handles tool calls internally (two-step pattern).

    Args:
        tenant_ctx: If provided, tool definitions are filtered by tenant
            capabilities and all service calls use the tenant's credentials.
        caller_number: Caller phone number for system prompt injection.
    """
    # Route based on LLM provider
    if settings.LLM_PROVIDER == "gemini":
        return await _process_message_gemini(call_id, user_message, tenant_ctx, caller_number)
    else:
        return await _process_message_ollama(call_id, user_message, tenant_ctx, caller_number)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect a rate-limit (429) error from any of the google-genai error paths."""
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        name in ("ResourceExhausted", "ClientError", "APIError")
        and ("429" in msg or "resource_exhausted" in msg or "rate" in msg or "quota" in msg)
    )


def _is_transient_server_error(exc: Exception) -> bool:
    """Detect a transient Gemini 500/503 worth retrying."""
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        name in ("ServerError", "APIError", "InternalServerError")
        and ("500" in msg or "503" in msg or "internal error" in msg or "service unavailable" in msg)
    )


async def _gemini_send_with_retry(chat, content):
    """Send a message via async chat with retry on rate-limit (429) and transient 500/503 errors."""
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            return await chat.send_message(content)
        except Exception as exc:
            is_retryable = _is_rate_limit_error(exc) or _is_transient_server_error(exc)
            if is_retryable and attempt < MAX_RETRIES - 1:
                # 500s: short fixed wait; rate limits: exponential backoff
                wait = 5 if _is_transient_server_error(exc) else min(15 * (2 ** attempt), 60)
                logger.warning(
                    "Gemini %s — retrying in %.0fs (attempt %d/%d): %s",
                    "server error (500)" if _is_transient_server_error(exc) else "rate limited (429)",
                    wait, attempt + 1, MAX_RETRIES, exc,
                )
                await asyncio.sleep(wait)
                continue
            raise


async def _process_message_gemini(
    call_id: str,
    user_message: str,
    tenant_ctx: Any | None = None,
    caller_number: str = "",
) -> str:
    """
    Gemini implementation with function calling support (google-genai SDK).
    Uses ThinkingLevel.MINIMAL to disable Gemma's chain-of-thought output.
    NOTE: For MVP/testing with fake data ONLY — not for production use.
    """
    session = get_session(call_id)
    if session is None:
        session = create_session(call_id, caller_number=caller_number, tenant_ctx=tenant_ctx)
    if tenant_ctx and not session.get("tenant_ctx"):
        session["tenant_ctx"] = tenant_ctx

    effective_ctx = session.get("tenant_ctx")

    # Refresh the time in the system prompt so "what time is it?" is accurate.
    _refresh_system_time(session)

    session["messages"].append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    logger.info("[Call %s] Processing (Gemini) user message (%d chars)", call_id, len(user_message))

    try:
        t0 = time.time()

        # Simple chat detection (same as Ollama)
        suppress_tools = _looks_like_simple_chat(user_message, session["messages"])
        tools_for_request = None if suppress_tools else get_tools(effective_ctx)
        if suppress_tools:
            logger.info("[Call %s] Simple chat detected — suppressing tools (Gemini)", call_id)

        # Extract system prompt and build Gemini history (including tool calls).
        # google-genai uses types.Content / types.Part objects.
        history: list[gtypes.Content] = []
        system_prompt = ""
        all_msgs = session["messages"]
        for i, msg in enumerate(all_msgs):
            if msg["role"] == "system":
                system_prompt = msg["content"]
            elif msg["role"] == "user":
                if msg["content"] != user_message:  # Skip the current message
                    history.append(gtypes.Content(
                        role="user",
                        parts=[gtypes.Part(text=msg["content"])],
                    ))
            elif msg["role"] == "assistant":
                if msg.get("tool_calls"):
                    parts = []
                    for tc in msg["tool_calls"]:
                        try:
                            args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        parts.append(gtypes.Part(
                            function_call=gtypes.FunctionCall(name=tc["name"], args=args),
                        ))
                    history.append(gtypes.Content(role="model", parts=parts))
                elif msg.get("content"):
                    history.append(gtypes.Content(
                        role="model",
                        parts=[gtypes.Part(text=msg["content"])],
                    ))
            elif msg["role"] == "tool":
                try:
                    result = json.loads(msg["content"]) if isinstance(msg["content"], str) else msg["content"]
                except (json.JSONDecodeError, TypeError):
                    result = {"result": msg.get("content", "")}
                if not isinstance(result, dict):
                    result = {"result": result}
                tool_name = "unknown"
                if i > 0 and all_msgs[i - 1].get("tool_calls"):
                    tool_name = all_msgs[i - 1]["tool_calls"][0]["name"]
                history.append(gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(
                        function_response=gtypes.FunctionResponse(name=tool_name, response=result),
                    )],
                ))

        # Trim history before sending so long calls don't keep growing the
        # per-request token count. Full transcript is still saved in
        # session["messages"] — only the *forwarded* history is trimmed.
        history = _trim_gemini_history(
            history,
            max_messages=settings.GEMINI_HISTORY_MAX_MESSAGES,
        )

        # Build config with ThinkingLevel.MINIMAL — disables CoT output for Gemma 4.
        gemini_tools = _openai_tools_to_gemini(tools_for_request) if tools_for_request else None

        # Optionally use an explicit context cache for the system prompt + tools.
        # This is a per-tenant cache that's lazily created and reused for an
        # hour, cutting per-call cost ~75% on the cached prefix. If caching
        # isn't supported (or fails) we silently fall back to inline send.
        cached_name = await _get_or_create_gemini_cache(
            system_prompt=system_prompt,
            tools=gemini_tools,
            tenant_ctx=effective_ctx,
        )
        if cached_name:
            config = _build_gemini_config(cached_content=cached_name)
        else:
            config = _build_gemini_config(
                system_instruction=system_prompt,
                tools=gemini_tools,
            )

        # Create async chat
        client = _get_gemini_client()
        chat = client.aio.chats.create(
            model=settings.GEMINI_MODEL,
            config=config,
            history=history,
        )

        response = await _gemini_send_with_retry(chat, user_message)

        llm_time = (time.time() - t0) * 1000
        logger.info("[Call %s] Gemini responded in %.0fms", call_id, llm_time)

        # Handle function calls
        MAX_TOOL_ROUNDS = 5
        reply = ""

        for tool_round in range(MAX_TOOL_ROUNDS):
            if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
                logger.warning("[Call %s] Gemini returned empty response (possibly blocked by safety filters)", call_id)
                reply = "I'm sorry, I wasn't able to process that. Could you rephrase your question?"
                break

            # Find a function_call part (skipping thought parts)
            fc_part = None
            for part in response.candidates[0].content.parts:
                if getattr(part, "thought", False):
                    continue
                if part.function_call and part.function_call.name:
                    fc_part = part
                    break

            if fc_part:
                fn_name = fc_part.function_call.name
                fn_args = dict(fc_part.function_call.args) if fc_part.function_call.args else {}

                logger.info("[Call %s] Gemini function call: %s(%s)", call_id, fn_name, fn_args)

                tool_result = await _execute_tool(call_id, fn_name, fn_args, tenant_ctx=effective_ctx)
                logger.info("[Call %s] Tool result: %s", call_id, json.dumps(tool_result, default=str)[:500])

                # Ensure tool_result is a dict (FunctionResponse.response expects a dict)
                if not isinstance(tool_result, dict):
                    tool_result = {"result": tool_result}

                # Send the function response back
                response = await _gemini_send_with_retry(
                    chat,
                    gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=fn_name, response=tool_result,
                        ),
                    ),
                )

                session["messages"].append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"name": fn_name, "arguments": json.dumps(fn_args)}],
                })
                session["messages"].append({
                    "role": "tool",
                    "content": json.dumps(tool_result),
                })
            else:
                reply = _extract_gemini_text(response)
                break
        else:
            reply = _extract_gemini_text(response) or "I apologize, I'm having difficulty. Could you repeat that?"
            logger.warning("[Call %s] Exceeded %d tool rounds (Gemini)", call_id, MAX_TOOL_ROUNDS)

        if not reply:
            reply = "I'm sorry, could you say that again?"

        # Save reply
        session["messages"].append({
            "role": "assistant",
            "content": reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        total_time = (time.time() - t0) * 1000
        logger.info("[Call %s] Gemini final reply (%.0fms): %s", call_id, total_time, reply[:200])
        return reply

    except Exception as exc:
        logger.error("Gemini processing error for call %s: %s", call_id, exc, exc_info=True)
        return (
            "I apologize, I'm having a brief technical difficulty. "
            "Could you repeat that? Or I can connect you with a team member."
        )


async def _process_message_ollama(
    call_id: str,
    user_message: str,
    tenant_ctx: Any | None = None,
    caller_number: str = "",
) -> str:
    """Ollama/OpenAI implementation (original code)."""
    session = get_session(call_id)
    if session is None:
        session = create_session(call_id, caller_number=caller_number, tenant_ctx=tenant_ctx)
    # Ensure tenant_ctx is stored (e.g. if session was pre-created without it)
    if tenant_ctx and not session.get("tenant_ctx"):
        session["tenant_ctx"] = tenant_ctx

    # Use session's tenant_ctx for consistency within the conversation
    effective_ctx = session.get("tenant_ctx")

    # Refresh the time in the system prompt so "what time is it?" is accurate.
    _refresh_system_time(session)

    # Append user turn
    session["messages"].append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    msg_count = len(session["messages"])
    logger.info("[Call %s] Processing user message (%d chars, %d messages in history)",
                call_id, len(user_message), msg_count)
    logger.debug("[Call %s] User said: %s", call_id, user_message[:200])

    try:
        client = _get_client()

        # ── Step 1: LLM call (may request a tool) ────────────────────────
        logger.info("[Call %s] Sending to Ollama (model: %s, %d messages)...",
                    call_id, settings.OLLAMA_MODEL, msg_count)
        t0 = time.time()

        # Suppress tools on greetings / small talk to prevent hallucinated
        # tool calls (e.g. escalate_to_human on "hi").
        # Only applies to the first message — mid-conversation always gets tools.
        suppress_tools = _looks_like_simple_chat(user_message, session["messages"])
        tools_for_request = None if suppress_tools else get_tools(effective_ctx)
        if suppress_tools:
            logger.info("[Call %s] Simple chat detected — suppressing tools", call_id)

        kwargs: dict[str, Any] = {
            "model": settings.OLLAMA_MODEL,
            "messages": _strip_timestamps(session["messages"]),
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if tools_for_request:
            kwargs["tools"] = tools_for_request

        response = client.chat.completions.create(**kwargs)

        llm_time = (time.time() - t0) * 1000
        choice = response.choices[0]
        assistant_msg = choice.message
        finish_reason = choice.finish_reason

        logger.info("[Call %s] Ollama responded in %.0fms (finish_reason=%s, tool_calls=%s)",
                    call_id, llm_time, finish_reason,
                    bool(assistant_msg.tool_calls))

        # ── Step 2: Tool call loop (up to MAX_TOOL_ROUNDS) ────────────────
        MAX_TOOL_ROUNDS = 5
        current_msg = assistant_msg
        reply = ""

        for tool_round in range(MAX_TOOL_ROUNDS):
            if not current_msg.tool_calls:
                reply = current_msg.content or ""
                break

            # Execute ALL tool calls in this round
            for tool_call in current_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)

                logger.info("[Call %s] Tool call (round %d): %s(%s)",
                            call_id, tool_round + 1, fn_name, fn_args)

                tool_result = await _execute_tool(call_id, fn_name, fn_args, tenant_ctx=effective_ctx)

                logger.info("=" * 60)
                logger.info("[Call %s] TOOL RESULT for %s:", call_id, fn_name)
                logger.info("[Call %s]   %s", call_id, json.dumps(tool_result, default=str)[:1000])
                logger.info("=" * 60)

                session["messages"].append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {"name": fn_name, "arguments": tool_call.function.arguments},
                        }
                    ],
                })
                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result),
                })

            # Call LLM again with tool results — may trigger another tool or produce text
            logger.info("[Call %s] Generating follow-up (round %d)...", call_id, tool_round + 1)
            t1 = time.time()

            followup_kwargs: dict[str, Any] = {
                "model": settings.OLLAMA_MODEL,
                "messages": _strip_timestamps(session["messages"]),
                "temperature": 0.7,
                "max_tokens": 1024,
            }
            # Include tools so the LLM can chain (e.g. get_slots → book)
            if tools_for_request:
                followup_kwargs["tools"] = tools_for_request

            followup = client.chat.completions.create(**followup_kwargs)
            followup_time = (time.time() - t1) * 1000
            current_msg = followup.choices[0].message
            logger.info("[Call %s] Follow-up round %d in %.0fms (tool_calls=%s, content=%d chars)",
                        call_id, tool_round + 1, followup_time,
                        bool(current_msg.tool_calls), len(current_msg.content or ""))

            if not current_msg.tool_calls:
                reply = current_msg.content or ""
                break
        else:
            # Exceeded max rounds — use whatever content we have
            reply = current_msg.content or "I apologize, I'm having difficulty. Could you repeat that?"
            logger.warning("[Call %s] Exceeded %d tool rounds", call_id, MAX_TOOL_ROUNDS)

        # ── Strip Qwen3 thinking blocks if /no_think is partially honored ───
        if "<think>" in reply:
            reply = _re.sub(r"<think>.*?</think>\s*", "", reply, flags=_re.DOTALL).strip()
            logger.info("[Call %s] Stripped <think> block, post-strip=%d chars", call_id, len(reply))

        # Save assistant reply
        session["messages"].append({
            "role": "assistant",
            "content": reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        total_time = (time.time() - t0) * 1000
        logger.info("=" * 60)
        logger.info("[Call %s] FINAL LLM REPLY (%0.fms total):", call_id, total_time)
        # Log full reply in chunks to avoid truncation
        for i in range(0, max(len(reply), 1), 500):
            logger.info("[Call %s]   >>> %s", call_id, reply[i:i+500])
        logger.info("=" * 60)
        return reply

    except Exception as exc:
        logger.error("LLM processing error for call %s: %s", call_id, exc, exc_info=True)

        # ── Retry up to 2 more times before giving up ──────────────────
        import asyncio
        for retry_num in range(1, 3):
            logger.warning("[Call %s] Retry %d/2 after error: %s", call_id, retry_num, str(exc)[:200])
            await asyncio.sleep(min(2 * retry_num, 5))  # 2s, 4s backoff
            try:
                client = _get_client()
                retry_kwargs = {
                    "model": settings.OLLAMA_MODEL,
                    "messages": _strip_timestamps(session["messages"]),
                    "temperature": 0.7,
                    "max_tokens": 1024,
                }
                retry_response = client.chat.completions.create(**retry_kwargs)
                reply = retry_response.choices[0].message.content or ""
                if reply:
                    logger.info("[Call %s] Retry %d succeeded", call_id, retry_num)
                    session["messages"].append({"role": "assistant", "content": reply})
                    return reply
            except Exception as retry_exc:
                logger.error("[Call %s] Retry %d failed: %s", call_id, retry_num, retry_exc)
                continue

        # All retries exhausted — apologize (can't ask a question the broken LLM can't handle)
        logger.error("[Call %s] All retries exhausted — returning apology", call_id)
        fallback = (
            "I'm really sorry, I'm experiencing some technical difficulties right now "
            "and I'm unable to process your request. Please try again in a few minutes, "
            "or call the office directly for immediate help."
        )
        session["messages"].append({"role": "assistant", "content": fallback})
        return fallback


# ── Streaming variant ─────────────────────────────────────────────────────────


async def process_message_stream(
    call_id: str,
    user_message: str,
    tenant_ctx: Any | None = None,
    caller_number: str = "",
):
    """
    Async generator version of process_message(). Yields content tokens as
    they stream from Ollama — the caller sees the first token in ~200-500ms
    instead of waiting 3-10s for the full response.

    Tool calls are handled internally: when the LLM requests a tool, token
    streaming pauses while the tool executes, then the follow-up response
    streams token-by-token.

    Yields: str (content token fragments)

    NOTE: Gemini streaming not yet implemented — falls back to non-streaming
    for Gemini provider.
    """
    # Gemini: non-streaming API, then simulate token-by-token delivery
    if settings.LLM_PROVIDER == "gemini":
        reply = await process_message(call_id, user_message, tenant_ctx, caller_number)
        # Yield word-by-word with tiny async pauses so the event loop
        # actually flushes each SSE chunk to the browser
        words = reply.split(" ")
        for i, word in enumerate(words):
            yield word if i == 0 else " " + word
            await asyncio.sleep(0.03)  # 30ms per word ≈ natural reading speed
        return

    session = get_session(call_id)
    if session is None:
        session = create_session(call_id, caller_number=caller_number, tenant_ctx=tenant_ctx)
    if tenant_ctx and not session.get("tenant_ctx"):
        session["tenant_ctx"] = tenant_ctx

    effective_ctx = session.get("tenant_ctx")

    session["messages"].append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    msg_count = len(session["messages"])
    logger.info("[Call %s] Streaming user message (%d chars, %d msgs)",
                call_id, len(user_message), msg_count)

    try:
        client = _get_async_client()

        suppress_tools = _looks_like_simple_chat(user_message, session["messages"])
        tools_for_request = None if suppress_tools else get_tools(effective_ctx)
        if suppress_tools:
            logger.info("[Call %s] Simple chat — suppressing tools for stream", call_id)

        full_reply = ""
        t0 = time.time()

        for tool_round in range(5):  # MAX_TOOL_ROUNDS
            kwargs: dict[str, Any] = {
                "model": settings.OLLAMA_MODEL,
                "messages": _strip_timestamps(session["messages"]),
                "temperature": 0.7,
                "max_tokens": 1024,
                "stream": True,
            }
            if tools_for_request:
                kwargs["tools"] = tools_for_request

            t_round = time.time()
            stream = await client.chat.completions.create(**kwargs)

            round_content = ""
            tool_calls_acc: dict[int, dict] = {}

            # <think> block suppression — buffer initial tokens to detect
            # Qwen3 reasoning blocks, then flush once we know the real
            # content is starting.
            buf = ""
            flushing = False

            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta

                # Accumulate streamed tool-call fragments
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if getattr(tc.function, "name", None):
                            tool_calls_acc[idx]["name"] = tc.function.name
                        if getattr(tc.function, "arguments", None):
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

                token = delta.content or ""
                if not token:
                    continue

                round_content += token

                # ── Smart buffer: suppresses <think> and <tool_call> blocks ──
                # Even when flushing, re-enter buffering if a `<` appears
                # (could be the start of a tool/think tag).
                if flushing:
                    if "<" in token:
                        # Might be start of a tag — buffer it instead of yielding
                        buf = token
                        flushing = False
                    else:
                        yield token
                        full_reply += token
                        continue
                else:
                    buf += token

                if "<think>" in buf:
                    # Still in think block — wait for closing tag
                    if "</think>" in buf:
                        after = buf.split("</think>", 1)[1].lstrip()
                        if after:
                            yield after
                            full_reply += after
                        buf = ""
                        flushing = True
                elif _has_tool_tag_start(buf):
                    # Tool call text detected — do NOT yield to user.
                    # Wait for closing tag, then strip it.
                    if _has_tool_tag_end(buf):
                        before, after = _strip_tool_block(buf)
                        if before.strip():
                            yield before
                            full_reply += before
                        buf = after.lstrip() if after else ""
                        if not buf:
                            flushing = True
                    # else: keep buffering until closing tag arrives
                elif len(buf) > 50:
                    # No tags found — safe to flush. Use 50 chars to ensure
                    # full tag names (e.g. "<tool_call>") can be detected.
                    yield buf
                    full_reply += buf
                    buf = ""
                    flushing = True

            # Flush remaining buffer
            if buf:
                clean = buf
                # Strip think blocks
                if "<think>" in clean:
                    clean = _re.sub(r"<think>.*?</think>\s*", "", clean, flags=_re.DOTALL).strip()
                # Strip tool call blocks
                if _has_tool_tag_start(clean) and _has_tool_tag_end(clean):
                    before, _ = _strip_tool_block(clean)
                    clean = before.strip()
                elif _has_tool_tag_start(clean):
                    # Incomplete tool tag — strip from tag start onwards
                    for start_tag, _ in _TOOL_TAG_PATTERNS:
                        if start_tag in clean:
                            clean = clean.split(start_tag, 1)[0].strip()
                            break
                if clean:
                    yield clean
                    full_reply += clean

            # ── Detect text-based tool calls (Qwen3 quirk) ────────────
            # Sometimes models output <tool_call>...</tool_call> as text
            # instead of using the proper function calling API.
            if not tool_calls_acc and _has_tool_tag_start(round_content) and _has_tool_tag_end(round_content):
                parsed = _parse_tool_call_text(round_content)
                if parsed:
                    logger.warning("[Call %s] ⚠️ Text-based tool call detected: %s — executing",
                                   call_id, parsed["name"])
                    tool_calls_acc[0] = {
                        "id": f"text_tc_{tool_round}",
                        "name": parsed["name"],
                        "arguments": json.dumps(parsed["arguments"]),
                    }

            round_ms = (time.time() - t_round) * 1000
            logger.info("[Call %s] Stream round %d: %.0fms, content=%d chars, tools=%d",
                        call_id, tool_round + 1, round_ms,
                        len(round_content), len(tool_calls_acc))

            if not tool_calls_acc:
                break  # Pure text response — done

            # ── Execute tool calls, add results to session ──────────
            for idx in sorted(tool_calls_acc.keys()):
                tc_info = tool_calls_acc[idx]
                fn_name = tc_info["name"]
                try:
                    fn_args = json.loads(tc_info["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info("[Call %s] Stream tool (round %d): %s(%s)",
                            call_id, tool_round + 1, fn_name, fn_args)

                tool_result = await _execute_tool(
                    call_id, fn_name, fn_args, tenant_ctx=effective_ctx,
                )
                logger.info("[Call %s] Stream tool result: %s",
                            call_id, json.dumps(tool_result, default=str)[:500])

                session["messages"].append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc_info["id"],
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": tc_info["arguments"],
                        },
                    }],
                })
                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc_info["id"],
                    "content": json.dumps(tool_result, default=str),
                })

        # Save complete reply to session history
        # Handle empty response from LLM (timeout, context overflow, etc.)
        if not full_reply:
            fallback = (
                "I apologize, I didn't catch that. Could you please repeat your request? "
                "I'm here to help with scheduling appointments."
            )
            logger.warning("[Call %s] LLM returned empty response after %.0fms — sending fallback",
                          call_id, (time.time() - t0) * 1000)
            yield fallback
            full_reply = fallback

        if full_reply:
            session["messages"].append({
                "role": "assistant",
                "content": full_reply,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        total_ms = (time.time() - t0) * 1000
        logger.info("[Call %s] Stream complete: %d chars in %.0fms",
                    call_id, len(full_reply), total_ms)

    except Exception as exc:
        logger.error("LLM streaming error for call %s: %s", call_id, exc, exc_info=True)

        # ── Retry up to 2 more times before giving up ──────────────────
        retried = False
        for retry_num in range(1, 3):
            logger.warning("[Call %s] Stream retry %d/2 after error: %s", call_id, retry_num, str(exc)[:200])
            await asyncio.sleep(min(2 * retry_num, 5))
            try:
                retry_client = _get_async_client()
                retry_stream = await retry_client.chat.completions.create(
                    model=settings.OLLAMA_MODEL,
                    messages=_strip_timestamps(session["messages"]),
                    temperature=0.7,
                    max_tokens=1024,
                    stream=True,
                )
                retry_content = ""
                async for chunk in retry_stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice or not choice.delta:
                        continue
                    token = choice.delta.content or ""
                    if token:
                        retry_content += token
                        yield token
                if retry_content:
                    session["messages"].append({
                        "role": "assistant",
                        "content": retry_content,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    retried = True
                    logger.info("[Call %s] Stream retry %d succeeded", call_id, retry_num)
                    break
            except Exception as retry_exc:
                logger.error("[Call %s] Stream retry %d failed: %s", call_id, retry_num, retry_exc)
                continue

        if not retried:
            logger.error("[Call %s] All stream retries exhausted — returning apology", call_id)
            fallback = (
                "I'm really sorry, I'm experiencing some technical difficulties right now "
                "and I'm unable to process your request. Please try again in a few minutes, "
                "or call the office directly for immediate help."
            )
            session["messages"].append({"role": "assistant", "content": fallback})
            yield fallback


# ── Caller-phone enforcement (privacy / compliance) ──────────────────────────
# Tools that access or mutate caller data MUST operate on the caller's own
# phone number.  This is a hard backend gate — prompt-level instructions alone
# are insufficient because callers (or prompt-injection attacks) can ask the
# LLM to look up arbitrary numbers.

_PHONE_GATED_TOOLS = frozenset({
    "lookup_caller",
    "lookup_caller_appointments",
    "update_caller_info",
    "book_appointment",
    "add_to_waitlist",
    "send_callback_request",
})


def _normalize_phone_for_match(phone: str) -> str:
    """Strip a phone to its last 10 digits for comparison.

    Different sources store phones in different formats (+12125551234 vs
    2125551234 vs 212-555-1234).  Comparing the last 10 digits is a simple,
    reliable way to match US numbers regardless of formatting.
    """
    digits = _re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _enforce_caller_phone(
    session: dict | None,
    name: str,
    args: dict,
    call_id: str,
) -> dict | None:
    """If the tool is phone-gated, verify the phone arg matches the caller.

    Returns None if the check passes (or doesn't apply).
    Returns a rejection dict for the LLM if the check fails.
    """
    if name not in _PHONE_GATED_TOOLS:
        return None

    if not session:
        return None  # No session → can't enforce (shouldn't happen)

    caller_phone = session.get("caller_number", "")
    if not caller_phone:
        return None  # No caller-ID on file → can't enforce (web widget, etc.)

    tool_phone = args.get("phone", "")
    if not tool_phone:
        return None  # Tool doesn't have a phone arg yet → let it proceed (tool will ask)

    caller_norm = _normalize_phone_for_match(caller_phone)
    tool_norm = _normalize_phone_for_match(tool_phone)

    if not caller_norm or not tool_norm:
        return None  # Edge case — can't compare

    if caller_norm == tool_norm:
        return None  # ✅ Match — proceed normally

    # ❌ Mismatch — block the request
    logger.warning(
        "[Call %s] PRIVACY BLOCK: tool=%s tried phone=%s but caller=%s",
        call_id, name, tool_phone, caller_phone,
    )
    return {
        "ok": False,
        "privacy_blocked": True,
        "summary_for_assistant": (
            "For caller privacy and security, I can only access records for the "
            "phone number on this call. If someone else needs to manage their "
            "appointments, they should call us directly from their own number, "
            "or our staff can help them in person."
        ),
    }


# ── Tool execution ────────────────────────────────────────────────────────────


async def _execute_tool(
    call_id: str,
    name: str,
    args: dict,
    tenant_ctx: Any | None = None,
) -> dict[str, Any]:
    """Route a tool call to the appropriate service and return a result dict.

    Args:
        tenant_ctx: When set, all downstream service calls (calendar, SMS) use
            the tenant's own credentials. When None, falls back to global .env
            settings (legacy single-tenant mode).
    """
    session = get_session(call_id)
    is_test = session.get("is_test", False) if session else False
    logger.info("[Call %s] Executing tool: %s with args: %s (tenant=%s, is_test=%s)",
                call_id, name, json.dumps(args)[:300],
                tenant_ctx.slug if tenant_ctx else "global", is_test)
    t0 = time.time()

    # ── Privacy gate: block cross-caller data access ──────────────────
    privacy_rejection = _enforce_caller_phone(session, name, args, call_id)
    if privacy_rejection:
        return privacy_rejection

    try:
        from backend.tools.registry import get_registry
        session_ctx = {
            "tenant_ctx": tenant_ctx,
            "call_id": call_id,
            "session": session,
            "is_test": is_test,
            "t0": t0,
        }
        return await get_registry().dispatch(name, args, session_ctx)
    except Exception as exc:
        elapsed = (time.time() - t0) * 1000
        logger.error("[Call %s] Tool '%s' failed after %.0fms: %s", call_id, name, elapsed, exc, exc_info=True)
        return {"error": str(exc)}



# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_timestamps(messages: list[dict]) -> list[dict]:
    """Remove the 'timestamp' key from messages before sending to the LLM."""
    clean = []
    for msg in messages:
        m = {k: v for k, v in msg.items() if k != "timestamp"}
        clean.append(m)
    return clean
