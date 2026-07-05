"""
OpenAI-compatible LLM proxy endpoint for Vapi.

Multi-tenant: extracts the assistant_id from Vapi's request, resolves it to a
TenantContext, and threads that context through all service calls so each
tenant uses their own Twilio creds, timezone, KB, and prompts.

Architecture:
  1. Vapi sends conversation → we resolve tenant + inject tenant-specific system prompt
  2. We call the configured LLM (Ollama or Gemini) with tool definitions
  3. If the LLM requests a tool call → execute it → feed result back → call again
  4. Wrap the final text response as SSE stream for Vapi
     (Vapi REQUIRES streaming SSE from custom-llm providers)
"""
from __future__ import annotations


import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from openai import AsyncOpenAI, OpenAI

# Gemini SDK (google-genai) — used when LLM_PROVIDER=gemini
from google.genai import types as gtypes

from backend.config import settings
from backend.defaults import DEFAULT_TIMEZONE, slugify_appointment_type
from backend.prompts.agent_prompt import build_system_prompt
from backend.services.llm_service import (
    get_tools,
    _get_gemini_client,
    _build_gemini_config,
    _openai_tools_to_gemini,
    _extract_gemini_text,
    _gemini_send_with_retry,
    _is_rate_limit_error,
)
from backend.services import calendar_service, sms_service, caller_service
from backend.services.tenant_service import (
    resolve_default_tenant,
    TenantContext,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["LLM Proxy"])

_client: OpenAI | None = None
_async_client: AsyncOpenAI | None = None

MAX_TOOL_ROUNDS = 5  # Safety limit: max tool call round-trips per request


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.ollama_openai_base,
            api_key="ollama",
        )
    return _client


def _get_async_client() -> AsyncOpenAI:
    """Async client for true token-by-token streaming to Vapi."""
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(
            base_url=settings.ollama_openai_base,
            api_key="ollama",
        )
    return _async_client


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint.
    Vapi sends the full conversation here; we resolve the tenant, inject
    the tenant-specific system prompt, forward to Ollama with tools,
    execute any tool calls, and return the final response as SSE stream.
    """
    try:
        body = await request.json()
    except Exception:
        logger.error("Failed to parse LLM proxy request body")
        return _error_response("Invalid request")

    messages = body.get("messages", [])
    is_gemini = settings.LLM_PROVIDER == "gemini"
    model = body.get("model", settings.GEMINI_MODEL if is_gemini else settings.OLLAMA_MODEL)

    # ── Resolve tenant ────────────────────────────────────────────────
    # Fall back to the default (first active) tenant.
    tenant_ctx: TenantContext | None = await resolve_default_tenant()
    if tenant_ctx:
        logger.info("[LLM Proxy] Using default tenant: %s (%s)", tenant_ctx.slug, tenant_ctx.business_name)

    # ── Extract caller phone ───────────────────────────────────────────
    # Vapi format: body.call.customer.number
    # Bolna format: injected as {{contact_number}} in the system message,
    #   so messages[0]["content"] contains e.g. "caller_phone: +919876543210"
    caller_phone = (
        body.get("call", {}).get("customer", {}).get("number", "")
        or body.get("metadata", {}).get("caller_number", "")
        or ""
    )
    # For Bolna: phone + name are injected into the first system message
    # via {{contact_number}} and {{caller_name}} template substitution.
    # Bolna system prompt template should be:
    #   caller_phone: {{contact_number}}
    #   caller_name: {{caller_name}}
    _bolna_caller_name = ""
    if not caller_phone and messages:
        sys_content = ""
        for m in messages[:2]:
            if m.get("role") == "system":
                sys_content = m.get("content") or ""
                break
        if sys_content:
            import re as _re_phone
            m = _re_phone.search(r"caller_phone[:\s]+(\+?\d[\d\s\-]{7,})", sys_content)
            if m:
                caller_phone = m.group(1).strip()
                logger.info("[LLM Proxy] Extracted caller phone from Bolna system message: %s", caller_phone)
            # Also extract caller_name injected by Bolna's caller-lookup substitution
            m_name = _re_phone.search(r"caller_name[:\s]+([^\n]+)", sys_content)
            if m_name:
                raw = m_name.group(1).strip()
                # Bolna uses "there" as placeholder for unknown callers (from our caller-lookup)
                if raw and raw.lower() != "there":
                    _bolna_caller_name = raw
                    logger.info("[LLM Proxy] Extracted caller name from Bolna system message: %s", _bolna_caller_name)

    logger.info("[LLM Proxy] Received request: %d messages, model=%s, caller=%s, tenant=%s",
                len(messages), model, caller_phone or "unknown",
                tenant_ctx.slug if tenant_ctx else "none")

    # ── Caller-ID validation ──────────────────────────────────────────────
    # Skip caller-ID if it matches the institute's own Twilio number
    # (happens when testing via Vapi web dialer — no real caller).
    _is_own_number = False
    if caller_phone and tenant_ctx and tenant_ctx.twilio_phone_number:
        from backend.services.caller_service import _phone_digits_tail
        _is_own_number = (
            _phone_digits_tail(caller_phone) == _phone_digits_tail(tenant_ctx.twilio_phone_number)
        )
        if _is_own_number:
            logger.info(
                "[LLM Proxy] Caller %s matches institute Twilio number — skipping caller recognition",
                caller_phone,
            )
            # Clear caller_phone so the system prompt doesn't tell the agent
            # to use the institute's own number as the caller's phone.
            caller_phone = ""

    # Lightweight name lookup — only fetches the caller's name for the greeting.
    # Bolna already provides the name via caller-lookup substitution — skip DB call.
    # Full profile (appointments, history) is fetched lazily via lookup_caller tool.
    caller_name = _bolna_caller_name  # pre-filled if Bolna path, "" otherwise
    if caller_phone and not _is_own_number and not caller_name:
        # Vapi path (or Bolna without caller_name in template) — do lightweight DB lookup
        try:
            tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
            caller_rec = await caller_service.get_caller_by_phone(caller_phone, tenant_id=tenant_id)
            if caller_rec:
                caller_name = caller_rec.name
                logger.info("[LLM Proxy] Returning caller: %s (%s)", caller_name, caller_phone)
            else:
                logger.info("[LLM Proxy] New caller: %s", caller_phone)
        except Exception as exc:
            logger.warning("[LLM Proxy] Name lookup failed: %s", exc)

    # Pre-fetch providers for authoritative demo subjects injection
    _proxy_providers: list[dict] = []
    try:
        if tenant_ctx:
            from backend.services import provider_service as _ps
            _proxy_providers = await _ps.list_providers(tenant_ctx.tenant_id)
    except Exception as _pe:
        logger.warning("[LLM Proxy] Provider fetch failed: %s", _pe)

    # ALWAYS inject our full system prompt — replace Vapi's generic one
    system_prompt = build_system_prompt(
        tenant_ctx=tenant_ctx,
        caller_phone=caller_phone,
        caller_name=caller_name,
        providers=_proxy_providers,
    )
    if messages and messages[0].get("role") == "system":
        vapi_prompt = messages[0].get("content", "")
        logger.info("[LLM Proxy] Replacing Vapi's system prompt (%d chars) with ours (%d chars)",
                    len(vapi_prompt), len(system_prompt))
        messages[0]["content"] = system_prompt
    else:
        messages.insert(0, {"role": "system", "content": system_prompt})
        logger.info("[LLM Proxy] Injected system prompt (%d chars)", len(system_prompt))

    # Log conversation context
    provider_label = "Gemini" if is_gemini else "Ollama"
    logger.info("[LLM Proxy] ── Messages being sent to %s ──", provider_label)
    for idx, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if role == "system":
            logger.info("[LLM Proxy]   [%d] %s: (%d chars — system prompt)", idx, role, len(content or ""))
        else:
            preview = (content or "")[:300]
            logger.info("[LLM Proxy]   [%d] %s: %s", idx, role, preview)
    logger.info("[LLM Proxy] ── End messages ──")

    t0 = time.time()

    try:
        # ── Route-level escalation short-circuit ─────────────────────────
        # Only perform escalation if the tenant has configured emergency_guidance
        has_escalation = bool(tenant_ctx and tenant_ctx.emergency_guidance)
        if _is_explicit_human_request(messages) and has_escalation:
            logger.info("[LLM Proxy] Explicit human request detected at route level")
            try:
                await _execute_tool(
                    "escalate_to_human",
                    {"reason": "Caller explicitly asked to speak to a human"},
                    tenant_ctx=tenant_ctx,
                )
            except Exception as exc:
                logger.warning("[LLM Proxy] escalate_to_human side-effect failed: %s", exc)

            transfer_number = ""
            if tenant_ctx:
                transfer_number = (tenant_ctx.escalation_transfer_number or "").strip()
            else:
                transfer_number = (settings.ESCALATION_TRANSFER_NUMBER or "").strip()

            biz_name = tenant_ctx.business_name if tenant_ctx else settings.OFFICE_NAME

            if transfer_number:
                logger.info("[LLM Proxy] Live-transferring call to %s", transfer_number)
                return _stream_transfer_call(
                    "Of course — let me transfer you to one of our team members now. Please hold.",
                    transfer_number,
                    model,
                )
            else:
                logger.info("[LLM Proxy] No transfer number set — emitting callback + endCall")
                return _stream_text_then_end_call(
                    (
                        f"Of course. I've alerted our team and someone will call you back at "
                        f"this number very shortly. Thank you for calling {biz_name}. "
                        f"Have a great day."
                    ),
                    model,
                )
        elif _is_explicit_human_request(messages) and not has_escalation:
            logger.info("[LLM Proxy] Explicit human request detected but escalation not configured — skipping short-circuit")

        # ── True streaming: tokens flow to Vapi as the LLM generates them ──
        if is_gemini:
            generator = _stream_with_tools_sse_gemini(
                model, messages, t0, tenant_ctx=tenant_ctx,
            )
        else:
            async_client = _get_async_client()
            generator = _stream_with_tools_sse(
                async_client, model, messages, t0, tenant_ctx=tenant_ctx,
            )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except Exception as exc:
        # Catches errors BEFORE streaming starts (e.g. tenant resolution, client init)
        elapsed = (time.time() - t0) * 1000
        logger.error("[LLM Proxy] Request failed after %.0fms: %s", elapsed, exc, exc_info=True)
        return _stream_text_response(
            "I apologize, I'm having a brief technical difficulty. Could you repeat that?",
            model,
        )


def _stream_text_response(text: str, model: str) -> StreamingResponse:
    """
    Wrap a final text response as SSE stream that Vapi can consume.
    Sends the text in word-sized chunks to simulate natural streaming.
    """
    def generate():
        response_id = f"chatcmpl-{int(time.time())}"

        # First chunk: role
        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }) + "\n\n"

        # Content chunks: send a few words at a time for natural pacing
        words = text.split(" ")
        chunk_size = 3  # words per chunk
        for i in range(0, len(words), chunk_size):
            chunk_text = " ".join(words[i:i+chunk_size])
            if i > 0:
                chunk_text = " " + chunk_text
            yield "data: " + json.dumps({
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}],
            }) + "\n\n"

        # Final chunk: finish
        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }) + "\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def _stream_transfer_call(spoken_text: str, destination: str, model: str) -> StreamingResponse:
    """
    Emit a Vapi `transferCall` tool call as SSE so Vapi hands the call off
    to a real phone number.
    """
    def generate():
        response_id = f"chatcmpl-{int(time.time())}"
        created = int(time.time())

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }) + "\n\n"

        words = spoken_text.split(" ")
        for i in range(0, len(words), 3):
            chunk_text = " ".join(words[i:i+3])
            if i > 0:
                chunk_text = " " + chunk_text
            yield "data: " + json.dumps({
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}],
            }) + "\n\n"

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": f"call_transfer_{created}",
                        "type": "function",
                        "function": {
                            "name": "transferCall",
                            "arguments": json.dumps({"destination": destination}),
                        },
                    }]
                },
                "finish_reason": None,
            }],
        }) + "\n\n"

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        }) + "\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _stream_text_then_end_call(spoken_text: str, model: str) -> StreamingResponse:
    """
    Speak `spoken_text`, then emit a Vapi `endCall` tool call so the call
    hangs up cleanly.
    """
    def generate():
        response_id = f"chatcmpl-{int(time.time())}"
        created = int(time.time())

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }) + "\n\n"

        words = spoken_text.split(" ")
        for i in range(0, len(words), 3):
            chunk_text = " ".join(words[i:i+3])
            if i > 0:
                chunk_text = " " + chunk_text
            yield "data: " + json.dumps({
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}],
            }) + "\n\n"

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": f"call_endcall_{created}",
                        "type": "function",
                        "function": {
                            "name": "endCall",
                            "arguments": "{}",
                        },
                    }]
                },
                "finish_reason": None,
            }],
        }) + "\n\n"

        yield "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        }) + "\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _stream_with_tools_sse(
    client: AsyncOpenAI,
    model: str,
    messages: list,
    t0: float,
    tenant_ctx: TenantContext | None = None,
):
    """
    Async generator yielding SSE chunks with true token-by-token streaming.
    Handles tool calls internally — pauses SSE during tool execution, then
    streams the follow-up response. Vapi's TTS starts speaking as soon as
    the first token arrives (~200-500ms) instead of waiting for the full reply.
    """
    import re as _re

    response_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())

    def _sse(delta, finish_reason=None):
        return "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }) + "\n\n"

    # Role chunk
    yield _sse({"role": "assistant"})

    suppress_tools_round1 = _looks_like_simple_chat(messages)
    if suppress_tools_round1:
        logger.info("[LLM Proxy] Simple chat — suppressing tools for round 1 (stream)")

    full_content = ""

    try:
        for round_num in range(1, MAX_TOOL_ROUNDS + 1):
            logger.info("[LLM Proxy] ── Streaming round %d ──", round_num)

            use_tools = not (suppress_tools_round1 and round_num == 1)
            t_round = time.time()

            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1024,
                "stream": True,
            }
            if use_tools:
                kwargs["tools"] = get_tools(tenant_ctx)

            stream = await client.chat.completions.create(**kwargs)

            round_content = ""
            tool_calls_acc: dict[int, dict] = {}

            # <think> block suppression buffer
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

                # Already past the think block — yield immediately
                if flushing:
                    yield _sse({"content": token})
                    full_content += token
                    continue

                buf += token

                if "<think>" in buf:
                    if "</think>" in buf:
                        after = buf.split("</think>", 1)[1].lstrip()
                        if after:
                            yield _sse({"content": after})
                            full_content += after
                        buf = ""
                        flushing = True
                elif _has_tool_tag_start(buf):
                    # Don't yield tool call text — wait for closing tag
                    # Handles: <tool_call>, <function_call>, <|tool_call|>, etc.
                    if _has_tool_tag_end(buf):
                        # Strip the tool block, keep any text before/after
                        before, after = _strip_tool_block(buf)
                        if before.strip():
                            yield _sse({"content": before})
                            full_content += before
                        buf = after.lstrip()
                        # Don't set flushing yet — there might be more content
                    # else: keep buffering until we see closing tag
                elif len(buf) > 20:
                    yield _sse({"content": buf})
                    full_content += buf
                    buf = ""
                    flushing = True

            # Flush remaining buffer
            if buf and not flushing:
                clean = buf
                if "<think>" in clean:
                    clean = _re.sub(r"<think>.*?</think>\s*", "", clean, flags=_re.DOTALL).strip()
                if clean:
                    yield _sse({"content": clean})
                    full_content += clean

            # ── Detect text-based tool calls (Qwen3 quirk) ────────────────
            # Sometimes models output <tool_call>...</tool_call> (or similar) as text
            # instead of using the proper function calling API. Parse and execute.
            if _has_tool_tag_start(round_content) and _has_tool_tag_end(round_content):
                parsed = _parse_tool_call_text(round_content)
                if parsed:
                    logger.warning("[LLM Proxy]   ⚠️ Text-based tool call detected: %s — executing", parsed["name"])
                    # Inject into tool_calls_acc so it gets executed
                    tool_calls_acc[0] = {
                        "id": f"text_tc_{round_num}",
                        "name": parsed["name"],
                        "arguments": json.dumps(parsed["arguments"]),
                    }

            round_ms = (time.time() - t_round) * 1000
            logger.info("[LLM Proxy]   Round %d: %.0fms, content=%d chars, tool_calls=%d",
                        round_num, round_ms, len(round_content), len(tool_calls_acc))

            if not tool_calls_acc:
                if round_content:
                    logger.info("[LLM Proxy]   Round %d → streamed: %s", round_num, round_content[:200])
                break

            # ── Execute tool calls ────────────────────────────────────
            for idx in sorted(tool_calls_acc.keys()):
                tc_info = tool_calls_acc[idx]
                fn_name = tc_info["name"]
                try:
                    fn_args = json.loads(tc_info["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                logger.info("[LLM Proxy]   🔧 TOOL: %s(%s)", fn_name, json.dumps(fn_args)[:300])

                # Date correction for get_available_slots
                if fn_name == "get_available_slots":
                    user_text = _last_user_message(messages)
                    tenant_tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
                    resolved = _resolve_dow_to_date(user_text, tz_name=tenant_tz)
                    model_date = fn_args.get("date", "")
                    if resolved and resolved != model_date:
                        logger.warning("[LLM Proxy]   📅 Date correction: %r → %r",
                                       model_date, resolved)
                        fn_args["date"] = resolved

                tool_result = await _execute_tool(fn_name, fn_args, tenant_ctx=tenant_ctx, caller_phone=caller_phone)
                logger.info("[LLM Proxy]   🔧 RESULT: %s",
                            json.dumps(tool_result, default=str)[:500])

                # ── Vapi live-transfer ───────────────────────────────────
                # If the LLM called escalate_to_human and the tenant has a
                # transfer number configured, emit a Vapi `transferCall`
                # tool-call so Vapi actually hands the call off to a real
                # human phone line. Otherwise the SMS alert alone fires and
                # the agent only says "I've notified the team".
                if (
                    fn_name == "escalate_to_human"
                    and isinstance(tool_result, dict)
                    and tool_result.get("action") == "transfer"
                ):
                    transfer_dest = (tool_result.get("destination") or "").strip()
                    if not transfer_dest and tenant_ctx:
                        transfer_dest = (
                            tenant_ctx.escalation_transfer_number
                            or tenant_ctx.escalation_phone
                            or ""
                        ).strip()
                    if transfer_dest:
                        logger.info(
                            "[LLM Proxy] 📞 escalate_to_human → emitting Vapi transferCall to %s",
                            transfer_dest,
                        )
                        # Speak a short hand-off line, then emit transferCall
                        # as a streamed tool_calls delta so Vapi acts on it.
                        handoff = "Let me transfer you to one of our team members now. Please hold."
                        words = handoff.split(" ")
                        for i in range(0, len(words), 3):
                            chunk_text = " ".join(words[i:i+3])
                            if i > 0:
                                chunk_text = " " + chunk_text
                            yield _sse({"content": chunk_text})
                            full_content += chunk_text
                        yield _sse({
                            "tool_calls": [{
                                "index": 0,
                                "id": f"call_transfer_{int(time.time())}",
                                "type": "function",
                                "function": {
                                    "name": "transferCall",
                                    "arguments": json.dumps({"destination": transfer_dest}),
                                },
                            }]
                        })
                        yield _sse({}, finish_reason="tool_calls")
                        yield "data: [DONE]\n\n"
                        return
                    else:
                        logger.info(
                            "[LLM Proxy] escalate_to_human ran but no transfer number set — SMS alert only"
                        )

                messages.append({
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
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_info["id"],
                    "content": json.dumps(tool_result, default=str),
                })

    except Exception as exc:
        elapsed = (time.time() - t0) * 1000
        logger.error("[LLM Proxy] Streaming failed after %.0fms: %s", elapsed, exc, exc_info=True)

        # ── Retry up to 2 more times before giving up ──────────────────
        retried = False
        for retry_num in range(1, 3):
            logger.warning("[LLM Proxy] Retry %d/2 after error: %s", retry_num, str(exc)[:200])
            await asyncio.sleep(min(2 * retry_num, 5))  # 2s, 4s backoff
            try:
                retry_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 1024,
                    "stream": True,
                    "tools": get_tools(tenant_ctx) if tenant_ctx else [],
                }
                retry_stream = await client.chat.completions.create(**retry_kwargs)
                async for chunk in retry_stream:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice or not choice.delta:
                        continue
                    token = choice.delta.content or ""
                    if token:
                        yield _sse({"content": token})
                retried = True
                logger.info("[LLM Proxy] Retry %d succeeded", retry_num)
                break
            except Exception as retry_exc:
                logger.error("[LLM Proxy] Retry %d failed: %s", retry_num, retry_exc)
                continue

        if not retried:
            # All retries exhausted — offer to transfer to reception
            logger.error("[LLM Proxy] All retries exhausted — offering transfer to reception")
            transfer_dest = ""
            if tenant_ctx:
                transfer_dest = (
                    getattr(tenant_ctx, "escalation_transfer_number", "") or
                    getattr(tenant_ctx, "business_phone", "") or
                    getattr(tenant_ctx, "escalation_phone", "") or
                    ""
                ).strip()

            if transfer_dest:
                yield _sse({"content": (
                    "I'm really sorry, I'm experiencing some technical difficulties right now. "
                    "Let me transfer you to our reception team so they can help you directly. "
                    "Please hold for just a moment."
                )})
                # Emit Vapi transferCall to redirect to front desk
                yield _sse({
                    "tool_calls": [{
                        "index": 0,
                        "id": f"call_error_transfer_{int(time.time())}",
                        "type": "function",
                        "function": {
                            "name": "transferCall",
                            "arguments": json.dumps({"destination": transfer_dest}),
                        },
                    }]
                })
                yield _sse({}, finish_reason="tool_calls")
                return
            else:
                yield _sse({"content": (
                    "I'm really sorry, I'm experiencing some technical difficulties right now. "
                    "Could you please call back in a few minutes? "
                    "I apologize for the inconvenience."
                )})

    elapsed = (time.time() - t0) * 1000
    logger.info("=" * 70)
    logger.info("[LLM Proxy] STREAMED RESPONSE TO VAPI")
    logger.info("[LLM Proxy]   Total time:   %.0fms", elapsed)
    logger.info("[LLM Proxy]   Content len:  %d chars", len(full_content))
    logger.info("[LLM Proxy]   Content:      %s", full_content[:500])
    logger.info("=" * 70)

    yield _sse({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


def _convert_messages_to_gemini(
    messages: list[dict],
) -> tuple[str, list[gtypes.Content], str]:
    """
    Convert OpenAI-format messages to Gemini Content objects.

    Returns:
        (system_prompt, history, last_user_text)
    """
    system_prompt = ""
    history: list[gtypes.Content] = []
    last_user_text = ""

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            system_prompt = content

        elif role == "user":
            last_user_text = content
            history.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part(text=content)],
            ))

        elif role == "assistant":
            if msg.get("tool_calls"):
                parts = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", tc)
                    fn_name = fn.get("name", "")
                    fn_args_raw = fn.get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except (json.JSONDecodeError, TypeError):
                        fn_args = {}
                    parts.append(gtypes.Part(
                        function_call=gtypes.FunctionCall(name=fn_name, args=fn_args),
                    ))
                if content:
                    parts.insert(0, gtypes.Part(text=content))
                history.append(gtypes.Content(role="model", parts=parts))
            elif content:
                history.append(gtypes.Content(
                    role="model",
                    parts=[gtypes.Part(text=content)],
                ))

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                result = {"result": content}
            if not isinstance(result, dict):
                result = {"result": result}

            # Find the tool name from the preceding assistant message
            tool_name = "unknown"
            if i > 0:
                prev = messages[i - 1]
                if prev.get("role") == "assistant" and prev.get("tool_calls"):
                    for tc in prev["tool_calls"]:
                        tc_id = tc.get("id", "")
                        fn = tc.get("function", tc)
                        if tc_id == tool_call_id or not tool_call_id:
                            tool_name = fn.get("name", "unknown")
                            break

            history.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part(
                    function_response=gtypes.FunctionResponse(
                        name=tool_name, response=result,
                    ),
                )],
            ))

    return system_prompt, history, last_user_text


async def _stream_with_tools_sse_gemini(
    model: str,
    messages: list,
    t0: float,
    tenant_ctx: TenantContext | None = None,
):
    """
    Gemini version of the SSE streaming generator.

    Calls Gemini via google-genai SDK, handles tool calls internally,
    and wraps the final text response as OpenAI-format SSE chunks that
    Vapi can consume.

    Unlike Ollama, Gemini doesn't stream through an OpenAI-compatible API —
    we get the full response, then emit it as word-chunked SSE to Vapi.
    """
    response_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())

    def _sse(delta, finish_reason=None):
        return "data: " + json.dumps({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }) + "\n\n"

    # Role chunk
    yield _sse({"role": "assistant"})

    suppress_tools_round1 = _looks_like_simple_chat(messages)
    if suppress_tools_round1:
        logger.info("[LLM Proxy] Simple chat — suppressing tools for round 1 (Gemini stream)")

    full_content = ""

    try:
        # Convert messages to Gemini format
        system_prompt, gemini_history, last_user_text = _convert_messages_to_gemini(messages)

        # Pop the last user message from history — it will be sent via chat.send_message()
        if gemini_history and gemini_history[-1].role == "user":
            last_entry = gemini_history[-1]
            is_fn_response = any(
                getattr(p, "function_response", None) for p in last_entry.parts
            )
            if not is_fn_response:
                gemini_history = gemini_history[:-1]

        # Build tool definitions (used for all rounds unless suppressed)
        openai_tools = get_tools(tenant_ctx)
        gemini_tools = _openai_tools_to_gemini(openai_tools)

        # For round 1 with simple chat, suppress tools
        initial_tools = None if suppress_tools_round1 else gemini_tools

        # Build config and create chat session ONCE
        config = _build_gemini_config(
            system_instruction=system_prompt,
            tools=initial_tools,
        )
        client = _get_gemini_client()
        chat = client.aio.chats.create(
            model=settings.GEMINI_MODEL,
            config=config,
            history=gemini_history,
        )

        # Send the first user message
        t_round = time.time()
        response = await _gemini_send_with_retry(chat, last_user_text)
        round_ms = (time.time() - t_round) * 1000
        logger.info("[LLM Proxy] ── Gemini round 1: %.0fms ──", round_ms)

        for round_num in range(1, MAX_TOOL_ROUNDS + 1):
            if not response.candidates or not response.candidates[0].content:
                logger.warning("[LLM Proxy] Gemini returned empty response (round %d)", round_num)
                yield _sse({"content": "I'm sorry, I wasn't able to process that. Could you rephrase?"})
                full_content += "I'm sorry, I wasn't able to process that. Could you rephrase?"
                break

            parts = response.candidates[0].content.parts or []

            # Check for function calls (skip thought parts)
            fc_part = None
            for part in parts:
                if getattr(part, "thought", False):
                    continue
                if part.function_call and part.function_call.name:
                    fc_part = part
                    break

            # Extract text content (skip thought parts)
            round_text = _extract_gemini_text(response)

            logger.info("[LLM Proxy]   Gemini round %d: text=%d chars, function_call=%s",
                        round_num, len(round_text), fc_part.function_call.name if fc_part else None)

            if not fc_part:
                # No tool call — stream the text response to Vapi
                if round_text:
                    words = round_text.split(" ")
                    chunk_size = 3
                    for i in range(0, len(words), chunk_size):
                        chunk_text = " ".join(words[i:i + chunk_size])
                        if i > 0:
                            chunk_text = " " + chunk_text
                        yield _sse({"content": chunk_text})
                        full_content += chunk_text
                break

            # ── Execute the function call ──────────────────────────────
            fn_name = fc_part.function_call.name
            fn_args = dict(fc_part.function_call.args) if fc_part.function_call.args else {}

            logger.info("[LLM Proxy]   🔧 TOOL (Gemini): %s(%s)", fn_name, json.dumps(fn_args)[:300])

            # Date correction for get_available_slots
            if fn_name == "get_available_slots":
                user_text = _last_user_message(messages)
                tenant_tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
                resolved = _resolve_dow_to_date(user_text, tz_name=tenant_tz)
                model_date = fn_args.get("date", "")
                if resolved and resolved != model_date:
                    logger.warning("[LLM Proxy]   📅 Date correction: %r → %r", model_date, resolved)
                    fn_args["date"] = resolved

            tool_result = await _execute_tool(fn_name, fn_args, tenant_ctx=tenant_ctx, caller_phone=caller_phone)
            logger.info("[LLM Proxy]   🔧 RESULT (Gemini): %s",
                        json.dumps(tool_result, default=str)[:500])

            # ── Vapi live-transfer (same logic as Ollama path) ─────────
            if (
                fn_name == "escalate_to_human"
                and isinstance(tool_result, dict)
                and tool_result.get("action") == "transfer"
            ):
                transfer_dest = (tool_result.get("destination") or "").strip()
                if not transfer_dest and tenant_ctx:
                    transfer_dest = (
                        tenant_ctx.escalation_transfer_number
                        or tenant_ctx.escalation_phone
                        or ""
                    ).strip()
                if transfer_dest:
                    logger.info("[LLM Proxy] 📞 escalate_to_human → Vapi transferCall to %s", transfer_dest)
                    handoff = "Let me transfer you to one of our team members now. Please hold."
                    words = handoff.split(" ")
                    for i in range(0, len(words), 3):
                        chunk_text = " ".join(words[i:i + 3])
                        if i > 0:
                            chunk_text = " " + chunk_text
                        yield _sse({"content": chunk_text})
                        full_content += chunk_text
                    yield _sse({
                        "tool_calls": [{
                            "index": 0,
                            "id": f"call_transfer_{int(time.time())}",
                            "type": "function",
                            "function": {
                                "name": "transferCall",
                                "arguments": json.dumps({"destination": transfer_dest}),
                            },
                        }]
                    })
                    yield _sse({}, finish_reason="tool_calls")
                    yield "data: [DONE]\n\n"
                    return
                else:
                    logger.info("[LLM Proxy] escalate_to_human ran but no transfer number — SMS only")

            if not isinstance(tool_result, dict):
                tool_result = {"result": tool_result}

            # Feed the tool result back to Gemini via the same chat session.
            # The chat object tracks history internally, so we just send the
            # function response and it appends the prior model function_call +
            # this function_response automatically.
            t_round = time.time()
            response = await _gemini_send_with_retry(
                chat,
                gtypes.Part(
                    function_response=gtypes.FunctionResponse(
                        name=fn_name, response=tool_result,
                    ),
                ),
            )
            round_ms = (time.time() - t_round) * 1000
            logger.info("[LLM Proxy] ── Gemini round %d: %.0fms ──", round_num + 1, round_ms)
        else:
            # Loop exhausted MAX_TOOL_ROUNDS — the last `response` from Gemini
            # (after the final function_response) was never checked. Extract
            # whatever text Gemini returned so the caller doesn't get silence.
            logger.warning("[LLM Proxy] Exceeded %d Gemini tool rounds", MAX_TOOL_ROUNDS)
            if response and response.candidates:
                final_text = _extract_gemini_text(response)
                if final_text:
                    words = final_text.split(" ")
                    for i in range(0, len(words), 3):
                        chunk_text = " ".join(words[i:i + 3])
                        if i > 0:
                            chunk_text = " " + chunk_text
                        yield _sse({"content": chunk_text})
                        full_content += chunk_text
            if not full_content:
                fallback = "I'm sorry, I'm having trouble accessing our scheduling system right now. Could you try again in a moment?"
                yield _sse({"content": fallback})
                full_content += fallback

    except Exception as exc:
        elapsed = (time.time() - t0) * 1000
        logger.error("[LLM Proxy] Gemini streaming failed after %.0fms: %s", elapsed, exc, exc_info=True)
        yield _sse({"content": "I apologize, I'm having a brief technical difficulty. Could you repeat that?"})

    elapsed = (time.time() - t0) * 1000
    logger.info("=" * 70)
    logger.info("[LLM Proxy] STREAMED RESPONSE TO VAPI (Gemini)")
    logger.info("[LLM Proxy]   Total time:   %.0fms", elapsed)
    logger.info("[LLM Proxy]   Content len:  %d chars", len(full_content))
    logger.info("[LLM Proxy]   Content:      %s", full_content[:500])
    logger.info("=" * 70)

    yield _sse({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


def _error_response(msg: str) -> dict:
    """Return an OpenAI-format error response."""
    return {
        "id": "error",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": msg}, "finish_reason": "stop"}],
    }


_DOW_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


# ── Tool call text detection (for models that output tool calls as text) ─────
# Some models (Qwen3, etc.) sometimes output tool calls as text instead of using
# the proper function calling API. These helpers detect and strip such patterns.

_TOOL_TAG_PATTERNS = [
    ("<tool_call>", "</tool_call>"),
    ("<function_call>", "</function_call>"),
    ("<|tool_call|>", "<|/tool_call|>"),
    ("<|function|>", "<|/function|>"),
    ("<tool>", "</tool>"),
    ("<function>", "</function>"),
]


def _has_tool_tag_start(text: str) -> bool:
    """Check if text contains the start of any tool call tag pattern."""
    return any(start in text for start, _ in _TOOL_TAG_PATTERNS)


def _has_tool_tag_end(text: str) -> bool:
    """Check if text contains the end of any tool call tag pattern."""
    return any(end in text for _, end in _TOOL_TAG_PATTERNS)


def _strip_tool_block(text: str) -> tuple[str, str]:
    """
    Strip a tool call block from text, returning (before, after).
    Handles multiple tag formats.
    """
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
    import re as _re_mod
    for start_tag, end_tag in _TOOL_TAG_PATTERNS:
        pattern = _re_mod.escape(start_tag) + r"\s*(\{.*?\})\s*" + _re_mod.escape(end_tag)
        match = _re_mod.search(pattern, text, _re_mod.DOTALL)
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


def _last_user_message(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return (m.get("content") or "").strip().lower()
    return ""


_HUMAN_REQUEST_PHRASES = [
    "speak to a human", "speak with a human", "talk to a human",
    "real person", "real human", "speak to someone",
    "transfer me", "transfer to a", "speak to staff",
    "speak to a person", "talk to a person",
]


def _is_explicit_human_request(messages: list) -> bool:
    user_text = _last_user_message(messages)
    return any(p in user_text for p in _HUMAN_REQUEST_PHRASES)


def _resolve_dow_to_date(user_text: str, tz_name: str = DEFAULT_TIMEZONE) -> str | None:
    """
    If the user's message mentions a day-of-week or 'tomorrow'/'today',
    return the corresponding YYYY-MM-DD in the tenant's timezone.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    today = datetime.now(tz)
    text = user_text.lower()

    if "today" in text:
        return today.strftime("%Y-%m-%d")
    if "tomorrow" in text:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    for dow_name, dow_idx in _DOW_MAP.items():
        if dow_name in text:
            today_idx = today.weekday()
            delta = (dow_idx - today_idx) % 7
            # "this X" and "next X" both mean the nearest upcoming occurrence.
            # Callers use them interchangeably (matches the system prompt).
            if delta == 0:
                delta = 7  # If today IS that day, go to next week
            target = today + timedelta(days=delta)
            return target.strftime("%Y-%m-%d")

    return None


def _looks_like_simple_chat(messages: list) -> bool:
    """
    Heuristic: detect short greetings / generic small-talk where the model
    should NOT have tools available.

    CRITICAL: Only applies to the FIRST user message. Once a multi-turn
    conversation is underway, tools must ALWAYS be available — the user
    might respond with short answers like "doc1", "yes", "2pm" that need tools.
    """
    # Count user messages — if more than 1, conversation is in progress
    user_msg_count = sum(1 for m in messages if m.get("role") == "user")
    if user_msg_count > 1:
        return False

    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = (m.get("content") or "").strip().lower()
            break

    if not last_user:
        return False

    if len(last_user) > 80:
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
        # Caller lookup
        "my appointment", "existing", "upcoming", "check on",
        "do i have", "any appointment", "look up", "find my",
    ]
    if any(kw in last_user for kw in tool_keywords):
        return False

    return True


async def _call_with_tools(
    client: OpenAI,
    model: str,
    messages: list,
    t0: float,
    tenant_ctx: TenantContext | None = None,
    caller_phone: str = "",
) -> str:
    """
    Call Ollama with tool definitions. If it requests a tool call,
    execute it, add the result to messages, and call again.
    """
    content = ""

    # ── Deterministic escalation short-circuit ──────────────────────────
    has_escalation = bool(tenant_ctx and tenant_ctx.emergency_guidance)
    if _is_explicit_human_request(messages) and has_escalation:
        logger.info("[LLM Proxy] Explicit human request — short-circuiting LLM in _call_with_tools")
        try:
            await _execute_tool(
                "escalate_to_human",
                {"reason": "Caller explicitly asked to speak to a human"},
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            logger.warning("[LLM Proxy] escalate_to_human side-effect failed: %s", exc)
        return "Of course — let me connect you with one of our team members. Please hold."
    elif _is_explicit_human_request(messages) and not has_escalation:
        logger.info("[LLM Proxy] Explicit human request but escalation not configured — skipping short-circuit")

    suppress_tools_round1 = _looks_like_simple_chat(messages)
    if suppress_tools_round1:
        logger.info("[LLM Proxy] Detected simple chat — suppressing tools for round 1")

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        logger.info("[LLM Proxy] ── LLM call round %d ──", round_num)

        use_tools = not (suppress_tools_round1 and round_num == 1)

        t_round = time.time()
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
            "stream": False,
        }
        if use_tools:
            kwargs["tools"] = get_tools(tenant_ctx)
        response = client.chat.completions.create(**kwargs)
        round_ms = (time.time() - t_round) * 1000

        choice = response.choices[0]
        content = choice.message.content or ""
        finish = choice.finish_reason

        # Strip Qwen3 thinking blocks
        if "<think>" in content:
            import re
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
            logger.info("[LLM Proxy]   Stripped <think> block, post-strip=%d chars", len(content))

        logger.info("[LLM Proxy]   Round %d: %.0fms, finish=%s, content=%d chars, tool_calls=%s",
                    round_num, round_ms, finish, len(content), bool(choice.message.tool_calls))

        if not choice.message.tool_calls:
            if content:
                logger.info("[LLM Proxy]   Round %d → text response: %s", round_num, content[:200])
            return content

        # ── Handle tool calls ────────────────────────────────────────
        for tc in choice.message.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info("[LLM Proxy]   🔧 TOOL CALL: %s(%s)", fn_name, json.dumps(fn_args)[:300])

            # ── Correct date if user said a day-of-week ──
            if fn_name == "get_available_slots":
                user_text = _last_user_message(messages)
                tenant_tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
                resolved = _resolve_dow_to_date(user_text, tz_name=tenant_tz)
                model_date = fn_args.get("date", "")
                if resolved and resolved != model_date:
                    logger.warning("[LLM Proxy]   📅 Date correction: model said %r → user-implied %r",
                                   model_date, resolved)
                    fn_args["date"] = resolved

            tool_result = await _execute_tool(fn_name, fn_args, tenant_ctx=tenant_ctx, caller_phone=caller_phone)

            logger.info("[LLM Proxy]   🔧 TOOL RESULT: %s", json.dumps(tool_result, default=str)[:500])

            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": tc.function.arguments,
                        },
                    }
                ],
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result, default=str),
            })

    logger.warning("[LLM Proxy] Exceeded %d tool rounds — returning last content", MAX_TOOL_ROUNDS)
    return content or "I apologize, I'm having difficulty processing your request. Could you repeat that?"


async def _execute_tool(
    name: str,
    args: dict,
    tenant_ctx: TenantContext | None = None,
    caller_phone: str = "",
) -> dict[str, Any]:
    """
    Execute a tool call and return the result dict.
    Uses tenant_ctx for per-tenant API keys, event types, etc.
    """
    t0 = time.time()
    logger.info("[Tool Exec] Executing: %s (tenant=%s)", name,
                tenant_ctx.slug if tenant_ctx else "default")

    # ── Normalise any phone number in the args to E.164 ──────────────
    # The AI agent receives bare digits from the caller ("6352418405")
    # or sometimes with partial formatting. We normalise using the
    # tenant's region so "6352418405" → "+16352418405" for a US tenant
    # and "9876543210" → "+919876543210" for an Indian tenant.
    if "phone" in args and args["phone"]:
        from backend.services.caller_service import _normalise_phone, _region_from_timezone
        region = _region_from_timezone(tenant_ctx.timezone if tenant_ctx else None)
        raw_phone = args["phone"]
        args["phone"] = _normalise_phone(raw_phone, default_region=region)
        if args["phone"] != raw_phone:
            logger.info("[Tool Exec] Normalised phone: %s → %s (region=%s)",
                        raw_phone, args["phone"], region)

    # ── Privacy gate: block cross-caller data access ────────────────
    # Same enforcement as llm_service.py — tools that access caller
    # data MUST use the caller's own phone number.
    from backend.services.llm_service import _PHONE_GATED_TOOLS, _normalize_phone_for_match
    if name in _PHONE_GATED_TOOLS and caller_phone:
        tool_phone = args.get("phone", "")
        if tool_phone:
            caller_norm = _normalize_phone_for_match(caller_phone)
            tool_norm = _normalize_phone_for_match(tool_phone)
            if caller_norm and tool_norm and caller_norm != tool_norm:
                logger.warning(
                    "[Tool Exec] PRIVACY BLOCK: tool=%s tried phone=%s but caller=%s",
                    name, tool_phone, caller_phone,
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

    try:
        if name == "get_available_slots":
            appt_type = args.get("appointment_type", "consultation")

            date = args.get("date", "")
            if not date:
                from datetime import timedelta
                date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

            slots = await calendar_service.get_available_slots(
                date_from=date,
                date_to=date,
                tenant_ctx=tenant_ctx,
                appointment_type_key=appt_type,
            )

            formatted_slots = []
            for s in slots:
                try:
                    from dateutil import parser as dt_parser
                    dt = dt_parser.parse(s)
                    formatted_slots.append({
                        "time_label": dt.strftime("%I:%M %p").lstrip("0"),
                        "exact_slot_time": s,
                    })
                except Exception:
                    formatted_slots.append({"time_label": s, "exact_slot_time": s})

            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] get_available_slots → %d slots in %.0fms", len(slots), elapsed)

            from dateutil import parser as dt_parser
            try:
                friendly_date = dt_parser.parse(date).strftime("%A, %B %d")
            except Exception:
                friendly_date = date

            if not formatted_slots:
                summary = (
                    f"There are no available appointment slots on {friendly_date}. "
                    f"Tell the caller politely that day is fully booked or closed, "
                    f"and offer to check a different day. Do NOT read this message verbatim — "
                    f"speak naturally in 1-2 sentences."
                )
            else:
                top = formatted_slots[:3]
                time_list = ", ".join(s["time_label"] for s in top)
                summary = (
                    f"Available times on {friendly_date}: {time_list}. "
                    f"Offer these to the caller in natural conversation (1-2 sentences). "
                    f"When they pick one, call book_appointment with the matching exact_slot_time. "
                    f"Include provider_id if the caller chose a specific provider. "
                    f"Do NOT read JSON or field names aloud."
                )

            return {
                "summary_for_assistant": summary,
                "available_slots": formatted_slots,
                "date": date,
                "count": len(slots),
            }

        elif name == "book_appointment":
            # ── Validate provider_id is present (or auto-assign) ─────
            provider_id_raw = (args.get("provider_id") or "").strip()
            if not provider_id_raw:
                logger.warning("[Tool Exec] book_appointment rejected — no provider_id")
                return {
                    "ok": False,
                    "error": "missing_provider",
                    "summary_for_assistant": (
                        "Provider is required — please ask the caller for their provider "
                        "preference first, or use provider_id='__auto__' for automatic assignment."
                    ),
                }

            # ── Validate required fields ──────────────────────────────
            required = {
                "caller_name": "the caller's full name",
                "phone": "the caller's phone number",
                "dob": "the caller's date of birth",
                "slot_time": "a confirmed appointment time (call get_available_slots first)",
                "appointment_type": "the appointment type",
            }
            missing = []
            for field, desc in required.items():
                val = args.get(field, "")
                if not val or not str(val).strip():
                    missing.append((field, desc))

            if missing:
                missing_list = "; ".join(f"{f} ({d})" for f, d in missing)
                logger.warning("[Tool Exec] book_appointment rejected — missing: %s",
                               [f for f, _ in missing])
                return {
                    "ok": False,
                    "error": "missing_required_fields",
                    "missing": [f for f, _ in missing],
                    "summary_for_assistant": (
                        f"Cannot book yet — missing: {missing_list}. "
                        f"Ask the caller ONLY for the missing items above in a natural sentence."
                    ),
                }

            appt_type = args.get("appointment_type", "consultation")

            # Email: leave blank if caller didn't provide one
            email = args.get("email", "") or ""

            # Provider: look up name or auto-assign
            provider_id_str = (args.get("provider_id") or "").strip()
            provider_id = None
            provider_name = None
            auto_assigned = False

            if provider_id_str == "__auto__":
                # Auto-assign: find the best available provider
                try:
                    from backend.services import provider_service
                    from dateutil import parser as dt_parser_prov
                    slot_for_auto = args.get("slot_time", "")
                    auto_dt = dt_parser_prov.parse(slot_for_auto) if slot_for_auto else None
                    appt_type_for_auto = args.get("appointment_type", "consultation")
                    # Get duration from tenant appointment types
                    auto_duration = 30
                    if tenant_ctx and tenant_ctx.appointment_types:
                        slug = slugify_appointment_type(appt_type_for_auto)
                        for at in tenant_ctx.appointment_types:
                            if slugify_appointment_type(at.get("code", "")) == slug:
                                auto_duration = at.get("duration_minutes", 30)
                                break

                    if auto_dt and tenant_ctx:
                        auto_provider = await provider_service.auto_assign_provider(
                            tenant_id=tenant_ctx.tenant_id,
                            appointment_type=appt_type_for_auto,
                            requested_datetime=auto_dt,
                            duration_minutes=auto_duration,
                        )
                        if auto_provider:
                            import uuid as uuid_mod
                            provider_id = uuid_mod.UUID(auto_provider["id"])
                            provider_name = auto_provider["name"]
                            provider_id_str = auto_provider["id"]
                            args["provider_id"] = provider_id_str
                            auto_assigned = True
                            logger.info("[Tool Exec] Auto-assigned provider: %s (%s)", provider_name, provider_id)
                        else:
                            logger.info("[Tool Exec] Auto-assign failed — no providers available")
                            return {
                                "ok": False,
                                "no_provider_available": True,
                                "summary_for_assistant": (
                                    "No providers are available at the requested time. "
                                    "Offer to add the caller to the waitlist — they'll be "
                                    "notified as soon as a slot opens up."
                                ),
                            }
                    else:
                        return {
                            "ok": False,
                            "error": "missing_slot_time",
                            "summary_for_assistant": "Cannot auto-assign without a slot time. Please confirm the time first.",
                        }
                except Exception as auto_exc:
                    logger.warning("[Tool Exec] Auto-assign error: %s", auto_exc)
                    return {
                        "ok": False,
                        "error": "auto_assign_failed",
                        "summary_for_assistant": "Could not auto-assign a provider. Please ask the caller for a provider preference.",
                    }
            elif provider_id_str:
                try:
                    from backend.services import provider_service
                    import uuid as uuid_mod
                    provider_id = uuid_mod.UUID(provider_id_str)
                    provider = await provider_service.get_provider(provider_id)
                    if provider:
                        provider_name = provider.get("name", "")
                        logger.info("[Tool Exec] Provider selected: %s (%s)", provider_name, provider_id)
                except Exception as prov_exc:
                    logger.warning("[Tool Exec] Provider lookup failed: %s", prov_exc)

            caller_info = {
                "name": args.get("caller_name", ""),
                "phone": args.get("phone", ""),
                "email": email,
                "dob": args.get("dob", ""),
            }

            slot_time = args.get("slot_time", "")

            # ── Smart slot matching ──────────────────────────────────
            logger.info("[Tool Exec] LLM provided slot_time: '%s' — will attempt smart matching", slot_time)
            try:
                from dateutil import parser as dt_parser
                requested_dt = dt_parser.parse(slot_time)
                target_hour = requested_dt.hour
                target_minute = requested_dt.minute
                correct_date = requested_dt.replace(year=datetime.now(timezone.utc).year)
                date_str = correct_date.strftime("%Y-%m-%d")

                available = await calendar_service.get_available_slots(
                    date_from=date_str,
                    date_to=date_str,
                    tenant_ctx=tenant_ctx,
                    appointment_type_key=appt_type,
                )

                matched = False
                for real_slot in available:
                    slot_dt = dt_parser.parse(real_slot)
                    if slot_dt.hour == target_hour and slot_dt.minute == target_minute:
                        slot_time = real_slot
                        matched = True
                        break

                if not matched:
                    for real_slot in available:
                        slot_dt = dt_parser.parse(real_slot)
                        if slot_dt.hour == target_hour:
                            slot_time = real_slot
                            matched = True
                            break

            except Exception as match_exc:
                logger.warning("[Tool Exec] Slot matching failed: %s — using original", match_exc)

            booking_notes = args.get("notes", "").strip() or None
            result = await calendar_service.book_appointment(
                caller_info=caller_info,
                start_time=slot_time,
                tenant_ctx=tenant_ctx,
                appointment_type_key=appt_type,
                provider_id=provider_id,
                notes=booking_notes,
            )

            if result:
                # Send SMS confirmation
                sms_sent = False
                try:
                    scheduled_dt = datetime.fromisoformat(args.get("slot_time", ""))
                    sms_service.send_confirmation(
                        caller_name=args.get("caller_name", ""),
                        phone=args.get("phone", ""),
                        appointment_type=args.get("appointment_type", "").replace("_", " ").title(),
                        scheduled_at=scheduled_dt,
                        tenant_ctx=tenant_ctx,
                    )
                    sms_sent = True
                except Exception as sms_exc:
                    logger.warning("[Tool Exec] SMS failed: %s", sms_exc)

                # Note: caller upsert + appointment creation are handled inside
                # native_scheduling.create_native_booking() — no need to call
                # record_session() separately (that would create duplicates).

                elapsed = (time.time() - t0) * 1000
                logger.info("[Tool Exec] book_appointment → SUCCESS in %.0fms: %s", elapsed, result)

                # Format confirmation for the LLM (tenant-timezone-aware)
                try:
                    from backend.tools.platform import _fmt_slot_display
                    _proxy_tz = tenant_ctx.timezone if tenant_ctx else DEFAULT_TIMEZONE
                    time_str, date_str = _fmt_slot_display(slot_time, _proxy_tz)
                    if not date_str:
                        date_str = "the requested date"
                except Exception:
                    time_str = slot_time
                    date_str = "the requested date"

                provider_msg = f" with {provider_name}" if provider_name else ""
                sms_note = "They'll receive an SMS confirmation shortly." if sms_sent else ""

                response = {"success": True, "booking": result}
                if auto_assigned and provider_name:
                    response["auto_assigned_provider"] = provider_name
                response["summary_for_assistant"] = (
                    f"Booking confirmed for {time_str} on {date_str}{provider_msg}. "
                    f"Tell the caller their session is booked. {sms_note} "
                    f"Do NOT make up any details not provided. "
                    f"Just confirm the time, date{', and provider' if provider_name else ''}, wish them well, and ask if there's anything else."
                )
                return response

            elapsed = (time.time() - t0) * 1000
            logger.error("[Tool Exec] book_appointment → FAILED in %.0fms", elapsed)
            return {
                "success": False,
                "error": "Failed to create booking.",
                "summary_for_assistant": (
                    "The booking could not be completed due to a technical issue. "
                    "Apologize to the caller and offer to try again or check a different time. "
                    "Do NOT make up reasons for the failure."
                ),
            }

        elif name == "reschedule_appointment":
            booking_uid = args.get("booking_uid", "")
            new_slot_time = args.get("new_slot_time", "")
            new_provider_id = args.get("provider_id", "") or None
            result = await calendar_service.reschedule_appointment(
                booking_uid=booking_uid,
                new_start_time=new_slot_time,
                tenant_ctx=tenant_ctx,
                provider_id=new_provider_id,
            )
            elapsed = (time.time() - t0) * 1000
            if result:
                # Fetch caller info from the appointment for SMS
                _sms_name = ""
                _sms_phone = ""
                _sms_type = "Appointment"
                try:
                    from backend.database import async_session as get_async_session
                    from sqlalchemy import select as sa_select
                    from backend.models.appointment import Appointment as ApptModel
                    async with get_async_session() as db_session:
                        stmt = sa_select(ApptModel).where(ApptModel.cal_booking_uid == booking_uid)
                        db_result = await db_session.execute(stmt)
                        appt = db_result.scalar_one_or_none()
                        if appt:
                            _sms_name = appt.client_name or ""
                            _sms_phone = appt.client_phone or ""
                            _sms_type = (appt.appointment_type or "Appointment").replace("_", " ").title()
                except Exception as db_exc:
                    logger.warning("[Tool Exec] Failed to fetch caller info for reschedule SMS: %s", db_exc)

                # Send reschedule SMS with actual caller data
                try:
                    from dateutil import parser as dt_parser
                    new_dt = dt_parser.parse(new_slot_time)
                    if _sms_phone:
                        sms_service.send_reschedule(
                            caller_name=_sms_name,
                            phone=_sms_phone,
                            new_scheduled_at=new_dt,
                            appointment_type=_sms_type,
                            tenant_ctx=tenant_ctx,
                        )
                except Exception as sms_exc:
                    logger.warning("[Tool Exec] Reschedule SMS failed: %s", sms_exc)
                logger.info("[Tool Exec] reschedule → SUCCESS in %.0fms", elapsed)
                return {"success": True, "reschedule": result}
            logger.error("[Tool Exec] reschedule → FAILED in %.0fms", elapsed)
            return {"success": False, "error": "Failed to reschedule"}

        elif name == "cancel_appointment":
            booking_uid = args.get("booking_uid", "")
            success = await calendar_service.cancel_appointment(
                booking_uid=booking_uid,
                reason=args.get("reason", ""),
                tenant_ctx=tenant_ctx,
            )
            if success:
                # Update DB status
                try:
                    from backend.database import async_session as get_async_session
                    from sqlalchemy import select as sa_select
                    from backend.models.appointment import Appointment as ApptModel, AppointmentStatus
                    async with get_async_session() as db_session:
                        stmt = sa_select(ApptModel).where(ApptModel.cal_booking_uid == booking_uid)
                        db_result = await db_session.execute(stmt)
                        appt = db_result.scalar_one_or_none()
                        if appt:
                            appt.status = AppointmentStatus.CANCELLED
                            await db_session.commit()
                            logger.info("[Tool Exec] ✓ DB appointment cancelled: %s", booking_uid)
                except Exception as db_exc:
                    logger.warning("[Tool Exec] DB cancel update failed: %s", db_exc)
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] cancel → %s in %.0fms", success, elapsed)
            return {"success": success}

        elif name == "escalate_to_human":
            sms_service.send_office_alert(
                reason=args.get("reason", "Caller requested human assistance"),
                caller_number="unknown",
                tenant_ctx=tenant_ctx,
            )
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] escalate → done in %.0fms", elapsed)
            return {"success": True, "action": "transfer"}

        elif name == "send_callback_request":
            sms_service.send_escalation_notification(
                caller_name=args.get("caller_name", ""),
                phone=args.get("phone", ""),
                tenant_ctx=tenant_ctx,
            )
            sms_service.send_office_alert(
                reason=f"Callback: {args.get('reason', 'N/A')}",
                caller_number=args.get("phone", ""),
                tenant_ctx=tenant_ctx,
            )
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] callback → done in %.0fms", elapsed)
            return {"success": True, "message": "Callback request sent"}

        elif name == "get_office_info":
            from backend.tools.platform import _build_office_info
            result = _build_office_info(args.get("topic", "all"), tenant_ctx)
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] get_office_info(%s) → done in %.0fms", args.get("topic"), elapsed)
            return result

        elif name == "lookup_caller_appointments":
            phone = args.get("phone", "")
            if not phone:
                return {
                    "ok": False,
                    "summary_for_assistant": "I need the caller's phone number to look up their sessions.",
                }

            tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
            tz_name = tenant_ctx.timezone if tenant_ctx else None
            history = await caller_service.get_caller_history(phone, tenant_id=tenant_id, tz_name=tz_name)
            if not history:
                return {
                    "ok": False,
                    "summary_for_assistant": (
                        f"No record found for {phone}. "
                        "This might be a new caller. Ask if they'd like to schedule a new session."
                    ),
                }

            upcoming = history.get("upcoming_appointments", [])
            caller_name = history["caller"]["name"]
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] lookup_caller_appointments → %d upcoming for %s in %.0fms",
                        len(upcoming), caller_name, elapsed)

            if not upcoming:
                return {
                    "ok": True,
                    "summary_for_assistant": (
                        f"{caller_name} has no upcoming sessions. "
                        "Ask if they'd like to schedule a new one."
                    ),
                    "caller_name": caller_name,
                    "upcoming": [],
                }

            def _appt_line(a):
                line = f"{a['type']} on {a['date']} at {a['time']}"
                if a.get("provider_name"):
                    title = f" ({a['provider_title']})" if a.get("provider_title") else ""
                    line += f" with {a['provider_name']}{title}"
                if a.get("duration_minutes"):
                    line += f" ({a['duration_minutes']} min)"
                return line

            appt_lines = [_appt_line(a) for a in upcoming]

            return {
                "ok": True,
                "summary_for_assistant": (
                    f"{caller_name} has {len(upcoming)} upcoming session(s): "
                    + "; ".join(appt_lines) + ". "
                    "Tell the caller about their class booking(s) naturally — include the trainer name and session details. "
                    "If they want to reschedule, use the booking_uid with reschedule_appointment."
                ),
                "caller_name": caller_name,
                "upcoming": upcoming,
            }

        elif name == "add_to_waitlist":
            from backend.services import waitlist_service
            result = await waitlist_service.add_to_waitlist(
                tenant_id=tenant_ctx.tenant_id if tenant_ctx else None,
                client_name=args.get("caller_name", ""),
                client_phone=args.get("phone", ""),
                appointment_type=args.get("appointment_type", "consultation"),
                preferred_date=args.get("preferred_date", ""),
            )
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] add_to_waitlist → %s in %.0fms", result.get("status"), elapsed)
            return result

        elif name == "check_waitlist_status":
            from backend.services import waitlist_service

            phone = args.get("phone", "")
            if not phone:
                return {
                    "ok": False,
                    "summary_for_assistant": "I need the caller's phone number to check their waitlist status.",
                }

            tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
            if not tenant_id:
                return {"ok": False, "summary_for_assistant": "Unable to check waitlist — no tenant context."}

            result = await waitlist_service.check_waitlist_status(
                client_phone=phone,
                tenant_id=tenant_id,
            )
            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] check_waitlist_status → %d entries in %.0fms",
                        len(result.get("entries", [])), elapsed)
            return result

        elif name == "lookup_caller":
            phone = args.get("phone", "")
            caller_name = args.get("name", "")

            if not phone and not caller_name:
                return {
                    "ok": False,
                    "summary_for_assistant": "I need either the caller's phone number or name to look them up.",
                }

            tenant_id = tenant_ctx.tenant_id if tenant_ctx else None
            tz_name = tenant_ctx.timezone if tenant_ctx else None

            # Primary: look up by phone
            history = None
            if phone:
                history = await caller_service.get_caller_history(phone, tenant_id=tenant_id, tz_name=tz_name)

            # Fallback: search by name
            if not history and caller_name:
                matches = await caller_service.get_caller_by_name(
                    caller_name, tenant_id=tenant_id,
                )
                if len(matches) > 1:
                    # Multiple callers share this name — ask for phone to disambiguate
                    elapsed = (time.time() - t0) * 1000
                    match_lines = [
                        f"- {m.name} (phone ending ...{m.phone[-4:]})"
                        for m in matches
                    ]
                    logger.info("[Tool Exec] lookup_caller → %d name matches for '%s' in %.0fms — disambiguation needed",
                                len(matches), caller_name, elapsed)
                    return {
                        "ok": False,
                        "multiple_matches": True,
                        "match_count": len(matches),
                        "summary_for_assistant": (
                            f"Found {len(matches)} callers matching the name '{caller_name}': "
                            + "; ".join(match_lines) + ". "
                            "You already have the caller's phone number from caller-ID. "
                            "Call lookup_caller again with the phone parameter to get the exact match. "
                            "If for some reason you don't have the phone, ask the caller to confirm it."
                        ),
                    }
                elif len(matches) == 1:
                    history = await caller_service.get_caller_history(
                        matches[0].phone, tenant_id=tenant_id, tz_name=tz_name,
                    )

            if not history:
                elapsed = (time.time() - t0) * 1000
                logger.info("[Tool Exec] lookup_caller → not found in %.0fms (phone=%s, name=%s)",
                            elapsed, phone, caller_name)
                return {
                    "ok": False,
                    "summary_for_assistant": (
                        f"No record found for {phone or caller_name}. "
                        "This appears to be a new caller. You will need to collect their "
                        "full name and reason for the session before booking."
                    ),
                }

            p = history["caller"]
            upcoming = history.get("upcoming_appointments", [])
            past = history.get("past_appointments", [])
            last_visit = history.get("last_visit")
            months_since = history.get("months_since_last_visit")

            summary_parts = [f"Caller found: {p['name']}, phone {p['phone']}."]
            if p.get("dob"):
                summary_parts.append(f"DOB: {p['dob']}.")
            else:
                summary_parts.append("DOB: not on file — ask the caller.")
            if p.get("notes"):
                summary_parts.append(f"Notes: {p['notes']}.")
            summary_parts.append(f"Visit count: {p.get('visit_count', 0)}.")

            if last_visit:
                ago = f" ({months_since} months ago)" if months_since is not None else ""
                summary_parts.append(f"Last visit: {last_visit['type']} on {last_visit['date']}{ago}.")

            if upcoming:
                def _appt_summary(a):
                    line = f"{a['type']} on {a['date']} at {a['time']}"
                    if a.get("provider_name"):
                        title = f" ({a['provider_title']})" if a.get("provider_title") else ""
                        line += f" with {a['provider_name']}{title}"
                    return line
                appt_lines = [_appt_summary(a) for a in upcoming]
                summary_parts.append(f"Upcoming sessions: {'; '.join(appt_lines)}.")
            else:
                summary_parts.append("No upcoming sessions.")

            if past:
                past_lines = [f"{a['type']} on {a['date']} ({a['status']})" for a in past[:3]]
                summary_parts.append(f"Recent history: {'; '.join(past_lines)}.")

            summary_parts.append(
                "Use this data when speaking to the caller. Do NOT ask for info you already have. "
                "If DOB is missing, ask for it. If updating any info, use update_caller_info."
            )

            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] lookup_caller → found %s (%d upcoming, %d past) in %.0fms",
                        p['name'], len(upcoming), len(past), elapsed)
            return {
                "ok": True,
                "summary_for_assistant": " ".join(summary_parts),
                "caller": p,
                "upcoming_appointments": upcoming,
                "past_appointments": past,
                "last_visit": last_visit,
                "months_since_last_visit": months_since,
            }

        elif name == "update_caller_info":
            phone = args.get("phone", "")
            if not phone:
                return {
                    "ok": False,
                    "summary_for_assistant": "I need the caller's phone number to update their record.",
                }

            tenant_id = tenant_ctx.tenant_id if tenant_ctx else None

            existing = await caller_service.get_caller_by_phone(phone, tenant_id=tenant_id)
            if not existing:
                return {
                    "ok": True,
                    "summary_for_assistant": (
                        f"This is a new caller — no record exists yet for {phone}. "
                        "That's perfectly normal. Their record (including date of birth and other details) "
                        "will be created automatically when you book the session. "
                        "Continue with the booking as usual — do NOT mention any issue to the caller."
                    ),
                }

            updated_fields = []
            from backend.database import async_session as get_async_session
            async with get_async_session() as db_session:
                from sqlalchemy import select as sa_select
                from sqlalchemy import and_
                from backend.models.caller import Caller

                filters = [Caller.phone == caller_service._normalise_phone(phone)]
                if tenant_id:
                    filters.append(Caller.tenant_id == tenant_id)

                result = await db_session.execute(
                    sa_select(Caller).where(and_(*filters))
                )
                caller_rec = result.scalar_one_or_none()

                if not caller_rec:
                    return {"ok": False, "summary_for_assistant": "Caller record not found."}

                if args.get("name", "").strip():
                    caller_rec.name = args["name"].strip()
                    updated_fields.append("name")
                if args.get("dob", "").strip():
                    caller_rec.date_of_birth = args["dob"].strip()
                    updated_fields.append("date of birth")
                if args.get("email", "").strip():
                    caller_rec.email = args["email"].strip()
                    updated_fields.append("email")
                if args.get("notes", "").strip():
                    caller_rec.notes = args["notes"].strip()
                    updated_fields.append("notes")

                if not updated_fields:
                    return {
                        "ok": False,
                        "summary_for_assistant": "No fields to update were provided. Specify at least one of: name, dob, email, notes.",
                    }

                await db_session.commit()

            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] update_caller_info → updated %s for %s in %.0fms",
                        updated_fields, phone, elapsed)
            return {
                "ok": True,
                "summary_for_assistant": (
                    f"Updated {', '.join(updated_fields)} for {existing.name} ({phone}). "
                    f"The changes are saved. Continue the conversation normally."
                ),
                "updated_fields": updated_fields,
            }

        elif name == "get_providers":
            from backend.services import provider_service
            appt_type = args.get("appointment_type", "")
            if appt_type and tenant_ctx:
                providers = await provider_service.get_providers_for_appointment_type(
                    tenant_ctx.tenant_id, appt_type,
                )
            elif tenant_ctx:
                providers = await provider_service.list_providers(tenant_ctx.tenant_id)
            else:
                providers = []

            if not providers:
                summary = "This studio doesn't have individual trainer profiles configured. Any available trainer can handle the class. Do not pass a provider_id when booking."
            else:
                def _fmt_provider(p):
                    name = p['name']
                    title = p.get('title')
                    return f"{name} ({title})" if title else name
                names = ", ".join(_fmt_provider(p) for p in providers)
                summary = (
                    f"Available trainers: {names}. "
                    f"Tell the caller which trainers are available and ask if they have a preference. "
                    f"When they choose, use that trainer's 'id' from the providers list below in get_available_slots and book_appointment. "
                    f"If they say 'anyone is fine', pass '__auto__' as the provider_id to auto-assign the best available trainer."
                )

            elapsed = (time.time() - t0) * 1000
            logger.info("[Tool Exec] get_providers → %d providers in %.0fms", len(providers), elapsed)
            return {
                "summary_for_assistant": summary,
                "providers": providers,
                "count": len(providers),
            }

        else:
            logger.warning("[Tool Exec] Unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        elapsed = (time.time() - t0) * 1000
        logger.error("[Tool Exec] %s failed after %.0fms: %s", name, elapsed, exc, exc_info=True)
        return {"error": str(exc)}
