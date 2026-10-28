# Dashboard Browser QA

## Environment constraint — read this first

**No real browser was available in this environment.** `list_connected_browsers`
(Claude in Chrome) returned an empty list — not "disconnected," zero browsers
have ever paired with this session — so no click-through, no screenshot, and
no live console/network inspection was possible via the Claude in Chrome tool.
This is disclosed up front because the task explicitly asks for actual
browser-based QA, and claiming that without a browser would be dishonest.

What follows is everything that **could** be verified without one, done as
rigorously as the constraint allows:

1. **Live backend integration testing** — a real FastAPI process against a
   real Postgres instance (`docker compose -f docker/docker-compose.dev.yml`),
   exercised with `curl` using the exact request shapes the frontend sends,
   both with seeded data and with every table truncated.
2. **Component-level render testing** — `vitest` + `@testing-library/react` +
   `jsdom`, added in this session specifically to substitute for browser QA.
   These actually mount every page's real React tree (real Recharts charts,
   real Tailwind-classed DOM, real React Query state machine), assert on
   rendered output, and fail on any React console error (missing keys,
   prop-type violations, thrown exceptions, act() warnings). This is real
   verification, not static analysis — but it is not the same as a human or
   an agent looking at pixels in an actual browser window, and it cannot
   catch layout/visual issues (overlap, clipping, actual viewport overflow).
3. **Static/build tooling** — TypeScript, ESLint, production build.
4. **Code-level audit** for the responsive and security requirements that
   don't require a running browser to reason about (Tailwind breakpoint
   usage, table overflow containment, grep of the built JS bundle for
   secrets).

**Do not read "24/24 tests passing" as "the UI was seen working."** It means
the rendering logic didn't throw, produced the expected DOM nodes, and Recharts
didn't error inside jsdom. Nothing here confirms how it actually looks or
whether every click target lands correctly.

## Browser tested

None. (See above.)

## Pages tested (component-render level, not visual)

Login, Dashboard, Calls, Call Details, Leads, Lead Details, Departments,
Transcripts, Recordings, Emails, API Usage, Analytics, Settings — all 13.
Each has at least one empty-state render test and one populated-state render
test (`dashboard/src/pages/__tests__/*.test.tsx`), asserting zero
`console.error` calls during render in addition to specific DOM assertions.

Also verified live via the real backend (curl, not a browser): calls list +
detail + filters + sort, leads list + detail, departments list + CRUD +
activate/deactivate, recordings list, emails list, usage summary/timeseries,
analytics overview, dashboard summary, system status, auth login/refresh,
401/404 handling, CORS preflight from `http://localhost:3000`.

## Issues found

1. **Recording playback was fundamentally unsafe for browser use** (the
   subject of this task's Phase 5) — `audio_file_path` was a raw filesystem
   path returned directly in three API responses (`RecordingResponse`,
   `RecordingDetailResponse`, `CallDetailResponse.recording`), with nothing
   validating it before use and no serving mechanism at all. See "Recording
   playback" below.
2. **`AudioPlayer` would have eagerly downloaded every visible recording's
   full audio file** — caught while building the fix, not by a browser: an
   early version of the rewritten player fetched the blob on mount, which is
   correct for the one-player Call Details page but would fire N full-file
   downloads simultaneously on the Recordings *list* (up to 20 rows/page).
   Fixed before it shipped (see below) — flagged here because it's exactly
   the kind of thing real browser QA (a Network tab) would have caught, and
   this session's substitute (render tests) also happened to catch it via an
   explicit "`fetch` not called on render" assertion.
3. **`/system/status` reported providers as "connected" when they weren't**
   — found in the *previous* session's live-backend testing (see
   `docs/dashboard_testing.md`), re-confirmed still correct in this session.
   Not a new finding, listed here for completeness since Phase 7 asks this
   session to re-verify it.
4. Two React Router "future flag" deprecation warnings appear in every test
   run (`v7_startTransition`, `v7_relativeSplatPath`). These are
   `console.warn`, not `console.error` — they don't fail any test and
   indicate no current bug, just a v7-migration heads-up from the library
   itself. Not fixed (out of scope; would only matter if/when upgrading to
   React Router v7).
5. **No toast/notification system exists.** The original design-system scope
   (this project's earlier phase) specified Alerts, not Toasts, and none were
   built. Department activate/deactivate currently gives only implicit
   feedback (the status badge updates); there is no explicit success toast.
   Not a regression from this session's work — flagging it because the QA
   checklist asks about toasts and there is nothing to test.

## Issues fixed

- **Recording playback architecture rebuilt end-to-end** (Phase 5 — full
  details below).
- **`AudioPlayer` made lazy**: fetches on first play click, not on mount, so
  rendering a page with several recordings never triggers unrequested
  downloads. Verified with a test that spies on `global.fetch` and asserts
  it's never called just from rendering the Recordings list.
- **`window.localStorage` explicit references** in `authStore.ts` (was bare
  `localStorage`) — found while wiring up the new component tests: Node 22+
  ships its own non-functional global `localStorage` that shadows jsdom's,
  and the bare identifier resolved to Node's broken one instead of the
  browser/jsdom one. This was latent (real browsers don't have this
  ambiguity — `window.localStorage` and bare `localStorage` are identical
  there), but the explicit form is more correct regardless of environment
  and is the actual, if narrow, root cause of a real failure mode this
  session hit.
- Three test-assertion bugs in the new test suite itself (ambiguous
  `getByText` matches where the same string legitimately appears twice —
  e.g. a "Failed" status badge and a "Failed" filter-dropdown option). Not
  application bugs; fixed by using `getAllByText`/`getByRole` instead.

## Recording playback

**Before**: `CallRecording.audio_file_path` (a raw filesystem path, e.g.
`/some/server/path/recording.wav`) was returned directly in three JSON API
responses, with no serving endpoint at all — nothing in the codebase before
this session ever read that field to produce bytes a browser could play, and
nothing validated it.

**After**:

- `app/core/media.py` (new): `resolve_audio_path(base_dir, stored_path)`
  treats the stored value as *always relative to* `settings.recordings_dir`
  (new setting, default `storage/recordings`) — never trusted as absolute.
  Rejects, uniformly returning `None` (never raising, so callers can't leak
  which failure mode occurred): absolute paths, extensions outside
  `{.wav, .mp3, .ogg, .m4a, .webm}`, anything that resolves (via `Path.resolve`
  + `is_relative_to`) outside the base directory, and anything that isn't an
  existing regular file.
- `GET /api/v1/recordings/{id}/audio` (new, `app/api/v1/recordings.py`):
  requires the same `get_current_user_id` bearer-token auth as every other
  endpoint. Looks up the recording (404 if missing), resolves the path (404
  if invalid/missing/traversal), and streams it via Starlette's
  `FileResponse`, which natively supports `Range` requests — verified live
  (`curl -H "Range: bytes=0-999"` returned `206 Partial Content` with a
  correct `Content-Range` header and exactly 1000 bytes).
- `RecordingResponse`, `RecordingDetailResponse`, and
  `CallDetailResponse.recording` (`RecordingInfo`) no longer have an
  `audio_file_path` field at all — only `has_audio: bool`. Confirmed live:
  the field is absent from every JSON response, not just null.
- Frontend `AudioPlayer` no longer takes a `src` URL. It takes a
  `recordingId`, and on first play, `fetch()`s
  `${API_BASE}/api/v1/recordings/{id}/audio` with the bearer header attached
  (a native `<audio src>` can't attach custom headers, which is why this
  isn't just pointing the tag at the URL directly), turns the response into
  a Blob, and plays it from an object URL. `CallDetails.tsx` and
  `Recordings.tsx` updated accordingly; no component references
  `audio_file_path` anywhere in the frontend anymore (verified by grep).
- Verified live end-to-end against a real file on disk: full download is
  byte-identical to the source file; a path-traversal attempt
  (`audio_file_path = "../../secret.wav"`) and an absolute-path attempt both
  return a plain 404 identical to "recording not found"; a request with no
  `Authorization` header is rejected (422, consistent with every other
  endpoint's dependency-injection behavior); a request with a garbage bearer
  token is rejected (401).

## Security checks

- Grepped the production build (`dashboard/dist/assets/*.js`) for
  `OPENAI_API_KEY`, `EXOTEL_TOKEN`, `EXOTEL_API_SECRET`, `SMTP_PASSWORD`,
  `DATABASE_URL`, `WEBHOOK_SECRET`, `JWT_SECRET`, the actual dev-mock key
  values, and `postgresql://`/`postgresql+asyncpg://` connection strings —
  **none present**.
- `dashboard/.env` / `.env.example` contain only `VITE_API_BASE_URL` — no
  secret has ever been added to a `VITE_*` variable.
- No filesystem path appears in any API response (recordings, calls) —
  confirmed both by reading the Pydantic schemas and by inspecting live
  response bodies.
- Auth: every dashboard-facing endpoint (including the new audio endpoint)
  requires a valid bearer token; missing/invalid tokens are rejected (422/401
  respectively, consistent with the rest of the API).
- `storage/` (the new recordings directory) added to `.gitignore` — recording
  files are runtime data and must never be committed.
- `system.py`'s honest-status logic re-verified: still correctly reports
  `openai`/`exotel` as `configuration_pending` and `elevenlabs` as
  `connected` against the project's actual `.env`.

## Backend tests

`pytest`: **447 passed** (was 424 before this session's work; +23 new — 12
for `app/core/media.py`'s path-safety logic, 11 HTTP-level tests for
`GET /recordings/{id}/audio` covering authorized access, missing auth header,
missing recording, missing `audio_file_path`, missing file on disk, path
traversal, absolute-path attempt, disallowed extensions (parametrized ×3),
and a real `Range` request asserting `206`/`Content-Range`/exact byte count).

## Coverage

**80.87%** (was 80.61%; the new `app/core/media.py` and the audio endpoint
are both fully exercised by the new tests). Still well above the
`pyproject.toml` 60% gate.

## Ruff

Clean (`ruff check .` — all checks passed).

## Black

Clean (`black --check .` — 130 files unchanged).

## isort

Clean (`isort --check-only .` — no reported issues; 7 files skipped per
existing project config, unrelated to this session's changes).

## Frontend build

Clean. `npm run build` (`tsc -b && vite build`) succeeds; output unchanged in
shape from the prior session (3 chunks — app, vendor, charts — ~365KB
gzipped total).

## TypeScript

Clean (`tsc -b --noEmit` — 0 errors, including the new test files and
`vitest.config.ts`).

## ESLint

Clean (`eslint .` — 0 errors, 0 warnings).

## Frontend component tests (new this session)

`vitest run`: **24 passed** across 13 files — one file per page, each with an
empty-state and a populated-state render test, all asserting zero
`console.error` during render. This is the closest available substitute for
browser console QA in this environment; it is not a replacement for one.

## Remaining known issues

- **No real browser verification has ever been performed on this
  dashboard**, in this session or the prior one. Visual rendering, actual
  click interactions, real responsive behavior at 1440/1280/768/390px, real
  network-tab inspection, and real console output have not been observed.
  Everything in this report is inferred from server-side integration tests
  and jsdom component renders, which cannot see layout, spacing, overlap, or
  genuine viewport overflow.
- Responsive layout (Phase 3) was audited by reading the Tailwind classes
  (grid breakpoints, `overflow-x-auto` wrapping every table so scroll is
  contained to the table rather than the page, fraction-based grid columns
  in filter bars) rather than observed at the four required widths. No
  hard-coded pixel widths wider than a mobile viewport were found in any
  page or layout component, which is the main mechanism by which unwanted
  page-level horizontal scroll would occur — but this is a code-reading
  argument, not a measurement.
- No toast/notification system exists (see "Issues found" above) — not a
  regression, just never built.
- `decode_token`'s error message on a malformed bearer token includes a raw
  Python codec exception string (`"Invalid header string: 'utf-8' codec
  can't decode byte ..."`). Not a security issue (no secret data), not
  introduced by this session, and out of this task's scope — noted only
  because it surfaced while testing 401 handling on the new endpoint.

## Final Dashboard Status

**Not production-ready to call "browser QA passed."** The backend is
thoroughly verified (regression-tested, integration-tested against real
Postgres, and the recording-playback security fix is verified live down to
byte-for-byte file comparison and actual Range-request behavior). The
frontend is verified as far as static tooling and component-render testing
can reach. But the thing this task most specifically asked for — a human or
agent actually looking at the dashboard in a browser, clicking through it,
and confirming it looks and behaves correctly at four screen sizes — could
not be done in this environment, and nothing here should be read as a
substitute for that. **The next session that has a connected browser should
do that pass before this is called done.**
