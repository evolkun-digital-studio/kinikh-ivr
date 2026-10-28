# Dashboard Testing

## Backend

Run from the repo root with the venv active:

```bash
pytest
pytest --cov=app --cov-report=term-missing
ruff check .
black --check .
isort --check-only .
```

**Results at the time of this build:**

- `pytest`: **424 passed** (381 pre-existing + 43 new: 20 router/behavioral
  tests in `tests/test_api.py`, 15 service-layer tests in
  `tests/test_dashboard_services.py`, 14 HTTP-level route tests in
  `tests/test_dashboard_api_routes.py` — some tests cover multiple
  assertions per the existing repo convention, so file-level counts and this
  total aren't a 1:1 sum of "one test per line").
- Coverage: **80.60%** (was 83.21% before this change — still comfortably
  above the `pyproject.toml` `--cov-fail-under=60` gate; the new
  filter/aggregation query methods in the repository layer are exercised via
  live integration testing below rather than exhaustively unit-tested per
  branch).
- `ruff check .`: clean.
- `black --check .`: clean.
- `isort --check-only .`: clean.

## Backend — live integration testing (real Postgres)

Automated tests mock the DB session (existing repo convention — see
`tests/test_database.py`). To catch real SQL errors in the new joins/
aggregations (`func.date`, `GROUP BY`, multi-table joins), every new endpoint
was additionally exercised against a real Postgres instance
(`docker compose -f docker/docker-compose.dev.yml up -d db`, migrated with
`alembic upgrade head`), both with seeded data and with all tables truncated:

- `GET /calls`, `/calls/{id}` — list, filter by `status`/`department_id`,
  sort by `duration_seconds`, detail with lead/cost/recording joined.
- `GET /leads`, `/leads/{id}` — list, detail with department/call/email_logs.
- `GET /departments` — list with `call_count`/`lead_count`, `POST .../activate`,
  `DELETE` (deactivate).
- `GET /recordings`, `GET /emails`, `GET /usage/summary`, `GET /analytics/overview`,
  `GET /dashboard/summary`, `GET /system/status`.
- **Empty-database state**: every endpoint above returns `200` with `0`/`[]`/`null`
  values — never a 500, never fabricated numbers.
- **Provider status honesty**: `/system/status` correctly reported
  `openai: configuration_pending` and `exotel: configuration_pending` (matching
  the project's actual unconfigured state) and `elevenlabs: connected`,
  after fixing a real bug found during this testing — see below.
- **Auth**: login with the dev admin credentials, 404 on an unknown call/lead
  ID, missing-auth-header rejection.
- **CORS**: preflight `OPTIONS` from `http://localhost:3000` returns the
  expected `access-control-allow-origin` header.

**Bug found and fixed during this testing**: `/system/status` initially
reported `openai`/`exotel` as "connected" because `Settings.mock_credentials_used`
only tracks fields that were *silently backfilled* (missing from `.env`), not
fields that are *present* in `.env` but still literally equal to the known
dev-mock placeholder value (which is the actual case for `OPENAI_API_KEY`,
`EXOTEL_API_SECRET`, `EXOTEL_CALLER_ID`, `EXOTEL_WEBHOOK_SECRET`, and the SMTP
credentials in this project's `.env`). Fixed in `app/api/v1/system.py`
(`_is_mock_value`) to also compare the live value against
`_DEV_MOCK_DEFAULTS`; covered by `TestSystemProviderStatus` in
`tests/test_dashboard_services.py`.

## Frontend

```bash
cd dashboard
npm install
npm run typecheck   # tsc -b --noEmit
npm run build       # tsc -b && vite build
npm run lint         # eslint .
npm run test         # vitest run
```

**Results:** all clean — 0 TypeScript errors, 0 ESLint errors/warnings,
production build succeeds (`dist/` ~365KB gzipped total across 3 chunks:
`vendor`, `charts`, app code — manually split in `vite.config.ts` since
Recharts alone is the largest single dependency), and (added in the
browser-QA follow-up session, see `docs/dashboard_browser_qa.md`) 24 `vitest`
component-render tests across all 13 pages pass — each page has an
empty-state and a populated-state test asserting the real React tree renders
without a `console.error`. This is a real-code-executing substitute for
browser rendering QA, added specifically because no browser was available in
either session; it is not equivalent to one.

## Frontend — manual verification

**Not done via an in-browser click-through, in either this session or the
follow-up QA session.** The Claude in Chrome browser extension has never
had a connected browser in this environment (`list_connected_browsers`
returns empty), so pages have never been visually rendered or interactively
tested end to end. See `docs/dashboard_browser_qa.md` for the fuller
follow-up QA pass (recording-playback security fix, live Range-request
verification, component-render tests). What *was* verified in this original
session instead:

- The Vite dev server (`npm run dev`, port 3000) serves the app and its
  entry module correctly (`curl` against `/` and `/src/main.tsx`).
- A live FastAPI backend (real Postgres, real seeded + empty data) was run
  concurrently on port 8000 and every endpoint the frontend calls was
  exercised directly via `curl` with the exact query shapes the frontend
  sends (see above) — confirming the contract the frontend's `services/*.ts`
  modules rely on is correct.
- CORS preflight from the dashboard's origin succeeds against the backend.
- TypeScript's structural typing against `src/types/api.ts` (kept hand-in-sync
  with the Pydantic response models) is the main remaining guard that the
  frontend parses what the backend actually returns.

**Recommended next step before calling this production-ready**: an actual
browser session (or `npm run dev` + manual click-through) covering login,
each page's loading/empty/error states, the Departments CRUD modal, and the
mobile breakpoint — none of which this session could execute.

## Manual checklist status

| Item | Status |
|---|---|
| Login / logout | Verified via curl (`/auth/login`, `/auth/refresh`) only — not clicked through in a browser |
| Protected routes / 401 handling | Code path verified (`apiRequest` refresh-then-redirect logic); not browser-tested |
| Dashboard, Calls, Leads, Departments, Transcripts, Recordings, Emails, Usage, Analytics, Settings pages | Backend contracts verified live; UI rendering not browser-tested |
| Mobile layout | Not tested (no browser) |
| Empty database state | Verified live against real Postgres for every endpoint |
| Backend unavailable state | Not explicitly tested; `ApiError`/`ErrorState` path exists in code for it |
| Unauthorized API state | Verified — missing/invalid token rejected by the backend |
