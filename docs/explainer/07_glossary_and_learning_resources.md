# 07 — Glossary & Learning Resources

Legend:
- **🔄 Java/Spring** — you likely know this; Python/name differs
- **🆕 New territory** — agent/LLM/Python-specific

---

## Core concepts

### Tool calling / function calling 🆕

**Definition:** An LLM API feature where the model returns structured `{name, arguments}` instead of only text. Your code executes the function and feeds the result back as a `role: tool` message.

**Analogy:** Ordering at a drive-through with a menu code — the customer (LLM) says "number 4 with args `{date: Tuesday}`" and the kitchen (your Python code) prepares it.

| Level | Resource |
|-------|----------|
| Beginner | [OpenAI — Function calling guide](https://platform.openai.com/docs/guides/function-calling) |
| Intermediate | [Anthropic — Tool use concepts](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) |

**In FitFront:** `backend/tools/platform.py:66+`, loop at `llm_service.py:1079–1248`.

---

### Agentic loop / ReAct pattern 🆕

**Definition:** Repeat: model proposes action → environment executes → model observes result → until done. "Reason + Act."

**Analogy:** A doctor ordering tests, reading results, ordering more tests, then diagnosing — not guessing without labs.

| Level | Resource |
|-------|----------|
| Beginner | [Prompting Guide — ReAct intro](https://www.promptingguide.ai/techniques/react) |
| Intermediate | [LangChain — Tool calling agent architecture](https://python.langchain.com/docs/concepts/tool_calling/) |

**In FitFront:** Max 5 rounds (`llm_proxy.py:58`, `llm_service.py:1079`).

---

### ASGI 🆕

**Definition:** Async Server Gateway Interface — Python standard for async web apps. Uvicorn implements it; FastAPI runs on it.

**Analogy:** WSGI is a single-lane road; ASGI is a multi-lane highway where lanes (`await`) yield during I/O.

| Level | Resource |
|-------|----------|
| Beginner | [FastAPI — Async / await](https://fastapi.tiangolo.com/async/) |
| Intermediate | [ASGI specification](https://asgi.readthedocs.io/en/latest/) |

**In FitFront:** `uvicorn backend.main:app` in `Dockerfile:21`.

---

### FastAPI `Depends()` 🔄

**Definition:** Declares dependencies injected into route handlers — auth, DB sessions, config.

**Analogy:** Spring `@Autowired` constructor parameters, resolved per HTTP request.

| Level | Resource |
|-------|----------|
| Beginner | [FastAPI — Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) |
| Intermediate | [FastAPI — Advanced dependencies](https://fastapi.tiangolo.com/advanced/advanced-dependencies/) |

**In FitFront:** `auth_service.get_current_user` at `auth_service.py:99–143`.

---

### Pydantic models 🔄

**Definition:** Python classes validating/shaping JSON with type hints. FastAPI uses them for request/response bodies.

**Analogy:** Spring `@RequestBody` record/DTO + Jakarta Validation.

| Level | Resource |
|-------|----------|
| Beginner | [Pydantic — Models](https://docs.pydantic.dev/latest/concepts/models/) |
| Intermediate | [FastAPI — Body / response models](https://fastapi.tiangolo.com/tutorial/body/) |

**In FitFront:** `TenantOnboardRequest` at `tenants.py:42–52`.

---

### SQLAlchemy async ORM 🔄

**Definition:** Python ORM with async session support. Maps classes to SQL tables; queries use `select()` API.

**Analogy:** JPA/Hibernate EntityManager, but explicit async sessions and no lazy-loading magic in async context.

| Level | Resource |
|-------|----------|
| Beginner | [SQLAlchemy 2.0 — ORM quickstart](https://docs.sqlalchemy.org/en/20/tutorial/orm_data_manipulation.html) |
| Intermediate | [SQLAlchemy — Asyncio extension](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) |

**In FitFront:** `database.py:46–54`, models in `backend/models/`.

---

### JWT bearer auth 🔄

**Definition:** Stateless token signed with a secret; client sends `Authorization: Bearer <token>` each request.

**Analogy:** Same as Spring Security OAuth2 resource server JWT — `sub` claim maps to tenant UUID here.

| Level | Resource |
|-------|----------|
| Beginner | [JWT.io — Introduction](https://jwt.io/introduction) |
| Intermediate | [FastAPI — OAuth2 with Password (Bearer)](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) |

**In FitFront:** `auth_service.py:71–81`, frontend `api.js`.

---

### Multi-tenancy (row-level) 🔄

**Definition:** One app instance serves many customers; data rows tagged with `tenant_id` and filtered in queries.

**Analogy:** Discriminator column + manual `WHERE tenant_id = ?` — not separate databases per client.

| Level | Resource |
|-------|----------|
| Beginner | [AWS — SaaS tenant isolation patterns](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/tenant-isolation.html) |
| Intermediate | [Microsoft — Multitenancy architecture](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/overview) |

**In FitFront:** `tenant.py:41–44`, query patterns in `callers.py`, `appointments.py`. **Caveat:** voice uses default tenant — see README complexity flags.

---

### Webhooks 🔄

**Definition:** HTTP callbacks — external service POSTs to your URL when events happen (SMS received, call ended).

**Analogy:** Kafka consumer but pull-based HTTP push from vendor; you must return 200 quickly.

| Level | Resource |
|-------|----------|
| Beginner | [Twilio — Webhooks basics](https://www.twilio.com/docs/usage/webhooks) |
| Intermediate | [Stripe — Webhook best practices](https://docs.stripe.com/webhooks/best-practices) (general pattern) |

**In FitFront:** `/webhook/sms`, `/api/bolna/webhook`.

---

### OAuth2 (Google Calendar) 🔄

**Definition:** User grants your app access to their Google account; you store refresh token, call API on their behalf.

**Analogy:** Same OAuth2 authorization code flow as Spring Security OAuth2 Client.

| Level | Resource |
|-------|----------|
| Beginner | [Google — OAuth 2.0 for Web Server Apps](https://developers.google.com/identity/protocols/oauth2/web-server) |
| Intermediate | [Google Calendar API — Python quickstart](https://developers.google.com/calendar/api/quickstart/python) |

**In FitFront:** `google_oauth.py`, tokens on `Tenant` row.

---

### Server-Sent Events (SSE) 🆕

**Definition:** One-way HTTP stream: server pushes `data: ...\n\n` lines. Used for LLM token streaming.

**Analogy:** Kafka topic with one subscriber, over HTTP — simpler than WebSockets for read-only streams.

| Level | Resource |
|-------|----------|
| Beginner | [MDN — Server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) |
| Intermediate | [OpenAI — Streaming chat completions](https://platform.openai.com/docs/api-reference/chat/streaming) |

**In FitFront:** `chat.py` stream, `llm_proxy.py` SSE wrappers.

---

### System prompt 🆕

**Definition:** Initial `role: system` message instructing model persona, rules, and tool-use policy.

**Analogy:** Operating procedures manual given to a new employee before they take calls.

| Level | Resource |
|-------|----------|
| Beginner | [OpenAI — Prompt engineering guide](https://platform.openai.com/docs/guides/prompt-engineering) |
| Intermediate | [Anthropic — Prompt design overview](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview) |

**In FitFront:** `agent_prompt.py:611+`.

---

### Knowledge grounding (tool-based) 🆕

**Definition:** Providing factual answers from a structured store at query time, rather than from model memory.

**Analogy:** Cashier must scan barcode (call tool) instead of memorizing prices.

**Not vector RAG in FitFront** — no embeddings index in repo. Uses JSONB + `get_office_info` tool.

| Level | Resource |
|-------|----------|
| Beginner | [OpenAI — Retrieval guide (concepts)](https://platform.openai.com/docs/guides/retrieval) |
| Intermediate | [LlamaIndex — RAG overview](https://docs.llamaindex.ai/en/stable/getting_started/concepts/) |

**In FitFront:** `knowledge_service.py`, `Tenant.knowledge_base`.

---

### Strategy / Adapter pattern (LLM providers) 🔄

**Definition:** Swap Ollama vs Gemini behind common calling convention based on config.

**Analogy:** `PaymentProcessor` interface with `StripeProcessor` and `PayPalProcessor` implementations.

| Level | Resource |
|-------|----------|
| Beginner | [Refactoring Guru — Strategy pattern](https://refactoring.guru/design-patterns/strategy) |
| Intermediate | [Google Gen AI SDK — Python](https://googleapis.github.io/python-genai/) |

**In FitFront:** `LLM_PROVIDER` in `config.py`, branches in `llm_service.py` and `llm_proxy.py`.

---

## Mermaid diagram types used

| Type | Used for | Doc |
|------|----------|-----|
| `flowchart` | Architecture, deployment | `01_HLD.md` |
| `sequenceDiagram` | Request flows | `02_LLD.md`, `06_voice_and_integrations.md` |
| `erDiagram` | Database schema | `02_LLD.md` |

| Level | Resource |
|-------|----------|
| Beginner | [Mermaid — Live editor](https://mermaid.live/) |
| Intermediate | [Mermaid — Sequence diagram syntax](https://mermaid.js.org/syntax/sequenceDiagram.html) |

---

## Suggested learning path (for your background)

1. Read `04_ai_agent_deep_dive.md` with OpenAI function calling docs open
2. Run local demo: `./start.sh` + `npm run dev`, use `/chat` to book a session
3. Set breakpoints / logs on `llm_service._execute_tool` and watch tool rounds
4. Trace one SMS path in `sms_inbound_service.py`
5. Skim `test_conversation_flows.py` for expected agent behaviors
6. Attempt full stack per `docs/FULL_STACK_SETUP.md` when ready for Bolna

---

[← Back to index](./README.md)
