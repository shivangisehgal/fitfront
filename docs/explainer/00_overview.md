# 00 — Project Story

## Elevator pitch

**FitFront** is a multi-tenant AI front desk for boutique fitness studios. A studio owner registers, gets approved by a platform admin, and then runs an AI agent that answers phone calls (via Bolna), SMS threads (via Twilio), and a browser-based “Test Front Desk” chat — all sharing the same tool-calling brain that can look up slots, book sessions, manage waitlists, and escalate to humans. The React dashboard is the CRM: members, sessions, trainers, knowledge base, and agent settings.

## The problem it solves

Small gyms cannot staff a 24/7 receptionist. FitFront automates the repetitive work — “What are your hours?”, “Can I book a trial session Tuesday at 6?”, “Cancel my class” — while writing structured data (appointments, caller profiles, SMS logs) into Postgres for staff to manage.

## Major design decisions (and why)

| Decision | What the code does | Why (stated or inferred) |
|----------|---------------------|---------------------------|
| **Single deployment, many tenants** | One FastAPI app + one Postgres DB; rows scoped by `tenant_id` (`backend/models/tenant.py:41–44`) | **Inference:** Cheaper ops than per-client deploys; fits SaaS onboarding flow (`POST /api/tenants` → admin approve). |
| **JWT auth on the Tenant row** | Login returns Bearer token; `Tenant.is_admin` is the only role flag (`backend/services/auth_service.py:71–81`, `146–155`) | Stateless API suitable for SPA + webhooks; no server-side session store. |
| **Tool-calling agent, not a single LLM reply** | Up to 5 tool rounds per turn (`backend/services/llm_service.py:1079`, `backend/routes/llm_proxy.py:58`) | Booking requires side effects (DB writes, SMS, calendar) — a plain completion cannot do that reliably. |
| **Postgres-native scheduling + optional Google Calendar** | `calendar_service` books to DB first; GCal sync when tenant OAuth connected (`backend/services/calendar_service.py` — see integration doc) | Works in local demo without Google; GCal is an add-on per tenant. |
| **Demo mode flags** | Global `DEMO_MODE` + per-tenant `demo_mode` (`backend/config.py:70`, `backend/models/tenant.py:74`) | Lets developers run the full UI without Twilio/GCal credentials. |
| **OpenAI-compatible LLM wire format** | `/api/llm/chat/completions` speaks SSE chunks Bolna expects (`backend/routes/llm_proxy.py:82–89`) | Voice platforms already integrate with that shape; FitFront plugs in as a custom LLM backend. |

## If you only read one section

Read **[04_ai_agent_deep_dive.md](./04_ai_agent_deep_dive.md)** if you care about how the product actually *thinks* — the tool loop, system prompt, and session state are the core innovation. Then skim **[01_HLD.md](./01_HLD.md)** for where that brain sits in the wider system.

For a Java mental model: imagine a Spring Boot monolith where each “tenant” is a row in `tenants`, JWT carries `tenantId`, and a `@Service` class runs a while-loop calling Gemini/Ollama with `@Tool`-annotated methods until the model returns natural language — except here tools are OpenAI function schemas and FastAPI routes are the HTTP shell around it.

## Repo layout (30-second map)

```
fitfront/
├── backend/          FastAPI app (API + agent + integrations)
├── frontend/         React/Vite dashboard (:5173 dev)
├── docs/             Setup guides + this explainer/
├── tests/            pytest integration & flow tests
├── docker-compose.yml   Postgres only (port 5433→5432)
├── Dockerfile        Backend image for Railway
└── start.sh          Local backend + optional tunnel
```

Next: [01_HLD.md — High-Level Design](./01_HLD.md)
