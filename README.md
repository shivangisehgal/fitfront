# FitFront — AI Front Desk for Boutique Gyms

Multi-tenant AI voice front desk for boutique gyms and fitness studios. Each tenant gets an AI agent that answers calls, books trial sessions and classes, manages waitlists, sends SMS confirmations, syncs with Google Calendar, and escalates to human staff when needed.

**Currently built for:** fitness studios (single vertical). One deployment serves many isolated gym clients — each with its own trainers, hours, pricing, and knowledge base in Postgres — without per-tenant code changes.

## Architecture

```
Caller → Bolna (voice) / Test Front Desk (chat) → FastAPI /api/llm
              ↓
         Ollama / Gemini LLM + tool-calling loop
              ↓
    Postgres (tenants, trainers, appointments, callers)
              ↓
    Google Calendar · Twilio SMS · Review solicitation
```

## Features

- **Voice AI front desk** — natural booking, rescheduling, cancellation, and FAQ handling
- **Trainer-aware scheduling** — slot capacity, trial session windows, per-trainer calendars
- **Class waitlist** — auto-notify when a full class opens up
- **SMS** — confirmations, reminders, membership info, review requests
- **Multi-tenant admin** — dashboard, CRM, knowledge base, trainer management
- **Local chat mode** — test the agent without a phone call via `/chat`

## Quick start (local demo)

Chat-only demo — no Twilio, Google Calendar, or phone setup required.

```bash
git clone https://github.com/shivangisehgal/fitfront.git
cd fitfront

cp .env.example .env
# Defaults work out of the box: DEMO_MODE=true, LOCAL_CHAT_MODE=true, Ollama LLM

docker compose up -d postgres

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 seed_data.py

python3 -m uvicorn backend.main:app --reload --port 8000
```

Frontend (separate terminal):

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173

**First login flow:**

1. Register a studio at `/register`
2. Log in as admin (`ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`) → approve the tenant
3. Log in as studio owner → use **Test Front Desk** (`/chat`) to book a session

**LLM:** Install [Ollama](https://ollama.com) and run `ollama pull qwen3:8b`, or set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` in `.env`.

**Database:** Docker exposes Postgres on host port **5433** (not 5432). This is already set in `.env.example`.

## Full stack setup

For real Google Calendar sync, Twilio SMS, and Bolna voice calls:

→ **[docs/FULL_STACK_SETUP.md](docs/FULL_STACK_SETUP.md)**

Summary: set `DEMO_MODE=false`, configure Twilio + Google OAuth + Bolna in `.env`, run `./start.sh` for a public tunnel, assign a Twilio number per tenant in the admin panel.

## Demo seed data

`python3 seed_data.py` creates **Iron & Ivy Fitness Studio** with 3 trainers, sample clients, calls, and appointments. The seed tenant has no login password — register your own studio to log in.

## Tests

```bash
pytest -q
```

Integration tests (`test_integration_scheduling.py`, `test_conversation_flows.py`) require a running server and LLM backend — see file headers for prerequisites.

## Configuration

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection — use port **5433** with Docker |
| `DEMO_MODE` | `true` = simulated SMS/calendar; `false` = live APIs |
| `LOCAL_CHAT_MODE` | `true` = browser chat only; `false` = use tunnel for voice/SMS |
| `LLM_PROVIDER` | `ollama` (local) or `gemini` (cloud — needed for Bolna voice) |
| `OLLAMA_*` / `GEMINI_*` | LLM credentials |
| `TWILIO_*` | Platform SMS credentials |
| `GOOGLE_*` | Calendar OAuth (platform-wide) |
| `BOLNA_*` | Voice calling (or set via admin Platform Settings) |
| `SERVER_BASE_URL` | Public URL for webhooks — set by `./start.sh` |

See `.env.example` for the full list including a commented full-stack block.

## License

MIT — see [LICENSE](LICENSE).
