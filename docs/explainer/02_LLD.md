# 02 — Low-Level Design

## Entity-relationship diagram

Derived from SQLAlchemy models in `backend/models/`. Table names match `__tablename__`.

```mermaid
erDiagram
    tenants ||--o{ callers : has
    tenants ||--o{ calls : has
    tenants ||--o{ trainers : has
    tenants ||--o{ support_tickets : has
    tenants ||--o{ tenant_tools : has
    tenants ||--o{ profile_change_logs : has

    callers ||--o{ appointments : has
    callers ||--o{ waitlist_entries : has
    callers ||--o{ sms_messages : has

    calls ||--o{ appointments : "optional call_id"
    trainers ||--o{ appointments : "optional provider_id"
    trainers ||--o{ waitlist_entries : "optional provider_id"

    appointments ||--o{ appointment_status_history : has
    support_tickets ||--o{ support_ticket_messages : has

    tenants {
        uuid id PK
        string slug UK
        string business_name
        enum business_type
        string owner_email UK
        string password_hash
        boolean is_admin
        enum status
        enum plan
        boolean demo_mode
        jsonb appointment_types
        jsonb business_hours
        jsonb knowledge_base
        string twilio_phone_number
        string google_calendar_refresh_token
    }

    callers {
        uuid id PK
        uuid tenant_id FK
        string name
        string phone
        boolean is_test
        jsonb extra_data
    }

    trainers {
        uuid id PK
        uuid tenant_id FK
        string name
        jsonb appointment_types
        string calendar_id
        jsonb trial_session_slots
    }

    appointments {
        uuid id PK
        uuid caller_id FK
        uuid call_id FK
        uuid provider_id FK
        datetime scheduled_at
        enum status
        enum booked_via
        string cal_booking_uid
    }

    calls {
        uuid id PK
        uuid tenant_id FK
        string vapi_call_id UK
        string caller_number
        uuid caller_id FK
        enum outcome
        jsonb transcript
    }

    waitlist_entries {
        uuid id PK
        uuid caller_id FK
        uuid provider_id FK
        string appointment_type
        string preferred_date
        enum status
        int priority
    }

    sms_messages {
        uuid id PK
        uuid caller_id FK
        enum direction
        string body
        string twilio_sid
    }

    support_tickets {
        uuid id PK
        uuid tenant_id FK
        string subject
        enum status
    }

    tenant_tools {
        uuid id PK
        uuid tenant_id FK
        string name
        jsonb parameters_schema
        enum handler_type
    }

    platform_config {
        string key PK
        string value
    }
```

**Note:** `appointments` has no stored `tenant_id` column — tenant scope is via `caller_id → callers.tenant_id` (`backend/models/appointment.py:4–6`).

**Legacy naming:** `Call.vapi_call_id` stores Bolna `execution_id` too (`backend/routes/bolna.py:307`). `Trainer` is aliased as `Provider` in imports.

---

## API surface map

All routes registered in `backend/main.py:288–305`. Methods and paths taken from route decorators.

### App-level

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check + `demo_mode` flag (`main.py:308–315`) |
| GET | `/{full_path:path}` | Serve React SPA when `frontend/dist` exists (`main.py:325–331`) |

### `/api/auth` — `backend/routes/auth.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/auth/login` | Email/password → JWT |
| POST | `/api/auth/register` | Self-register studio (creates tenant) |
| GET | `/api/auth/me` | Current user profile |
| PATCH | `/api/auth/profile` | Update owner profile |
| POST | `/api/auth/change-password` | Change password |
| GET | `/api/auth/profile-changes` | Audit log of profile edits |

### `/api/tenants` — `backend/routes/tenants.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/tenants` | Onboard new studio (status PENDING) |
| GET | `/api/tenants` | List tenants (admin) |
| GET | `/api/tenants/usage` | Current tenant usage meters |
| GET | `/api/tenants/plans` | Plan tier definitions |
| GET | `/api/tenants/{tenant_id}` | Tenant detail |
| PUT | `/api/tenants/{tenant_id}` | Update tenant config |
| POST | `/api/tenants/{tenant_id}/approve` | PENDING → ACTIVE (admin) |
| POST | `/api/tenants/{tenant_id}/suspend` | ACTIVE → SUSPENDED |
| POST | `/api/tenants/{tenant_id}/reactivate` | Restore ACTIVE |
| DELETE | `/api/tenants/{tenant_id}` | Soft deactivate |
| DELETE | `/api/tenants/{tenant_id}/purge` | Hard delete all tenant data (admin) |

### `/api/chat` — `backend/routes/chat.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/chat/enabled` | Always `{enabled: true}` |
| POST | `/api/chat/reset` | Clear LLM session for conversation |
| POST | `/api/chat/stream` | SSE chat through tool loop (JWT) |

### `/api/llm` — `backend/routes/llm_proxy.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/llm/chat/completions` | OpenAI-compatible SSE for Bolna/voice |

### `/api/calls` — `backend/routes/calls.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/calls` | Paginated call log |
| GET | `/api/calls/export` | CSV export |
| GET | `/api/calls/{call_id}` | Call detail + transcript |

### `/api/appointments` — `backend/routes/appointments.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/appointments` | List sessions |
| POST | `/api/appointments/{id}/cancel` | Dashboard cancel + SMS + optional GCal |
| PATCH | `/api/appointments/{id}` | Reschedule / status / notes |
| GET | `/api/appointments/{id}/history` | Status audit trail |
| POST | `/api/appointments/sync-gcal` | Bidirectional Google Calendar sync |

### `/api/callers` — `backend/routes/callers.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/callers` | Member list |
| GET | `/api/callers/{id}` | Member profile + history |
| PUT | `/api/callers/{id}` | Update member |
| POST | `/api/callers/bulk-delete` | Bulk delete |
| DELETE | `/api/callers/test-data` | Clear test members |
| DELETE | `/api/callers/{id}` | Delete one member |

### `/api/trainers` and `/api/providers` — `backend/routes/providers.py`

Same router mounted twice (`main.py:296–297`).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/trainers` | List trainers |
| POST | `/api/trainers` | Create trainer |
| PUT | `/api/trainers/{provider_id}` | Update trainer |
| DELETE | `/api/trainers/{provider_id}` | Delete trainer |

### `/api/waitlist` — `backend/routes/waitlist.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/waitlist` | List waitlist entries |
| DELETE | `/api/waitlist/{entry_id}` | Remove entry |
| POST | `/api/waitlist/{entry_id}/check-conflicts` | Pre-promote conflict check |
| POST | `/api/waitlist/{entry_id}/promote` | Manually book from waitlist |

### `/api/sms` — `backend/routes/sms_messages.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/sms/conversations` | SMS inbox threads |
| GET | `/api/sms/messages` | Messages for a conversation |
| POST | `/api/sms/send` | Staff outbound SMS |

### SMS webhook — `backend/routes/sms_webhook.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/webhook/sms` | Twilio inbound → TwiML reply |

### Dashboard/config — `backend/routes/dashboard.py` (full paths on decorators)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/dashboard/stats` | KPI aggregates |
| GET/PUT | `/api/knowledge` | Read/write tenant KB JSON |
| GET/PUT | `/api/config` | Agent + integration settings |
| GET | `/api/voice-preview/{voice_id}` | TTS preview audio |
| POST/DELETE | `/api/config/test-phones` | Manage test caller phones |
| POST/DELETE | `/api/config/test-client-names` | Manage test names |
| POST/PUT/DELETE | `/api/config/test-callers` | Unified test caller CRUD |

### Google OAuth — `/api/integrations/google`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/integrations/google/connect` | Start OAuth |
| GET | `/api/integrations/google/callback` | OAuth callback |
| POST | `/api/integrations/google/disconnect` | Revoke connection |
| GET | `/api/integrations/google/status` | Connection status |

### Bolna — `backend/routes/bolna.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/bolna/call` | Trigger Bolna outbound call |
| GET | `/api/bolna/logs` | Fetch Bolna execution logs |
| POST | `/api/calls/outbound` | Outbound call alias |
| GET | `/api/bolna/caller-lookup` | Pre-call hook (Bolna → FitFront) |
| POST | `/api/bolna/webhook` | Call completion webhook |

### `/api/admin` — `backend/routes/platform_admin.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET/PUT | `/api/admin/platform/config` | Global Bolna keys etc. |
| GET | `/api/admin/platform/bolna/test` | Test Bolna connectivity |
| GET | `/api/admin/tenants/{id}` | Rich tenant admin view |
| GET | `/api/admin/tenants/{id}/usage` | Usage summary |
| POST | `/api/admin/tenants/{id}/integrations` | Assign Twilio / flags |

### Support tickets — `backend/routes/support_tickets.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/support/tickets` | Tenant creates ticket |
| GET | `/api/support/tickets` | Tenant lists own tickets |
| GET | `/api/support/tickets/{id}` | Ticket detail |
| POST | `/api/support/tickets/{id}/messages` | Tenant reply |
| POST | `/api/support/tickets/{id}/reopen` | Reopen resolved |
| GET | `/api/admin/support/tickets` | Admin list all |
| GET | `/api/admin/support/tickets/stats` | Ticket counts |
| GET/PATCH | `/api/admin/support/tickets/{id}` | Admin view/update |
| POST | `/api/admin/support/tickets/{id}/messages` | Admin reply |

### `/api/tenant-tools` — `backend/routes/tenant_tools.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tenant-tools` | List custom tools |
| POST | `/api/tenant-tools` | Create custom tool |
| PATCH | `/api/tenant-tools/{id}` | Update tool |
| DELETE | `/api/tenant-tools/{id}` | Delete tool |
| POST | `/api/tenant-tools/{id}/test` | Test tool handler |

---

## Sequence diagram 1 — Trial session booking via Test Front Desk chat

**Path:** Browser → `/api/chat/stream` → `llm_service.process_message_stream` → registry tools → Postgres.

```mermaid
sequenceDiagram
    actor User as Studio owner (browser)
    participant LC as LocalChat.jsx
    participant Chat as routes/chat.py
    participant LLM as llm_service.py
    participant Reg as tools/registry.py
    participant Plat as platform.py tools
    participant Cal as calendar_service.py
    participant DB as PostgreSQL

    User->>LC: Type "Book trial Tuesday 6pm"
    LC->>Chat: POST /api/chat/stream (JWT, conversation_id)
    Chat->>Chat: resolve_by_id(current_user.id)
    Chat->>LLM: create_session / process_message_stream
    loop Up to 5 tool rounds (llm_service.py:1079)
        LLM->>LLM: OpenAI/Ollama stream + tools
        LLM->>Reg: dispatch(get_available_slots, ...)
        Reg->>Plat: call_tool
        Plat->>Cal: get slots from DB
        Cal->>DB: SELECT appointments, trainers, hours
        DB-->>Plat: slot list
        Plat-->>LLM: tool result JSON
        LLM->>Reg: dispatch(book_appointment, ...)
        Reg->>Plat: call_tool
        Plat->>Cal: book_appointment
        Cal->>DB: INSERT appointment
        DB-->>Plat: booking_uid
        Plat-->>LLM: success + summary_for_assistant
    end
    LLM-->>Chat: async token generator
    Chat-->>LC: SSE chat.completion.chunk
    LC-->>User: Render streamed reply
```

**Trace yourself:**

| Step | File:line |
|------|-----------|
| SSE fetch | `frontend/src/components/LocalChat.jsx:778–790` |
| Stream endpoint | `backend/routes/chat.py:93–181` |
| Session key | `backend/routes/chat.py:187–195` |
| Tool loop | `backend/services/llm_service.py:1079–1248` |
| Tool dispatch | `backend/services/llm_service.py:1430–1438` |
| Registry | `backend/tools/registry.py:49–60` |

**Note:** In demo mode with a logged-in tenant, scheduling uses **native Postgres**, not fake slots (`calendar_service.py` — see integration doc). SMS confirmation may be simulated if `demo_mode` is on.

---

## Sequence diagram 2 — Waitlist auto-notification after SMS cancel

**Important:** This flow is triggered from the **SMS keyword cancel path**, not dashboard cancel.

```mermaid
sequenceDiagram
    actor Member as Member phone
    participant Twilio as Twilio
    participant WH as routes/sms_webhook.py
    participant In as sms_inbound_service.py
    participant WL as waitlist_service.py
    participant SMS as sms_service.py
    participant DB as PostgreSQL

    Member->>Twilio: SMS "CANCEL"
    Twilio->>WH: POST /webhook/sms (From, To, Body)
    WH->>In: handle_inbound_sms
    In->>In: resolve_tenant_by_phone(To)
    In->>DB: Find upcoming appointment, SET status=CANCELLED
    In->>SMS: send_cancellation
    SMS-->>Member: "Your appointment on ... cancelled"
    In->>WL: check_waitlist_for_opening(...)
    WL->>DB: SELECT WAITING entries (priority order)
    WL->>DB: UPDATE entry status=NOTIFIED
    WL->>SMS: _send_sms "Reply YES to book..."
    SMS-->>Member: Waitlist offer SMS (next person on list)
```

**Trace yourself:**

| Step | File:line |
|------|-----------|
| Webhook entry | `backend/routes/sms_webhook.py:21–22` |
| Cancel + waitlist trigger | `backend/services/sms_inbound_service.py:301–327` |
| Waitlist matching logic | `backend/services/waitlist_service.py:456–628` |
| YES acceptance | `backend/services/sms_inbound_service.py:346+` (NOTIFIED entry check) |

**Gap to know:** Dashboard cancel at `backend/routes/appointments.py:178–247` sends cancellation SMS and optional GCal cancel but **does not** call `check_waitlist_for_opening`.

---

## Sequence diagram 3 — Admin approves new tenant registration

```mermaid
sequenceDiagram
    actor Owner as Studio owner
    actor Admin as Platform admin
    participant FE as TenantRegister / TenantAdmin
    participant Auth as routes/auth.py
    participant Ten as routes/tenants.py
    participant TS as tenant_service.py
    participant DB as PostgreSQL

    Owner->>FE: Submit registration form
    FE->>Auth: POST /api/auth/register
    Auth->>TS: create_tenant (status=PENDING)
    TS->>DB: INSERT tenants
    Auth-->>FE: JWT + user (may redirect /pending)

    Admin->>FE: Open /admin/tenants
    FE->>Ten: GET /api/tenants (admin JWT)
    Ten-->>FE: List including PENDING

    Admin->>FE: Click Approve
    FE->>Ten: POST /api/tenants/{id}/approve
    Ten->>TS: approve_tenant(uid)
    TS->>DB: UPDATE status=ACTIVE
    Ten-->>FE: TenantOut ACTIVE

    Owner->>FE: Login again → /dashboard
```

**Trace yourself:**

| Step | File:line |
|------|-----------|
| Public onboard (alternate) | `backend/routes/tenants.py:163–179` |
| Register with password | `backend/routes/auth.py:186–187` |
| Approve endpoint | `backend/routes/tenants.py:287–312` |
| Pending UI | `frontend/src/components/PendingApproval.jsx` |
| Admin UI | `frontend/src/components/TenantAdmin.jsx` |

---

## Voice booking path (Bolna) — same tool loop, different entry

For phone calls, Bolna hits `/api/llm/chat/completions` instead of `/api/chat/stream`. The tool loop lives in `llm_proxy._stream_with_tools_sse` (`llm_proxy.py:521+`) with inline `_execute_tool` (`llm_proxy.py:1426+`) rather than the registry path. See [04_ai_agent_deep_dive.md](./04_ai_agent_deep_dive.md) and [06_voice_and_integrations.md](./06_voice_and_integrations.md).

Next: [03_backend_deep_dive.md](./03_backend_deep_dive.md)
