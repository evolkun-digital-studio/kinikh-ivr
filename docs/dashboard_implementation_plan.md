# Dashboard Implementation Plan

## 1. Existing Frontend Status

**None.** There is no `dashboard/`, `frontend/`, `client/` directory, no `package.json`
anywhere in the repository, and no static files served by FastAPI. The project is a
pure backend (FastAPI + SQLAlchemy + PostgreSQL) with a telephony/voice pipeline.
The dashboard will be built from scratch as a new `dashboard/` app. Node v26 / npm 11
are available on this machine; npm is used since no other JS package manager
(yarn/pnpm) is present anywhere in the repo.

## 2. Existing Backend Endpoints (`app/api/v1/`)

| Router | Path prefix | Endpoints | Auth |
|---|---|---|---|
| `health.py` | `/api/v1` | `GET /health` | none |
| `auth.py` | `/api/v1/auth` | `POST /login`, `POST /refresh` | none (issues tokens) |
| `leads.py` | `/api/v1/leads` | `GET ""` (limit/offset only, no filters/total), `GET /{id}` | JWT |
| `departments.py` | `/api/v1/departments` | `GET ""`, `POST ""`, `PATCH /{id}`, `DELETE /{id}` (deactivate) | JWT |
| `telephony.py` | `/api/v1/telephony` | Exotel webhooks + audio stream (not for dashboard use) | none (webhook) |

There is **no** `app/schemas/` package — every router defines its request/response
Pydantic models inline. The dashboard backend additions follow this same convention
for consistency.

Notably **missing** for a dashboard: any way to list/inspect Calls, Recordings,
Email logs, API usage/cost, or any aggregate/analytics endpoint.

## 3. Existing Database Information Available to the Dashboard

Models (`app/models/`), all with `id: UUID`, `created_at`, `updated_at`:

- **Call** — `call_sid`, `caller_number`, `callee_number`, `status`, `direction`,
  `duration_seconds`, `recording_url`, `transcript`, `started_at`, `ended_at`,
  `provider`, `error_message`. `1:1` with `Lead`.
- **Lead** — `name`, `phone`, `email`, `requirement`, `summary`, `language`,
  `additional_notes`, `raw_transcript`, `department_id`, `call_id`.
- **Department** — `name`, `display_name`, `email`, `greeting`, `prompt`,
  `knowledge_base`, `keywords[]`, `is_active`.
- **CallRecording** — `call_sid`, `caller_number`, `department`, `language`,
  `start_time`, `end_time`, `duration`, `transcript`, `summary`,
  `audio_file_path`, `lead_id`, `status`, `errors`.
- **CallCost** — `call_sid`, `call_id`, `telephony_cost_usd`, `openai_cost_usd`,
  `voice_cost_usd`, `email_cost_usd`, `total_cost_usd`, `duration_seconds`, `report` (JSON).
- **ApiUsage** — `call_sid`, `call_id`, `provider`, `model`, `operation`,
  `prompt_tokens`, `completion_tokens`, `total_tokens`, `characters`,
  `duration_seconds`, `latency_ms`, `estimated_cost_usd`.
- **EmailLog** — `lead_id`, `recipient_email`, `subject`, `template_name`,
  `status`, `sent_at`, `error_message`, `retry_count`.
- **AuditLog** — exists, not surfaced in the dashboard scope requested.

Repositories only expose a generic `list()` (no filters, no total count) plus a
handful of point lookups (`get_by_sid`, `get_by_phone`, etc.). None support the
filtering/sorting/pagination-with-total-count the dashboard tables need, and there
are no aggregate queries (counts by status/department/language, sums, time buckets).

## 4. Existing Authentication Mechanism

Single hard-coded admin account (`settings.admin_email` / `admin_password_hash`) —
**no user table**. `POST /auth/login` verifies against these settings and issues a
JWT access token (`jwt_access_token_expire_minutes`, default 60 min) and a JWT
refresh token (`jwt_refresh_token_expire_days`, default 30 days). `POST
/auth/refresh` exchanges a valid refresh token for a new pair. Protected routes
depend on `get_current_user_id`, which requires `Authorization: Bearer <token>`
and validates the `type` claim is `"access"`. The dashboard reuses this exactly:
login screen → store tokens → attach bearer header → refresh on 401 → retry once
→ else redirect to login.

## 5. Missing Endpoints Required by the Dashboard

All additive, read-oriented (except pre-existing department CRUD), following the
existing repository → service → API layering:

- `GET /api/v1/calls` — filter by status/department/language/date range/search,
  sort, paginate, return `{items, total}`.
- `GET /api/v1/calls/{id}` — full detail: call fields + lead + cost + recording.
- `GET /api/v1/recordings` — filtered/paginated list of `CallRecording`.
- `GET /api/v1/emails` — filtered/paginated list of `EmailLog` (+ lead/department).
- `GET /api/v1/usage/summary` — cost totals by provider, request/token/character
  counts, telephony seconds, cost-per-call.
- `GET /api/v1/usage/timeseries` — cost over time (daily buckets).
- `GET /api/v1/analytics/overview` — calls/leads over time, calls by department,
  calls by language, duration buckets, completed-vs-failed, email success/failure.
- `GET /api/v1/dashboard/summary` — KPI cards + recent calls + recent leads.
- `GET /api/v1/system/status` — DB connectivity + honest per-provider configuration
  status (derived from `settings.mock_credentials_used`, never fake "connected").
- `GET /api/v1/leads` — extend (additively) with department/date/search filters
  and a `total` count; existing shape (`items` as bare list) is preserved for
  backward compatibility by wrapping in a new response envelope only when the new
  `paginated=true`-style params are used — see risk note below. Decision made:
  change the response envelope to `{items, total}` since the endpoint is
  currently only consumed by tests exercising the router import, not real
  clients, and every dashboard table needs a total for pagination.
- `GET /api/v1/departments` — response enriched with `call_count`/`lead_count`
  (additive fields only).

## 6. Proposed Dashboard Architecture

- **Stack**: React 18 + TypeScript + Vite + Tailwind CSS + React Router +
  TanStack Query (server state/caching, cheap, no need to hand-roll fetch caching)
  + Recharts (lightweight, common charting lib) + Zustand (tiny, for auth/UI state
  only — not a big global store).
- **API client**: one `fetch`-based wrapper in `services/api.ts` — base URL from
  `VITE_API_BASE_URL`, attaches bearer token, normalizes errors, retries once
  after a silent token refresh on 401, else clears session and redirects to
  `/login`.
- **Auth state**: Zustand store persisting only the access/refresh tokens (in
  memory + `localStorage` for persistence across reloads — no secrets, these are
  short-lived user session tokens, not API keys).
- **Layout**: sidebar + topbar shell wrapping all authenticated routes via a
  `ProtectedRoute` wrapper; a bare `Login` route outside the shell.
- **Design system**: a small set of primitive components (Button, Card, Table,
  Badge, Input, Select, Modal, Drawer, Alert, Tabs, Pagination, Skeleton,
  EmptyState, ErrorState) styled with Tailwind utility classes + CSS variables
  for the light/neutral palette, reused across every page.

## 7. Files to Create

Backend (additive only):
- `app/api/v1/calls.py`, `app/api/v1/recordings.py`, `app/api/v1/emails.py`,
  `app/api/v1/usage.py`, `app/api/v1/analytics.py`, `app/api/v1/dashboard.py`,
  `app/api/v1/system.py`
- Tests: `tests/test_api_calls.py`, `tests/test_api_recordings.py`,
  `tests/test_api_emails.py`, `tests/test_api_usage.py`,
  `tests/test_api_analytics.py`, `tests/test_api_dashboard.py`,
  `tests/test_api_system.py`

Frontend: the full `dashboard/` tree described in Phase 1 of the task brief
(`src/app`, `src/components/*`, `src/pages/*`, `src/services/*`, `src/hooks`,
`src/stores`, `src/types`, `src/utils`, `src/styles`), plus config files
(`package.json`, `tsconfig*.json`, `vite.config.ts`, `tailwind.config.js`,
`postcss.config.js`, `.env.example`, `README.md`).

## 8. Files to Modify

- `app/api/v1/router.py` — register the seven new routers.
- `app/api/v1/leads.py` — add filters + total count to `list_leads`.
- `app/api/v1/departments.py` — enrich `DepartmentResponse` with counts.
- `app/repositories/call_repository.py`, `lead_repository.py`,
  `call_recording_repository.py`, `email_log_repository.py`,
  `api_usage_repository.py`, `call_cost_repository.py`,
  `department_repository.py` — add filtered/paginated/aggregate query methods.
- `app/services/call_service.py`, `lead_service.py`, `email_service.py`,
  `usage_tracking_service.py`, `department_service.py` — thin service methods
  wrapping the new repository queries (business logic stays out of routes).
- `docs/api.md` — document new endpoints.

## 9. Backend Changes Genuinely Required

Confirmed necessary because the dashboard cannot function without them:
call/recording/email listing+detail, cost/usage aggregation, analytics
aggregation, a dashboard summary endpoint, and an honest system/config status
endpoint. All are read-only additions except the pre-existing department CRUD
(reused unchanged) and the two additive response-shape changes to
`GET /leads` and `GET /departments` noted above. **No changes** touch the voice
pipeline, OpenAI/Exotel clients, or telephony webhook handling.

## 10. Potential Risks

- **`GET /leads` response shape change** (`list[LeadResponse]` → `{items,
  total}`): a breaking change for any existing consumer. Mitigated: grepped the
  codebase — the only caller is the dashboard being built and the existing
  smoke test only checks the router imports, not the response shape. Backend
  test for the old shape does not exist, so this is safe.
- **N+1 queries on `/departments`** when attaching call/lead counts — mitigated
  with a single grouped `COUNT ... GROUP BY department_id` query per list call,
  not one query per department.
- **Large transcript/report payloads** in call detail — acceptable at current
  scale; not paginated further since a single call's transcript is bounded.
- **No real OpenAI/Exotel credentials** — dashboard must never fabricate cost or
  usage numbers for these; `/system/status` and the Settings page read
  `mock_credentials_used` to report "Configuration Pending" honestly.
- **CORS**: `.env` already allows `http://localhost:3000`, matching Vite's
  default dev port — no backend config change needed for local dev.
