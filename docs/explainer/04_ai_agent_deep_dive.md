# 04 — AI / Agent Deep Dive

This is the heart of FitFront. Everything else is plumbing to get user utterances into the loop below and persist the results.

---

## Concept: What is a tool-calling loop?

**Analogy:** A hotel concierge who can *talk* but also pick up an internal phone to ask departments factual questions or make reservations. Each department call is a **tool**. The concierge (LLM) decides when to call, reads the answer, then either calls another department or replies to the guest.

**Contrast with single-shot LLM:**

```
Single-shot:  User → LLM → text answer (may hallucinate "Tuesday 6pm is available")
Tool loop:    User → LLM → tool(get_slots) → DB truth → LLM → text answer grounded in data
                              → tool(book) → DB write → LLM → "You're booked!"
```

FitFront **must** use tools for scheduling because availability lives in Postgres, not in model weights.

---

## Two entry points, one idea

| Channel | HTTP entry | Loop implementation | Tool dispatch |
|---------|------------|---------------------|---------------|
| Browser chat | `POST /api/chat/stream` | `llm_service.process_message_stream` | `tools/registry.py` |
| Voice (Bolna) | `POST /api/llm/chat/completions` | `llm_proxy._stream_with_tools_sse` | Inline `_execute_tool` in `llm_proxy.py` |

**Complexity flag:** These paths are **not fully unified**. Chat uses the registry; voice proxy duplicates tool logic. When reading bugs, check both files.

---

## The loop — step by step (chat path)

ASCII overview:

```
┌──────────┐    messages[]     ┌─────────┐    tool_calls?    ┌──────────┐
│  User    │ ───────────────► │   LLM   │ ─── yes ────────► │  Tools   │
│  message │                  │ Ollama/ │                   │ registry │
└──────────┘                  │ Gemini  │ ◄── JSON result ─ └──────────┘
                              └─────────┘
                                   │ no tool_calls
                                   ▼
                              Stream text to user
```

### 1. Session lookup / create

```python
# backend/services/llm_service.py:1050–1054
session = get_session(call_id)
if session is None:
    session = create_session(call_id, caller_number=caller_number, tenant_ctx=tenant_ctx)
```

Sessions live in an **in-memory dict** keyed by call/conversation id (`llm_service.py:96`). Stale sessions expire after 2 hours (`llm_service.py:100`).

Chat session keys: `chat-{user.id}-{conversation_id}` (`backend/routes/chat.py:187–195`).

### 2. Append user message

```python
# llm_service.py:1058–1062
session["messages"].append({
    "role": "user",
    "content": user_message,
    "timestamp": datetime.now(timezone.utc).isoformat(),
})
```

Timestamps are stripped before sending to the LLM (`llm_service.py:1449–1455`).

### 3. Optional tool suppression for small talk

```python
# llm_service.py:1071–1074
suppress_tools = _looks_like_simple_chat(user_message, session["messages"])
tools_for_request = None if suppress_tools else get_tools(effective_ctx)
```

Heuristic: first message only, short text without scheduling keywords (`llm_service.py:368–412`). Prevents the model from calling `book_appointment` on "hello".

### 4. Tool round loop (max 5)

```python
# llm_service.py:1079
for tool_round in range(5):  # MAX_TOOL_ROUNDS
```

Voice proxy uses the same limit:

```python
# backend/routes/llm_proxy.py:58
MAX_TOOL_ROUNDS = 5
```

Each round:
1. Stream LLM completion with `tools=` parameter
2. Accumulate streamed `tool_calls` fragments (`llm_service.py:1109–1120`)
3. If no tool calls → break, yield remaining text
4. Else execute each tool, append assistant `tool_calls` + `tool` result messages (`llm_service.py:1232–1248`)
5. Loop — LLM sees tool results and decides next step

**Termination conditions:**
- Model returns text without tool calls
- 5 rounds exhausted (safety cap)
- Escalation short-circuit in voice proxy (transfer/end call — `llm_proxy.py:664–710`)

### 5. Tool execution (chat path)

```python
# llm_service.py:1424–1438
privacy_rejection = _enforce_caller_phone(session, name, args, call_id)
# ...
return await get_registry().dispatch(name, args, session_ctx)
```

**Privacy gate:** Tools that access caller data must use the session's caller-ID phone, not a different number the model guessed (`llm_service.py:1329–1398`). Prevents "look up someone else's appointments."

### 6. Registry dispatch order

```python
# backend/tools/registry.py:49–60
if self._platform.handles(name):
    return await self._platform.call_tool(...)
if self._fitness.handles(name):
    return await self._fitness.call_tool(...)
return await self._custom.call_tool(...)  # tenant-defined tools from DB
```

Providers:
- **Platform** — scheduling, KB, SMS, escalation (`backend/tools/platform.py`)
- **Fitness** — studio-specific tools (`backend/tools/fitness.py`)
- **Custom** — webhook/KB handlers per tenant (`backend/tools/tenant_custom.py`)

### 7. Hallucinated / malformed tool calls

FitFront handles several failure modes:

| Problem | Handling |
|---------|----------|
| Model emits `<tool_call>{json}</tool_call>` as text | Parsed by `_parse_tool_call_text` (`llm_service.py:72–90`, `1195–1204`) |
| Qwen3 `` blocks | Buffered and stripped from user-visible stream (`llm_service.py:1096–1170`) |
| Invalid JSON in tool args | Defaults to `{}` (`llm_service.py:1219–1221`) |
| Unknown tool name | Registry returns `{"error": "Unknown tool: ..."}` (`registry.py:59–60`) |
| Tool exception | Caught, returned as `{"error": str(exc)}` (`llm_service.py:1439–1442`) |
| Empty LLM response | Fallback apology string (`llm_service.py:1252–1260`) |

The model sees tool errors in the next round and can recover — classic ReAct pattern.

---

## Tool schemas

OpenAI "function calling" format — list of `{type: "function", function: {name, description, parameters}}`.

Defined in `backend/tools/platform.py:66+` as `TOOL_SCHEMAS`. Examples include:

- `get_available_slots`
- `book_appointment`
- `reschedule_appointment`
- `cancel_appointment`
- `lookup_caller`
- `get_office_info`
- `add_to_waitlist`
- `escalate_to_human`
- `send_sms`

Tools are **filtered per tenant** — e.g. escalation hidden if Twilio not configured (`PlatformToolProvider.list_tools_sync` in `platform.py`).

**Shim note:** `llm_service.get_tools()` only returns platform tools synchronously (`llm_service.py:352–363`). Full merge including custom tools requires `await get_registry().get_tools(tenant_ctx)`.

---

## System prompt design

Built by `build_system_prompt()` in `backend/prompts/agent_prompt.py:611+`.

### Structure (assembled parts)

1. **Static personality + rules** — agent name, studio name, session types, hours, greeting style (`_build_static_prompt` / fitness variant)
2. **Current date/time context** — day-of-week mappings so "this Friday" resolves correctly
3. **Caller phone/name** — from caller-ID or test phone; full profile **not** embedded upfront
4. **Tool usage rules** — when to call which tool, anti-hallucination instructions

### Key design choice: lazy caller context

```python
# backend/prompts/agent_prompt.py:22
# KB content is now served via get_office_info tool — not injected into prompt
```

```python
# agent_prompt.py:625–626
# caller_context: DEPRECATED — full caller data fetched lazily via lookup_caller
```

**Why:** Context window is finite. Injecting full CRM + KB every turn is expensive and stale. Instead:

- Prompt tells model to call `lookup_caller` when it needs history
- Prompt tells model to call `get_office_info(topic=...)` for hours/pricing/FAQs

Example instruction from prompt:

```text
# agent_prompt.py:146 (fitness prompt section)
1. Answer questions about the studio — ALWAYS call get_office_info for hours,
   location, services, or pricing. NEVER answer these from memory.
```

### Prompt refresh during session

```python
# llm_service.py:118–141
def _refresh_system_time(session):
    """Update the CURRENT TIME line in the system prompt..."""
```

Long chat sessions get updated clock without rebuilding entire prompt.

### Tenant override

`Tenant.system_prompt_override` can replace generated prompt (`tenant.py:82`) — escape hatch for power users.

### Good system prompt principles (as demonstrated here)

1. **Separate facts from behavior** — facts via tools; behavior rules in static text
2. **Explicit tool routing** — "hours → get_office_info(topic='hours')"
3. **Anti-patterns listed** — "NEVER say we offer X unless get_office_info returned it"
4. **Domain vocabulary** — fitness terms (session, member, trainer) baked into fitness prompt builder
5. **Escalation guardrails** — emergency guidance only when escalation phone configured

---

## LLM provider abstraction (Strategy / Adapter pattern)

```python
# backend/config.py — LLM_PROVIDER = "ollama" | "gemini"
```

### Ollama path (local dev)

OpenAI-compatible client pointed at Ollama:

```python
# llm_service.py:143–150 (sync client init pattern)
_client = OpenAI(base_url=settings.ollama_openai_base, api_key="ollama")
```

Async streaming for chat:

```python
# llm_service.py:1081–1091
kwargs = {"model": settings.OLLAMA_MODEL, "messages": ..., "stream": True}
stream = await client.chat.completions.create(**kwargs)
```

Spring analogue: interface `LlmClient` with `OllamaAdapter` implementation.

### Gemini path (cloud / voice)

Uses `google-genai` SDK with native tool calling:

```python
# llm_service.py:26–30
from google import genai
from google.genai import types as gtypes
```

Voice proxy selects Gemini branch when `settings.LLM_PROVIDER == "gemini"` (`llm_proxy.py:97–98`, `_stream_with_tools_sse_gemini` at line 901).

**Why two providers:** Ollama is free/local for dashboard chat dev; Gemini is reachable from Bolna's cloud and supports reliable function calling.

Selection is **config-time**, not per-request — one provider active per deployment.

---

## Conversation / session state

### What's stored (in memory per session)

```python
# create_session in llm_service.py:418+
# Typical session dict keys:
# - messages: list of {role, content, tool_calls?, timestamp?}
# - tenant_ctx: TenantContext snapshot
# - caller_number, is_test, call_start_time
```

### What's re-sent to the LLM each turn

The **full `messages` list** (minus timestamps) — standard chat completion pattern. No separate "memory service"; the transcript *is* the memory.

### Context window concerns

Mitigations in code:
- Lazy tool fetch instead of fat system prompt
- Tool round cap (5)
- `max_tokens: 1024` per generation (`llm_service.py:1084`)
- Simple-chat tool suppression on turn 1 only

**Not implemented:** summarization of old turns, vector memory, external Redis session store.

### Production scaling note

Sessions are process-local (`llm_service.py:96`). Multiple uvicorn workers or Railway replicas would **not** share session state. **Inference:** Single worker is intentional for now (`Dockerfile:21`).

---

## Knowledge grounding (not vector RAG)

FitFront does **not** use embedding search in the codebase reviewed.

### Storage

Per-tenant JSONB: `Tenant.knowledge_base` (`tenant.py:156`). Edited via dashboard `PUT /api/knowledge` (`dashboard.py:364+`). Fallback file: `backend/knowledge/default_kb.json` (`knowledge_service.py:18`).

### Retrieval pattern — tool-based "RAG-lite"

At conversation time, the model calls `get_office_info(topic=...)` which reads KB sections (hours, services, FAQs) and returns formatted text to the model — **retrieval triggered by the LLM**, not automatic chunk injection.

```python
# knowledge_service.py:54–64 — tenant KB isolation
if tenant_ctx is not None:
    return tenant_ctx.knowledge_base or {}
```

This is **grounding via tool result**, not Retrieval-Augmented Generation in the vector DB sense. Cheaper to build; relies on model actually calling the tool (prompt enforces this heavily).

---

## Escalation to human

### Tool: `escalate_to_human`

When the model invokes this tool, platform code can:

1. Send SMS alert to escalation phone
2. Return `{action: "transfer", destination: "+1..."}` to the proxy

Voice proxy special case:

```python
# llm_proxy.py:664–710
if fn_name == "escalate_to_human" and tool_result.get("action") == "transfer":
    # Emit Vapi/Bolna-compatible transferCall SSE
    yield _sse({"tool_calls": [{"function": {"name": "transferCall", ...}}]})
```

Also short-circuit if user explicitly asks for human early (`llm_proxy.py:225–260` region — explicit human request detection before full loop).

### When escalation tools appear

Only if tenant has escalation phone/guidance configured — filtered in tool list based on `has_escalation` in prompt builder (`agent_prompt.py:681–690`).

---

## End-to-end trace checklist

When debugging agent behavior:

1. Find session id in logs: `[Call chat-{uuid}-...]`
2. Check which path: `/api/chat/stream` vs `/api/llm/chat/completions`
3. Read tool rounds in logs: `Stream round N` / `[LLM Proxy] ── Streaming round N ──`
4. Verify tenant: chat uses JWT tenant; voice uses `resolve_default_tenant()`
5. Inspect tool result JSON in logs (truncated to 500 chars)
6. If wrong facts → did model skip `get_office_info`?
7. If wrong booking → trace `calendar_service` + Postgres row

Next: [05_frontend_deep_dive.md](./05_frontend_deep_dive.md)
