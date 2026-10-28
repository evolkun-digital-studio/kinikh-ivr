# Dashboard Architecture

## Stack

React 18 + TypeScript + Vite + Tailwind CSS + React Router v6 + TanStack Query
+ Zustand + Recharts. No other runtime dependencies. Chosen for being the
smallest set of libraries that covers routing, server-state caching, minimal
client state, and charts — nothing was added "for later."

## Routes (`src/app/router.tsx`)

| Path | Page | Notes |
|---|---|---|
| `/login` | `Login` | Public. Redirects to `/` if already authenticated. |
| `/` | `Dashboard` | KPIs, activity charts, recent calls/leads. |
| `/calls` | `Calls` | Filterable/sortable/paginated table. |
| `/calls/:id` | `CallDetails` | Full call record: info, transcript, lead, recording, cost. |
| `/leads` | `Leads` | Filterable/sortable/paginated table. |
| `/leads/:id` | `LeadDetails` | Extracted info, call reference, transcript, email delivery. |
| `/departments` | `Departments` | CRUD + activate/deactivate. |
| `/transcripts` | `Transcripts` | List + drawer with the conversation timeline. |
| `/recordings` | `Recordings` | List with inline audio player where available. |
| `/emails` | `Emails` | Delivery log with status badges. |
| `/usage` | `Usage` | Cost totals, per-provider breakdown, cost-over-time, provider status. |
| `/analytics` | `Analytics` | All aggregate charts, time-range selectable. |
| `/settings` | `Settings` | AI/Voice/Telephony/Email provider status + system health. |

All routes except `/login` render inside `AppShell` (sidebar + topbar) and are
gated by `useAuthStore().isAuthenticated` — an unauthenticated visit to any
path redirects to `/login`.

## Authentication (`src/hooks/useAuth.ts`, `src/stores/authStore.ts`, `src/services/api.ts`)

Reuses the backend's existing single-admin JWT flow unchanged:

1. `POST /auth/login` → `{access_token, refresh_token}`, stored in
   `localStorage` via the Zustand `authStore` (session tokens, not secrets —
   short-lived, revocable by rotating `JWT_SECRET_KEY` server-side).
2. Every authenticated request attaches `Authorization: Bearer <access_token>`.
3. On a `401`, `apiRequest` (in `services/api.ts`) transparently calls
   `POST /auth/refresh` once, retries the original request with the new
   token, and only then gives up. A failed refresh clears the store, which
   the router observes reactively and redirects to `/login` — no manual
   redirect wiring needed per page.
4. Logout clears the store and navigates to `/login`.

## API client (`src/services/api.ts`)

One `fetch`-based client, not a generated SDK — the backend has under 25
endpoints and a wrapper library would be pure overhead. It centralizes: base
URL from `VITE_API_BASE_URL`, bearer header injection, query-string building
(drops empty/undefined filter values), JSON error normalization into a typed
`ApiError`, and the 401→refresh→retry flow above. Each backend resource gets
a thin `services/*.ts` module (`calls.ts`, `leads.ts`, etc.) that just types
the query params and response shape — no logic duplicated across them.

## State management

- **Server state**: TanStack Query exclusively. Every page fetches via
  `useQuery`/`useMutation`; there is no manually-managed "loading" boolean
  anywhere. Mutations (`Departments` CRUD) invalidate the relevant query key
  on success instead of manually patching cached data.
- **Auth state**: one small Zustand store, described above.
- **UI state** (filters, pagination offset, drawer/modal open state, sort
  column): local `useState` in the owning page component. Nothing here
  warranted a global store — filters are scoped to the page that owns them.

## Design system (`src/components/{common,cards,tables,badges,modals,charts}`)

Every page is built from a small set of primitives so the whole app is
visually consistent: `Button`, `Input`/`Select`, `Card`, `KpiCard`,
`DataTable` (loading/error/empty states built in, optional client-driven
sort via column `sortKey`), `StatusBadge` (maps ~15 known status strings to
one of 4 tones), `Modal`/`Drawer`, `Pagination`, `Tabs`, `Alert`,
`Skeleton`/`TableSkeleton`/`CardSkeleton`, `EmptyState`, `ErrorState`.

Charts (`components/charts/`) use the validated categorical/status palette
from the project's dataviz skill reference instance
(`references/palette.md`) rather than ad hoc colors — fixed hue order for
categorical series, the status palette (good/warning/serious/critical)
reserved for state (completed/failed, email delivery status), one sequential
blue hue for magnitude-over-time. Animations are disabled
(`isAnimationActive={false}`) per the "no unnecessary animations" brief.

## Backend endpoints used

See `docs/dashboard_implementation_plan.md` §5 for the full list of new
endpoints added to support this dashboard, and §2 for what already existed.
In short: `GET /health`, `/auth/login`, `/auth/refresh`, `/leads`,
`/leads/{id}`, `/departments` (+ create/patch/activate/deactivate),
`/calls`, `/calls/{id}`, `/recordings`, `/recordings/{id}`, `/emails`,
`/usage/summary`, `/usage/timeseries`, `/analytics/overview`,
`/dashboard/summary`, `/system/status`.

## Recording playback

`CallRecording.audio_file_path` is DB-stored, untrusted input — never an
absolute path, always relative to `settings.recordings_dir`. It is resolved
safely by `app/core/media.py::resolve_audio_path`, which rejects absolute
paths, traversal outside the recordings directory, and disallowed file
extensions before ever touching disk; a missing recording, a missing file,
and a rejected path all return an identical `404`. The API never returns the
raw path — `RecordingResponse`/`RecordingInfo` only expose `has_audio: bool`.
Audio is served via `GET /recordings/{id}/audio` (authenticated, `Range`-
request capable through Starlette's native `FileResponse`). Because a native
`<audio src>` can't attach an `Authorization` header, the frontend's
`AudioPlayer` component fetches the file with `fetch()` instead and plays it
from an object URL — loaded lazily on first play (not on mount), since it
renders once per row in the Recordings table and eagerly downloading every
row's full audio just to paint the list would be wasteful.

## Known limitations

- **Sorting** is limited to a small whitelisted column set per resource
  (enforced server-side in the repository layer) rather than arbitrary
  columns — deliberate, to avoid exposing unindexed sort columns.
- **Departments list** always fetches all departments (active + inactive) in
  one request with a grouped count query for calls/leads — fine at the
  current scale; would need pagination if the department count grows large.
