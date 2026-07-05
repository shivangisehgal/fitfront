# FitFront Codebase Explainer

A teaching-oriented architecture walkthrough of the FitFront repository. Every diagram and code reference was verified against the actual source in `/Users/shivangisehgal/Desktop/Projects/fitfront/`.

**Audience:** Intermediate backend engineer (Java/Spring/Kafka background), new to Python/FastAPI and LLM tool-calling in production.

## Reading order

| # | File | What you'll learn |
|---|------|-------------------|
| 1 | [00_overview.md](./00_overview.md) | What FitFront is, why it exists, one-page summary |
| 2 | [01_HLD.md](./01_HLD.md) | System architecture, deployment, multi-tenancy |
| 3 | [02_LLD.md](./02_LLD.md) | ER diagram, full API map, sequence diagrams |
| 4 | [03_backend_deep_dive.md](./03_backend_deep_dive.md) | Python/FastAPI patterns, backend module tour |
| 5 | [04_ai_agent_deep_dive.md](./04_ai_agent_deep_dive.md) | Tool-calling loop, prompts, LLM providers, sessions |
| 6 | [05_frontend_deep_dive.md](./05_frontend_deep_dive.md) | React/Vite dashboard, auth, LocalChat vs CRM |
| 7 | [06_voice_and_integrations.md](./06_voice_and_integrations.md) | Bolna, Twilio, Google Calendar, demo modes |
| 8 | [07_glossary_and_learning_resources.md](./07_glossary_and_learning_resources.md) | Concepts glossary + curated links |

## Files read during authoring

These paths were opened to build this series:

**Root:** `README.md`, `.env.example`, `docker-compose.yml`, `Dockerfile`, `railway.json`, `start.sh`, `requirements.txt`, `seed_data.py`

**Backend core:** `backend/main.py`, `backend/config.py`, `backend/database.py`, `backend/defaults.py`

**Models:** `backend/models/tenant.py`, `caller.py`, `appointment.py`, `provider.py`, `call.py`, `waitlist.py`, `sms_message.py`, `support_ticket.py`, `tenant_tool.py`, `platform_config.py`

**Routes (all 17):** `auth.py`, `tenants.py`, `chat.py`, `llm_proxy.py`, `dashboard.py`, `appointments.py`, `calls.py`, `callers.py`, `providers.py`, `waitlist.py`, `sms_webhook.py`, `sms_messages.py`, `google_oauth.py`, `bolna.py`, `platform_admin.py`, `support_tickets.py`, `tenant_tools.py`

**Services:** `auth_service.py`, `tenant_service.py`, `llm_service.py`, `knowledge_service.py`, `calendar_service.py`, `sms_service.py`, `sms_inbound_service.py`, `waitlist_service.py`, `bolna_service.py`, `google_calendar.py`, `reminder_service.py`

**AI/tools:** `backend/prompts/agent_prompt.py`, `backend/tools/registry.py`, `backend/tools/platform.py`, `backend/tools/fitness.py`

**Frontend:** `frontend/vite.config.js`, `frontend/package.json`, `frontend/src/main.jsx`, `App.jsx`, `contexts/AuthContext.jsx`, `lib/api.js`, `components/LocalChat.jsx`, `CallerCRM.jsx`, `Dashboard.jsx`, `TenantAdmin.jsx`

**Docs:** `docs/FULL_STACK_SETUP.md`

## Complexity flags (read these first if you're in a hurry)

1. **Two parallel tool-execution paths:** `llm_service._execute_tool` delegates to `tools/registry.py`; `llm_proxy._execute_tool` is a large inline duplicate (`backend/routes/llm_proxy.py:1426+`). Voice and chat do not share identical dispatch code today.
2. **Voice tenant resolution is weak for true multi-tenant:** Bolna/LLM proxy paths call `resolve_default_tenant()` (first ACTIVE tenant), not per-phone routing (`backend/routes/llm_proxy.py:102`, `backend/routes/bolna.py:275`).
3. **Waitlist auto-SMS on cancel** runs from the **SMS cancel fast-path** only (`backend/services/sms_inbound_service.py:318–327`), not from dashboard cancel (`backend/routes/appointments.py:178–247`).
4. **No vector RAG:** Knowledge grounding is JSONB + `get_office_info` tool lookup, not embeddings search.
5. **No Alembic:** Schema evolves via inline `ALTER TABLE` statements in `backend/database.py:69+`.
