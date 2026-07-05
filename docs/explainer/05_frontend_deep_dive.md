# 05 — Frontend Deep Dive

## Stack

| Piece | Technology | Config |
|-------|------------|--------|
| UI library | React 18 | `frontend/package.json` |
| Routing | React Router 6 | `frontend/src/main.jsx`, `App.jsx` |
| Build | Vite 6 | `frontend/vite.config.js` |
| Styling | Tailwind CSS (class dark mode) | `tailwind.config.js` |
| Charts | Recharts | Used in `Dashboard.jsx` |
| Icons | Lucide | Throughout components |

---

## Folder structure

```
frontend/src/
├── main.jsx              Providers: Router, Auth, Theme, Modal
├── App.jsx               Public routes + AppShell (sidebar layout)
├── index.css             Tailwind imports
├── lib/
│   ├── api.js            HTTP client + JWT storage
│   ├── tenantLabels.js   Status/plan label maps
│   ├── timezone.js       Display formatting
│   └── timezones.js      IANA list for registration
├── contexts/
│   ├── AuthContext.jsx   Login state, token lifecycle
│   ├── ThemeContext.jsx  Light/dark toggle
│   └── ModalContext.jsx  Toasts, confirm dialogs
└── components/
    ├── Landing.jsx           Public homepage /
    ├── Login.jsx             /login
    ├── TenantRegister.jsx    /register
    ├── PendingApproval.jsx   /pending (PENDING tenants)
    ├── ProtectedRoute.jsx    Auth guards
    ├── Dashboard.jsx         KPI dashboard
    ├── LocalChat.jsx         Test Front Desk /chat
    ├── CallerCRM.jsx         Members /contacts
    ├── AppointmentManager.jsx  Sessions /appointments
    ├── TrainerManager.jsx    /trainers
    ├── WaitlistView.jsx      /waitlist
    ├── SMSConversations.jsx  /sms
    ├── KnowledgeBase.jsx     /knowledge
    ├── AgentConfig.jsx       /settings
    ├── SetupGuide.jsx        /setup
    ├── SupportTickets.jsx    /support
    ├── Profile.jsx           /profile
    ├── TenantAdmin.jsx       Admin /admin/tenants
    ├── TenantDetail.jsx      Admin tenant detail
    ├── TicketDetail.jsx      Admin ticket thread
    └── ui/                   Shared inputs, date pickers, etc.
```

---

## How the frontend talks to the backend

### API client — `lib/api.js`

```javascript
// frontend/src/lib/api.js:6–11
const TOKEN_KEY = 'scheduler_ai_token';
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
```

| Environment | `API_BASE` | Effect |
|-------------|------------|--------|
| Local dev | `''` (empty) | Relative URLs → Vite proxy → `:8000` |
| Production | `VITE_API_BASE_URL` | Absolute URL to deployed API |

Vite proxy (`vite.config.js:8–12`):

```javascript
proxy: {
  '/api': 'http://localhost:8000',
  '/webhook': 'http://localhost:8000',
  '/health': 'http://localhost:8000',
},
```

### Authenticated fetch

```javascript
// api.js — apiFetch attaches Bearer token, JSON body, handles 401
if (token && !headers.Authorization) {
  headers.Authorization = `Bearer ${token}`;
}
```

On 401: clears token; `ProtectedRoute` sends user to login.

Spring analogue: Axios interceptor adding `Authorization` header from localStorage.

### Auth context

```javascript
// frontend/src/contexts/AuthContext.jsx:51–59
const login = useCallback(async (email, password) => {
  const res = await apiFetch('/api/auth/login', { method: 'POST', body: { email, password } });
  setToken(res.access_token);
  setUser(res.user);
}, []);
```

On mount: if token exists, `GET /api/auth/me` hydrates user.

---

## Routing architecture

### Top level (`App.jsx`)

```javascript
// frontend/src/App.jsx:94–124 (structure)
<Route path="/" element={isAuthenticated ? <Navigate to={...} /> : <Landing />} />
<Route path="/login" ... />
<Route path="/register" ... />
<Route path="/pending" element={<ProtectedRoute requireActive={false}><PendingApproval /></ProtectedRoute>} />
<Route path="/*" element={<ProtectedRoute><AppShell /></ProtectedRoute>} />
```

- Admins land on `/admin/tenants`
- Studio owners land on `/dashboard`
- PENDING tenants see `/pending` (cannot access CRM until approved)

### AppShell inner routes

Studio nav groups (`App.jsx:49–81`): Overview, Setup, **Test Front Desk**, Members, Sessions, Trainers, Waitlist, SMS, Studio Info, AI Agent, Support.

Admin nav is minimal: **Manage Tenants** only (`App.jsx:204–216`).

---

## Test Front Desk vs CRM — two different jobs

### LocalChat (`/chat`) — simulate the AI agent

**Purpose:** Text-based rehearsal of the same tool loop voice uses.

**State:**
- Local React state for message bubbles
- `conversation_id` ref (stable UUID per session)
- Optional test caller phone selector (simulates caller-ID)

**API:** Raw `fetch` for SSE (not `apiFetch`):

```javascript
// frontend/src/components/LocalChat.jsx:778–790
const res = await fetch(`${API_BASE}/api/chat/stream`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({ message, conversation_id, test_phone }),
});
```

 Parses `data: {...}` SSE lines as OpenAI `chat.completion.chunk` — same wire format Bolna consumes.

**Backend:** `llm_service.process_message_stream` with tenant from JWT.

### CallerCRM (`/contacts`) — manage real member records

**Purpose:** Staff view of persisted callers from Postgres.

**State:** Fetches `/api/callers`, detail panel with appointments/history from `/api/callers/{id}`.

**No LLM** — pure CRUD REST. Edits member notes, deletes test data, etc.

| Aspect | LocalChat | CallerCRM |
|--------|-----------|-----------|
| Talks to agent | Yes | No |
| Creates test members | Yes (via agent tools) | Displays them |
| Streaming | SSE | JSON REST |
| Route | `/chat` | `/contacts` |

---

## Other notable views

| Component | Data source | Role |
|-----------|-------------|------|
| `Dashboard.jsx` | `GET /api/dashboard/stats` | Charts/KPIs |
| `AppointmentManager.jsx` | `GET /api/appointments`, cancel/patch | Session calendar |
| `AgentConfig.jsx` | `GET/PUT /api/config` | Agent on/off, demo_mode, integrations |
| `KnowledgeBase.jsx` | `GET/PUT /api/knowledge` | Studio FAQ/pricing editor |
| `TenantAdmin.jsx` | `GET /api/tenants`, approve/suspend | Platform admin |

---

## Vite dev vs production

### Development

```bash
cd frontend && npm run dev   # → http://localhost:5173
```

- Hot module reload
- API proxied to backend on 8000
- No build step

### Production

```bash
cd frontend && npm run build   # → frontend/dist/
```

If `frontend/dist` exists, FastAPI serves it (`backend/main.py:320–331`):

- Static assets at `/assets/*`
- All other paths → `index.html` (SPA fallback)

Alternative: deploy `dist/` to Vercel (`frontend/vercel.json`) with `VITE_API_BASE_URL` pointing at Railway/Render API.

---

## Protected routes

```javascript
// frontend/src/components/ProtectedRoute.jsx
// Props: requireAdmin, requireActive
```

- `requireAdmin` — platform admin pages
- `requireActive={false}` — allows PENDING users on `/pending` only

---

## UI patterns worth noting

- **Theme:** `ThemeContext` + Tailwind `dark:` classes
- **Modals/toasts:** `ModalContext` centralizes UX feedback
- **Tenant-specific labels:** `tenantLabels.js` maps enum values to display strings
- **Test data toggle:** `TestDataToggle.jsx` filters test vs real members in lists

Next: [06_voice_and_integrations.md](./06_voice_and_integrations.md)
