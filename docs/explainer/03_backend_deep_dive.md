# 03 — Backend & Python/FastAPI Deep Dive

## Directory tour

```
backend/
├── main.py              App factory, lifespan, router mounts, static SPA
├── config.py            Settings from env vars (Pydantic-style dataclass pattern)
├── database.py          Async SQLAlchemy engine, create_all, inline migrations
├── defaults.py          Shared constants (timezone, greeting defaults)
├── models/              SQLAlchemy ORM tables (13 entities)
├── routes/              FastAPI routers — thin HTTP layer
├── services/            Business logic + external API clients
├── tools/               LLM tool schemas + dispatch (platform, fitness, custom)
├── prompts/             System prompt builder
└── knowledge/           default_kb.json fallback file
```

**Convention:** Routes validate auth, parse Pydantic bodies, call services, return JSON. Heavy logic stays in `services/` or `tools/`. This mirrors a typical Spring `@RestController` → `@Service` split.

---

## FastAPI concepts (from this repo)

### ASGI vs WSGI (why it matters here)

FastAPI runs on **ASGI** (Asynchronous Server Gateway Interface). Uvicorn is the server (`Dockerfile:21`, `main.py:347–352`).

- **WSGI** (Flask, Django sync): one request = one thread, blocking I/O.
- **ASGI** (FastAPI): `async def` endpoints can `await` DB and HTTP without blocking the event loop.

FitFront uses async SQLAlchemy sessions and `httpx`/`AsyncOpenAI` for LLM streaming — natural fit for ASGI.

Spring analogue: similar to WebFlux reactive stack, except Python's async is cooperative single-thread event loop per worker, not reactive streams.

### Application lifespan

```python
# backend/main.py:118–119
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks before the server accepts requests."""
```

On startup: connect DB, run migrations, seed admin, load KB, start reminder background task (`main.py:118–210`). Spring analogue: `@PostConstruct` or `ApplicationRunner`, but scoped to the ASGI app lifecycle.

### Routers

Each feature file exports `router = APIRouter(prefix=...)` and is included in `main.py`:

```python
# backend/main.py:288–305
app.include_router(auth_router)
app.include_router(chat_router)
# ...
app.include_router(providers_router, prefix="/api/trainers")
app.include_router(providers_router, prefix="/api/providers")  # alias mount
```

Same router mounted at two prefixes — useful for backward-compatible API names.

### Dependency injection with `Depends`

FastAPI resolves dependencies per request:

```python
# backend/services/auth_service.py:99–101
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tenant:
```

Used in routes as:

```python
# backend/routes/appointments.py:182
current_user: Tenant = Depends(auth_service.get_current_user),
```

| FastAPI | Spring Boot |
|---------|-------------|
| `Depends(get_current_user)` | Constructor/`@Autowired` security principal |
| `Depends(get_db)` | `@Transactional` EntityManager injection |
| `Depends(require_admin)` | `@PreAuthorize("hasRole('ADMIN')")` |

Dependencies can depend on other dependencies — FastAPI builds a DAG per request.

### Pydantic request/response models

```python
# backend/routes/tenants.py:42–52
class TenantOnboardRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9_-]+$")
    business_name: str = Field(..., min_length=2, max_length=255)
    # ...
```

FastAPI validates JSON bodies before your handler runs. Invalid payloads → 422 with field errors.

| Pydantic | Spring |
|----------|--------|
| `BaseModel` + `Field(...)` | `@RequestBody` DTO + `@Valid` + Bean Validation |
| `response_model=TenantOut` | Return type + Jackson serialization |

### Async route handlers

Most I/O-bound routes are `async def`:

```python
# backend/routes/chat.py:93–94
@router.post("/stream")
async def chat_stream(...):
```

They `await` service calls and DB. **Do not** call blocking code inside async routes without `run_in_executor` — it stalls the event loop. FitFront's LLM sync client paths are mostly isolated in services with async wrappers for streaming.

### Middleware

```python
# backend/main.py — CORSMiddleware + log_requests HTTP middleware
```

No tenant middleware — tenancy is explicit in handlers (see HLD doc).

### Error handling

Routes raise `HTTPException`:

```python
# backend/routes/appointments.py:193
raise HTTPException(status_code=404, detail="Appointment not found.")
```

Global exception handlers are minimal — mostly per-route 4xx/5xx. Spring analogue: `@ControllerAdvice` but lighter.

### Streaming responses

Chat and LLM proxy return `StreamingResponse` with SSE:

```python
# backend/routes/chat.py — returns StreamingResponse wrapping async generator
```

The client reads `text/event-stream` incrementally — same pattern as Server-Sent Events in Spring WebFlux `Flux<ServerSentEvent>`.

---

## Database layer

### Stack

- **SQLAlchemy 2.x async** — `create_async_engine` + `async_sessionmaker` (`database.py:46–54`)
- **Driver:** `asyncpg` via `postgresql+asyncpg://...` URL
- **No Alembic** — schema changes are raw SQL in `_MIGRATIONS` list (`database.py:69+`), applied at startup after `create_all`

Spring analogue: Flyway/Liquibase scripts, except here they're Python strings executed with `IF NOT EXISTS`.

### Session lifecycle

```python
# backend/database.py:54
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

Typical usage in routes:

```python
# backend/routes/appointments.py:181
db: AsyncSession = Depends(get_db),
```

`get_db` yields a session, commits/rolls back on exit. Services often open their own `async with async_session()` for standalone operations (`waitlist_service.py:527`).

### Models

Declarative base:

```python
# backend/database.py:59–61
class Base(DeclarativeBase):
    pass
```

Example entity:

```python
# backend/models/caller.py:19–26
class Caller(Base):
    __tablename__ = "callers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_caller_tenant_phone"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), ...)
```

Relationships use `relationship()` sparingly; many queries use explicit `select()` for async clarity.

---

## Auth & roles

### Model

There is one user table: **`tenants`**. Platform admin is `Tenant.is_admin == True` (`backend/models/tenant.py:67`). Studio owners are normal tenants with `is_admin=False`.

### Login flow

```python
# backend/routes/auth.py:144–145
@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
```

1. Look up tenant by `owner_email`
2. `verify_password` (bcrypt)
3. `create_access_token(tenant.id, email, is_admin)`
4. Return `{access_token, user}`

JWT payload (`auth_service.py:74–80`): `sub`, `email`, `is_admin`, `exp`, `iat`.

### Account states

`TenantStatus`: PENDING, APPROVED, ACTIVE, SUSPENDED, DEACTIVATED (`tenant.py:19–25`).

- Registration → PENDING (or ACTIVE after approve)
- `get_current_user` blocks SUSPENDED/DEACTIVATED for non-admins (`auth_service.py:134–141`)
- Frontend redirects PENDING users to `/pending` (`App.jsx`)

### Three request personas

| Persona | Auth | Scoping |
|---------|------|---------|
| **Platform admin** | JWT + `require_admin` | Cross-tenant read/write |
| **Studio owner** | JWT + `get_current_user` | Own `tenant_id` only |
| **Webhooks (Twilio/Bolna/LLM)** | No JWT | Tenant resolved by phone or default tenant |

---

## Key services (what to read next)

| Service | Responsibility |
|---------|----------------|
| `tenant_service.py` | Resolve tenant → `TenantContext`, CRUD, cache invalidation |
| `llm_service.py` | Session store, tool loop, Ollama/Gemini clients |
| `calendar_service.py` | Slot computation, booking, native scheduling |
| `caller_service.py` | Member upsert, phone normalization |
| `sms_service.py` | Outbound Twilio + demo simulation |
| `sms_inbound_service.py` | Inbound routing, keywords, agentic SMS |
| `waitlist_service.py` | Waitlist CRUD + cancel fill-in notifications |
| `knowledge_service.py` | KB load/format (not vector search) |
| `reminder_service.py` | Background SMS reminders |
| `bolna_service.py` | Outbound voice via Bolna REST API |

---

## Config

`backend/config.py` reads environment variables into a settings object:

```python
# backend/config.py:70–90 (representative)
DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"
LOCAL_CHAT_MODE: bool = os.getenv("LOCAL_CHAT_MODE", "false").lower() == "true"
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
```

Spring analogue: `@ConfigurationProperties` bound from `application.yml`.

---

## Tests

`tests/` uses pytest with async support (`pytest.ini`). Notable files:

- `test_multi_tenant.py` — tenant isolation assertions
- `test_conversation_flows.py` — agent dialogue scenarios
- `test_integration_scheduling.py` — booking edge cases

Tests are the fastest way to see expected agent behavior without running voice.

Next: [04_ai_agent_deep_dive.md](./04_ai_agent_deep_dive.md)
