# Full-stack local setup

Complete guide to running FitFront locally with **real** Google Calendar sync, Twilio SMS, and Bolna voice calls — not just the browser chat demo.

For a quick chat-only demo, use the defaults in `.env.example` (`DEMO_MODE=true`, `LOCAL_CHAT_MODE=true`).

---

## What “full stack” means

| Layer | Local demo (default) | Full stack |
|-------|----------------------|------------|
| Database + API + UI | ✅ | ✅ |
| AI via Test Front Desk (`/chat`) | ✅ | ✅ (or voice instead) |
| Simulated SMS / calendar | ✅ (`DEMO_MODE=true`) | ❌ |
| Real Google Calendar events | ❌ | ✅ |
| Real Twilio SMS | ❌ | ✅ |
| Real phone calls (Bolna) | ❌ | ✅ |

---

## Architecture

```
Phone call  → Bolna AI  → {SERVER_BASE_URL}/api/llm        → LLM + tools → Postgres
SMS inbound → Twilio    → {SERVER_BASE_URL}/webhook/sms    → AI reply
Call ended  → Bolna     → {SERVER_BASE_URL}/api/bolna/webhook
Pre-call    → Bolna     → {SERVER_BASE_URL}/api/bolna/caller-lookup
Calendar    → Google OAuth (per studio) → events synced after booking
Dashboard   → http://localhost:5173 → FastAPI on port 8000
```

**Note:** Per-tenant Vapi configuration was removed from the platform. Voice is configured via **Bolna AI** (Admin → Platform Settings). The `/api/llm` endpoint still uses the OpenAI-compatible wire format that Bolna (and Vapi, if self-wired) expect.

---

## Prerequisites

Install on your machine:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Python 3.11+ and Node.js 18+
- Accounts (free tiers OK for testing):
  - [Twilio](https://www.twilio.com) — SMS
  - [Google Cloud Console](https://console.cloud.google.com) — Calendar OAuth
  - [Bolna](https://bolna.ai) — voice calls
  - [Gemini API key](https://aistudio.google.com/apikey) — recommended for voice (Bolna cannot reach local Ollama)

---

## Step 1 — Base infrastructure

```bash
git clone https://github.com/shivangisehgal/fitfront.git
cd fitfront

cp .env.example .env
# Edit .env — see Step 2

docker compose up -d postgres

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 seed_data.py
```

**Database port:** `docker-compose.yml` maps Postgres to host port **5433**. Your `.env` must use:

```env
DATABASE_URL=postgresql+asyncpg://scheduler_user:scheduler_pass@localhost:5433/scheduler_ai
```

Verify Postgres is up:

```bash
docker compose ps
```

---

## Step 2 — Configure `.env` for live mode

Minimum changes from the demo defaults:

```env
DEMO_MODE=false
LOCAL_CHAT_MODE=false

LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.0-flash

TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=your-token
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX

GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
GOOGLE_REDIRECT_URI=http://localhost:8000/api/integrations/google/callback

FEATURE_TWILIO_ENABLED=true
```

`SERVER_BASE_URL` will be set automatically by `./start.sh` when you enable the tunnel (Step 5).

See the commented **FULL STACK** block at the bottom of `.env.example` for the complete list.

---

## Step 3 — Google Calendar

### Google Cloud Console

1. Create a project.
2. Enable **Google Calendar API**.
3. Configure **OAuth consent screen** (External) — add your Google account as a test user.
4. Create **OAuth client ID** → Web application.
5. Add authorized redirect URI:
   ```
   http://localhost:8000/api/integrations/google/callback
   ```
6. Copy Client ID and Client Secret into `.env`.

### Connect in the app

1. Start backend + frontend (Step 6).
2. Register a studio at http://localhost:5173/register
3. Log in as admin (`ADMIN_EMAIL` / `ADMIN_PASSWORD`) → approve the tenant.
4. Log in as studio owner → **AI Agent** → **Connect Google Calendar**.
5. Complete OAuth — status should show connected.

Book a session via chat or voice — a Google Calendar event should appear.

---

## Step 4 — Twilio SMS

### Twilio console

1. Create account / use trial.
2. Buy or use a trial phone number with SMS capability.
3. Copy **Account SID**, **Auth Token**, and phone number into `.env`.

### Public webhook URL

Twilio must reach your local machine. Use the project startup script:

```bash
./start.sh
```

This starts **localtunnel**, updates `SERVER_BASE_URL` in `.env`, and runs the backend.

In Twilio → Phone Numbers → your number → **Messaging**:

| Field | Value |
|-------|-------|
| Webhook URL | `{SERVER_BASE_URL}/webhook/sms` |
| Method | POST |

Example: `https://random-name.loca.lt/webhook/sms`

### Assign number to a studio (admin)

1. Admin → **Manage Tenants** → open the studio.
2. **Integrations & Flags** tab.
3. Set **Twilio Phone Number** (e.g. `+15551234000`).
4. Enable **SMS** feature flag and **Agent Status** → Live → Save.

Inbound SMS to that number routes to the correct studio by matching the `To` field.

### Verify

- Book a session — member receives confirmation SMS.
- Reply **C** (confirm), **R** (reschedule), or **X** (cancel).

---

## Step 5 — Bolna voice

### Bolna dashboard

1. Create a voice agent.
2. Configure **Custom LLM** (OpenAI-compatible):
   ```
   https://YOUR-TUNNEL-URL/api/llm
   ```
   Do **not** append `/chat/completions` — the platform adds it.

3. **Inbound pre-call hook** (Internal API):
   ```
   GET https://YOUR-TUNNEL-URL/api/bolna/caller-lookup?contact_number={{contact_number}}&agent_id={{agent_id}}&execution_id={{execution_id}}
   ```

4. **Call completion webhook**:
   ```
   POST https://YOUR-TUNNEL-URL/api/bolna/webhook
   ```

### FitFront admin

1. Admin → **Manage Tenants** → **Platform Settings**.
2. Enter **Bolna API Key** and **Agent ID** → Save → **Test Connection**.

### Start with tunnel

```bash
# Ensure LOCAL_CHAT_MODE=false in .env
./start.sh
```

Keep this terminal open. The script prints the tunnel URL and warms the LLM if using Ollama.

**LLM note:** If you use Ollama on localhost, Bolna's cloud cannot reach it. Use **Gemini** (`LLM_PROVIDER=gemini`) for real phone calls, or deploy backend + Ollama on a public server.

---

## Step 6 — Run everything

**Terminal 1 — backend with tunnel (voice + SMS webhooks):**

```bash
source venv/bin/activate
./start.sh
```

**Terminal 2 — frontend:**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173

---

## Step 7 — First-time app flow

1. **Register** a studio (http://localhost:5173/register).
2. **Admin login** (incognito window) → approve tenant → assign Twilio number → configure Bolna in Platform Settings.
3. **Studio owner login** → complete Setup Guide → connect Google Calendar → configure Studio Info + trainers.
4. **Test chat:** Test Front Desk → book a trial session → check Sessions + Members.
5. **Test voice:** call via Bolna → verify booking in dashboard.
6. **Test SMS:** confirm text received; reply **C**.

---

## Demo seed data caveat

`python3 seed_data.py` creates **Iron & Ivy Fitness Studio** with `demo_mode=true`, which keeps SMS/calendar simulated even when global `DEMO_MODE=false`.

For full-stack testing, either:

- **Register a fresh studio** (recommended), or
- Disable demo on the seed tenant:

```sql
UPDATE tenants SET demo_mode = false WHERE slug = 'iron-and-ivy-fitness';
```

The seed tenant also has **no login password** — use registration + admin approval for a studio account you can log into.

---

## Webhook URL reference

Replace `{BASE}` with your `SERVER_BASE_URL` (public https URL):

| Service | URL |
|---------|-----|
| Twilio SMS inbound | `{BASE}/webhook/sms` |
| Bolna custom LLM | `{BASE}/api/llm` |
| Bolna caller lookup | `{BASE}/api/bolna/caller-lookup` |
| Bolna call webhook | `{BASE}/api/bolna/webhook` |
| Google OAuth callback | `http://localhost:8000/api/integrations/google/callback` |
| Health check | `{BASE}/health` |
| API docs | `{BASE}/docs` |

---

## End-to-end verification checklist

- [ ] `curl http://localhost:8000/health` returns OK
- [ ] Studio registered and approved by admin
- [ ] Google Calendar connected (studio owner)
- [ ] Twilio number assigned to tenant (admin)
- [ ] Bolna credentials saved + connection test passes (admin)
- [ ] `./start.sh` running with public tunnel URL
- [ ] Bolna agent LLM URL points to `{BASE}/api/llm`
- [ ] Twilio messaging webhook points to `{BASE}/webhook/sms`
- [ ] Chat booking creates session in dashboard
- [ ] Google Calendar shows the event
- [ ] SMS confirmation received on real phone
- [ ] Voice call completes a booking (optional)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connect call failed` on port 5432 | Use port **5433** in `DATABASE_URL` when using Docker |
| SMS not sending | `DEMO_MODE=false`, tenant `demo_mode=false`, Twilio creds set, `LOCAL_CHAT_MODE=false` |
| Twilio webhook never fires | `SERVER_BASE_URL` must be public https — run `./start.sh` |
| Calendar connect button greyed out | Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`, restart backend |
| Bolna call connects but AI silent | LLM unreachable — use Gemini instead of local Ollama |
| Inbound SMS wrong studio | Admin must set `twilio_phone_number` on the correct tenant |
| Stuck on Awaiting Approval | Log in as admin and approve the tenant |

---

## Daily startup (full stack)

```bash
docker compose up -d postgres
source venv/bin/activate
./start.sh                    # terminal 1 — tunnel + backend
cd frontend && npm run dev    # terminal 2
```
