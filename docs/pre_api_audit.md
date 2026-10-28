# Kinikh IVR — Pre-API Production Readiness Audit

**Date:** 2026-09-16
**Scope:** Internal completeness and correctness only. No real external API credentials
were used, requested, or required at any point in this audit.

---

## Environment Status

**PASS**

- Python 3.12.1, venv at `venv/`.
- All declared dependencies installed and import-clean, after fixing two missing/incompatible
  pins (`greenlet`, `bcrypt` — see Issues Fixed).
- FastAPI 0.128.8 / Pydantic 2.13.4 / SQLAlchemy 2.0.51 / Alembic 1.16.5 — all mutually
  compatible, confirmed by a full application boot and the full test suite.
- Full-app import smoke test passes; no circular imports, no orphaned `__init__.py` gaps.

## Application Status

**PASS** (after fix)

- The app previously could **not** start without real `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
  `EXOTEL_*`, and `SMTP_*` values (`Settings` fields were all `Field(...)`-required). This
  violated the audit's core requirement of a safe mock/dev boot mode and has been fixed
  (see Issues Fixed #1).
- With `APP_ENV=development` and only `DATABASE_URL` set, the app now boots, logs a clear
  `WARNING` naming every mocked credential, and serves traffic — verified locally and inside
  Docker.

## Database Status

**PASS** (after fix)

- Async engine / session factory / `BaseRepository` CRUD all verified against a **real**
  PostgreSQL 16 instance (Docker), not just mocks — this caught two real bugs (see below).
- Full CRUD verified end-to-end for `Department`, `Call`, and `Lead` against a live database.
- `alembic upgrade head` initially failed (missing `greenlet` — fixed). After the fix, it runs
  cleanly and creates all tables the ORM models expect.

## Docker Status

**PASS** (after fix)

- `docker compose -f docker/docker-compose.yml config` and the `.dev.yml` variant both validate.
- Runtime image builds successfully (multi-stage, non-root user, healthcheck).
- **Critical finding, fixed:** there was no `.dockerignore`, so `COPY . .` baked `.env` (real
  secrets in a real deployment), `.git/`, and the 300+ MB `venv/` directory directly into the
  image. Added `.dockerignore`; image size dropped 748 MB → 452 MB and the leak is closed.
- Container boots, connects to Postgres, passes its own `HEALTHCHECK` (`healthy` status
  confirmed), and serves `/api/v1/health` with `database: "ok"`.
- Redis and full multi-container `docker compose up` could not be end-to-end verified in this
  environment because port 6379 was already bound by an unrelated local `redis-server`
  process on the host machine — not a project defect. `db` + `app` were verified together
  directly. Also note: Redis is provisioned in both compose files and `.env.example` but is
  **not referenced anywhere in `app/`** — dead infrastructure (see Remaining Issues).
- `nginx.conf` is syntactically valid and correctly proxies `/api/v1/health` (not bare
  `/health` — see Application/API note below); full validation requires the `app` service's
  Docker DNS name and real TLS certs, both expected only in a running compose stack.

## API Status

**PASS**

- App starts locally and via Docker; `GET /api/v1/health` returns `200` with `database: "ok"`.
- **Note:** the audit brief's "GET /health" is not a real route — the only health route is
  `/api/v1/health` (versioned, no bare `/health` alias). The Dockerfile's own `HEALTHCHECK`
  already correctly targets `/api/v1/health`, so this is consistent internally, just worth
  knowing for load-balancer / uptime-check configuration.
- All 9 REST routes registered correctly (`/api/v1/health`, `/auth/login`, `/auth/refresh`,
  `/leads`, `/leads/{id}`, `/departments`, `/departments/{id}`, `/telephony/incoming`,
  `/telephony/status`) plus the `/telephony/stream` WebSocket (not in OpenAPI, as expected).
  `/docs` and `/redoc` both serve `200`.
- Error handling verified live: malformed webhook payloads and missing auth headers return
  structured 4xx JSON, not raw tracebacks; unhandled exceptions return a generic
  `INTERNAL_ERROR` body (no stack trace or secret leakage) — confirmed by triggering a real
  DB `IntegrityError` and inspecting the client-facing response.
- CORS and security headers (`X-Content-Type-Options`, `X-Frame-Options`) confirmed present
  on live responses.

## AI Integration Readiness

**PASS**

- `OpenAIClient` wrapper, `ConversationEngine`, `LeadExtractor`, `DepartmentClassifier`,
  `SummaryGenerator`, `LanguageDetector` all accept an injected client and are fully covered
  by the existing mock-based test suite (`test_ai.py`, 109+ tests): malformed JSON, missing
  fields, timeouts, and service failures are all exercised.
- No code path constructs a real OpenAI request during any test or during dev-mode boot.

## Voice Integration Readiness

**PASS**

- `VoiceManager`, `AudioBuffer`, `SpeechHandler`, STT/TTS wrappers, and both provider
  implementations (`OpenAIVoiceProvider`, `ElevenLabsProvider`) are provider-agnostic behind
  `BaseVoiceProvider` and fully mock-tested end to end (audio in → transcript → AI reply →
  synthesized audio → WebSocket send).
- Fixed a minor test-hygiene bug here: a test left an `AudioBuffer`'s internal silence-flush
  `asyncio.Task` un-drained, producing a "Task was destroyed but it is pending" warning on
  every test run. Not a production bug (`SpeechHandler.finalize()` always calls `drain()` on
  the real call-exit path) — fixed by draining in the test.

## Telephony Integration Readiness

**PASS** (after fixes; one design gap noted)

- Full mock pipeline verified: webhook → `CallService.initiate()` → DB → WebSocket → audio →
  STT → conversation → TTS → call end → lead extraction → DB → email.
- **Two real bugs found and fixed via live HTTP testing against the running app** (not just
  unit tests) — see Issues Fixed #4 and #5: missing/empty `CallSid` was silently accepted and
  written to the DB (now rejected with `422`); a redelivered ("duplicate") webhook crashed
  with an unhandled `500` (now idempotent).
- **Design gap, not fixed (see Remaining Issues):** a fully-built `ExotelWebhookHandler` with
  HMAC signature verification and `CallManager` integration exists at
  `app/telephony/exotel/webhook_handler.py`, but the live route in `app/api/v1/telephony.py`
  bypasses it entirely and calls `ExotelAdapter` + `CallService` directly. **The live incoming-
  webhook route currently performs no signature verification at all** — anyone who can reach
  `/api/v1/telephony/incoming` can inject a fake call. This is a genuine security gap, but
  wiring in the existing handler is an architecture-level integration decision (which webhook
  header carries the signature, how `CallManager` state should relate to `CallService`/DB
  state) that is out of scope for an audit-only pass — flagged for a deliberate follow-up.

## Email Integration Readiness

**PASS**

- `EmailService` → `TemplateEngine` (Jinja2) → `SMTPClient` → `EmailSender` (retry) is fully
  covered by `test_email.py`: correct recipient/subject/HTML, SMTP failure, retry, permanent
  failure. No real SMTP connection is ever attempted in tests or dev-mode boot.
- Confirmed by code inspection: email failures are caught and logged inside
  `EmailService`/`EmailSender` and never propagate in a way that would roll back or corrupt an
  already-committed `Lead` record.

## Security Status

**PASS** (after fixes; gaps noted)

- SQL injection: no raw/formatted SQL anywhere in `app/` — 100% parameterized SQLAlchemy
  ORM/Core.
- Password hashing: **critical bug found and fixed** — `passlib==1.7.4` + `bcrypt==5.0.0` is a
  known-broken combination; `hash_password()`/`verify_password()` crashed on *every* call,
  meaning `/auth/login` was completely non-functional. Reproduced live, fixed by pinning
  `bcrypt==4.0.1` (see Issues Fixed #2).
- JWT: **bug found and fixed** — `get_current_user_id()` never checked the token `type` claim,
  so a long-lived refresh token could be used directly as a short-lived Bearer access token on
  every protected route. Fixed by rejecting non-`access` tokens (see Issues Fixed #3).
- **Finding, fixed:** the admin login used hardcoded, non-rotatable credentials
  (`admin@kinikh.com` / bcrypt hash of `"changeme"`) baked directly into `app/api/v1/auth.py`,
  despite a comment claiming they were rotatable via env. Moved to `Settings.admin_email` /
  `Settings.admin_password_hash`, required in production, mock-defaulted in dev mode.
- Rate limiting: enforced only at the Nginx layer (`limit_req_zone` in `docker/nginx.conf`),
  which is fine when Nginx fronts the app — but `Settings.rate_limit_calls_per_minute` /
  `rate_limit_burst` are declared and documented in `.env.example` yet never read by any code
  in `app/`. If the app is ever exposed directly (e.g. the dev compose, which has no Nginx),
  there is no rate limiting at all. Not fixed — implementing in-app rate limiting is a new
  feature, out of this audit's scope.
- Internal exceptions/secrets: confirmed never leaked to clients — a real DB `IntegrityError`
  returned only `{"error":{"code":"INTERNAL_ERROR","message":"An unexpected error occurred"}}`
  to the client, with the real trace only in server logs.
- `.gitignore` previously excluded **all Alembic migration files**
  (`alembic/versions/*.py`) from version control — see Issues Fixed #6. This is unrelated to
  runtime security but is a repository-integrity issue worth flagging here since it would have
  silently discarded schema history.

## Test Status

- **Tests executed:** `pytest`, `pytest --cov=app --cov-report=term-missing`
- **Number of tests:** 381
- **Passed:** 381
- **Failed:** 0
- **Coverage:** 83.21% (statement+branch combined; baseline before this audit was 68.21%
  across 327 tests, all passing)

Ruff, Black, and isort all report zero violations (`ruff check .`, `black --check .`,
`isort --check-only .`).

## Issues Found

1. `Settings` required real `OPENAI_API_KEY` / `ELEVENLABS_API_KEY` / `EXOTEL_*` / `SMTP_*` to
   even construct — the app could not boot in a safe mock/dev mode at all.
2. `passlib==1.7.4` + `bcrypt==5.0.0` incompatibility: `hash_password()`/`verify_password()`
   raised `ValueError` on every call. `/auth/login` was completely broken.
3. `get_current_user_id()` accepted refresh tokens as access tokens (no `type` claim check).
4. Exotel incoming/status webhook parser defaulted a missing `CallSid` to `""` instead of
   rejecting the request; the empty string was written straight to the `calls` table.
5. `CallService.initiate()` was not idempotent — a redelivered webhook (same `CallSid` twice,
   an explicitly-expected Exotel/telephony behavior) crashed with an unhandled 500
   `IntegrityError` instead of being handled gracefully.
6. `.gitignore` excluded `alembic/versions/*.py`, i.e. **every** migration file, from version
   control.
7. No `.dockerignore` — `docker build` copied `.env` (secrets), `.git/`, and `venv/` into the
   image.
8. `requirements.txt`/`pyproject.toml` were missing `greenlet`, a hard runtime dependency of
   SQLAlchemy's async engine (`create_async_engine(...).connect()` fails without it). Masked
   entirely by the test suite's use of mocked `AsyncSession` objects, which never exercise a
   real engine connection.
9. Three ORM models (`ApiUsage`, `CallCost`, `CallRecording`) existed with no corresponding
   Alembic migration — `alembic upgrade head` created only 5 of the 8 modeled tables.
10. Hardcoded, non-rotatable admin credentials in `app/api/v1/auth.py` (contradicted its own
    "rotate via env in production" comment).
11. Admin login endpoint had zero behavioral test coverage (only a "router is importable"
    smoke test) — which is how bug #2 went undetected.
12. `AudioBuffer` test left a background `asyncio.Task` undrained, producing a stderr warning
    on every test run (test-only, not a production bug).
13. `alembic/env.py` vs. standalone `isort` disagreed on whether the local `alembic/`
    directory or the third-party `alembic` package should be treated as first-party, because
    isort auto-detects any top-level directory as first-party by default.
14. Stale Ruff config referenced two removed rule codes (`ANN101`, `ANN102`), producing a
    warning on every run.
15. Both `docker-compose.yml` files used the obsolete Compose `version:` key, producing a
    warning on every invocation.
16. `EventBus` (with 11 fully-defined event types matching the audit's own event catalog),
    `UsageTrackingService`/cost-tracking, and `CallRecordingService` are fully implemented,
    individually correct (see Issues Fixed for their new test coverage), but **entirely
    unwired** — not imported by `SpeechHandler`, `CallService`, or any API route. 0% coverage
    before this audit.
17. `ExotelWebhookHandler` (signature verification + `CallManager` integration) and
    `CallStateMachine` (`app/call/state.py`, exactly matching the state machine this audit's
    own brief describes) are both fully implemented and tested in isolation but never imported
    by the live call-handling code path.
18. `CallRecordingService` has no configuration flag to disable recording, and isn't wired in
    to be recording anything regardless.

## Issues Fixed

1. **Dev/mock boot mode** — `app/core/config.py`: added a `model_validator(mode="before")`
   that backfills all external-credential fields with clearly-fake values (logged once as a
   `WARNING` naming every mocked field) **only** when `APP_ENV=development`; production mode
   is untouched and still requires every real credential. Verified: boots with zero real
   credentials in dev mode; still raises `ValidationError` in production mode with no
   credentials set.
2. **bcrypt/passlib crash** — pinned `bcrypt==4.0.1` in `requirements.txt` and
   `pyproject.toml` (was `5.0.0`). Verified: `hash_password()`/`verify_password()` round-trip
   correctly; added 16 new behavioral tests in `tests/test_auth_and_security.py`.
3. **Refresh-token-as-access-token** — `app/core/dependencies.py`: `get_current_user_id()` now
   rejects any token whose `type` claim isn't `"access"`. Covered by new regression tests.
4. **Missing CallSid accepted** — `app/api/v1/telephony.py`: both webhook routes now raise
   `ValidationError` (422) when `call_info.call_sid` is empty, before touching the DB. Verified
   live against the running app (curl) and with new tests in
   `tests/test_telephony_webhook_validation.py`.
5. **Duplicate webhook crash** — `app/services/call_service.py`: `initiate()` now checks for an
   existing call by `call_sid` first and returns it unchanged instead of attempting a duplicate
   insert. Verified live (two identical webhook deliveries both return `200`) and with new
   unit + HTTP-level tests.
6. **Migrations excluded from git** — removed the `alembic/versions/*.py` /
   `!alembic/versions/.gitkeep` lines from `.gitignore`. Verified with `git check-ignore`
   before/after.
7. **Secrets baked into Docker image** — added `.dockerignore` (excludes `.env*`, `.git/`,
   `venv/`, `tests/`, caches, docs, etc). Verified: rebuilt image no longer contains `/app/.env`,
   `/app/venv`, or `/app/.git`; image size dropped 748 MB → 452 MB.
8. **Missing `greenlet` dependency** — added `greenlet==3.5.6` to `requirements.txt` and
   `pyproject.toml`. Verified: `alembic upgrade head` and real (non-mocked) async DB access
   both work; previously both raised `ValueError: the greenlet library is required...`.
9. **Missing migration for 3 tables** — generated and hand-trimmed
   `alembic/versions/1a15ed1a5fc5_add_api_usage_call_costs_call_.py` (kept scoped to the three
   new tables; did not touch an unrelated, purely cosmetic constraint-vs-index difference
   autogenerate also flagged on `calls`/`departments`). Verified: `alembic upgrade head`,
   `downgrade -1`, `downgrade base`, and `upgrade head` again all succeed; all 8 modeled tables
   present after upgrade.
10. **Hardcoded admin credentials** — moved to `Settings.admin_email` /
    `Settings.admin_password_hash` (required in production, mock-defaulted in dev mode);
    `.env.example` documents both, with a one-liner to generate a real hash.
11. **Zero auth test coverage** — added `tests/test_auth_and_security.py` (16 tests: password
    hashing, JWT create/decode, token-type enforcement, and live `/auth/login` +
    `/auth/refresh` HTTP behavior).
12. **Dangling asyncio task in tests** — `tests/test_voice.py`: added the missing
    `await buf.drain()`. Verified: the "Task was destroyed but it is pending" warning no longer
    appears in any test run.
13. **isort/ruff first-party disagreement** — `pyproject.toml`: added
    `known_third_party = ["alembic"]` under `[tool.isort]` so both tools agree the local
    `alembic/` directory is not the same thing as the `alembic` PyPI package.
14. **Stale Ruff ignore list** — removed `ANN101`/`ANN102` from `[tool.ruff.lint] ignore`
    (rules no longer exist in the installed Ruff version).
15. **Obsolete Compose `version:` key** — removed from both `docker/docker-compose.yml` and
    `docker/docker-compose.dev.yml`.
16. **Coverage gap in unwired-but-real subsystems** — added
    `tests/test_events_and_usage.py` (34 tests) covering `EventBus`, all 11 event types, both
    subscribers, `UsageTrackingService` (including the non-negative-cost invariant and
    persistence-failure resilience), `CallRecordingService`, and the three previously
    0%-covered repositories. This is real behavioral coverage of real code, not padding — see
    Remaining Issues for why these subsystems still aren't live.
17. Assorted pre-existing lint violations unrelated to the above (unsorted imports in
    `alembic/env.py`, two unused test locals, four over-length lines, one stale `noqa`) —
    fixed via `ruff --fix` / `black` / `isort` plus three manual line-wraps.

Coverage moved from **68.21% → 83.21%** (327 → 381 tests) as a direct result of #2, #11, and
#16 above — every added test exercises real, existing application code, not throwaway
assertions.

## Remaining Issues

These are architecture-level integration decisions, correctly out of scope for this audit
(no new features / no architecture changes without a required correction), but should be
resolved before this system is considered production-complete:

1. **Webhook signature verification is not applied on the live route.**
   `ExotelWebhookHandler._check_signature()` exists, is tested, and does the right thing — but
   `app/api/v1/telephony.py` never calls it. Anyone who can reach `/api/v1/telephony/incoming`
   can currently inject a fabricated call. Needs a decision on which real Exotel header carries
   the signature (or confirmation Exotel doesn't sign webhooks and this should rely on
   `EXOTEL_WEBHOOK_SECRET`-based shared-path-segment auth instead) before wiring it in.
2. **`CallOrchestrator` does not exist.** Call lifecycle logic is split correctly-enough
   between `app/api/v1/telephony.py`, `CallService`, and `SpeechHandler`, but there is no single
   owner of the full lifecycle described in the audit brief (telephony → conversation → voice →
   lead → DB → email → analytics). Works today because `SpeechHandler` internally does most of
   this; a dedicated orchestrator would mainly help once `EventBus`/cost-tracking/recording are
   wired in (below).
3. **`EventBus`, `UsageTrackingService` (cost tracking), and `CallRecordingService` are fully
   built and now fully tested in isolation, but not invoked from the real call path.** No call
   today publishes a single event, tracks a single cent of cost, or persists a call recording
   row, despite dedicated tables and services existing for all three. Wiring these in is real,
   deliberate integration work (deciding exactly where in `SpeechHandler`/`CallService` each
   `record_*`/`publish()` call belongs) and was intentionally not done here.
4. **`CallStateMachine` (`app/call/state.py`) is unused.** A complete, tested FSM matching the
   audit brief's state list exists but nothing constructs or transitions it.
5. **`CallRecordingService` has no enable/disable configuration flag**, and recording isn't
   wired in regardless (see #3) — needed once #3 is addressed.
6. **In-app rate limiting is absent**; relies entirely on Nginx being in front of the app. Fine
   for the production compose, not fine for any deployment path that exposes the app directly.
7. **Redis is provisioned (Docker, config, `.env.example`) but never used** by any application
   code. Either wire it in (e.g. as the rate-limit backend for #6) or remove it to reduce
   operational surface.
8. Full `docker compose up` (all services together, including Redis) could not be verified
   end-to-end in this sandboxed environment due to a pre-existing local port conflict on 6379
   unrelated to the project. Re-run `docker compose -f docker/docker-compose.dev.yml up` in a
   clean environment to confirm.
9. Bare `GET /health` (as opposed to `/api/v1/health`) does not exist. If any external
   monitoring or load balancer is configured to hit `/health`, it needs to target
   `/api/v1/health` instead, or a bare alias route should be added.

## External Credentials Required

Only variable names — no values were entered or requested:

- `SECRET_KEY`
- `OPENAI_API_KEY`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID_EN`
- `ELEVENLABS_VOICE_ID_HI`
- `EXOTEL_SID`
- `EXOTEL_TOKEN`
- `EXOTEL_API_KEY`
- `EXOTEL_API_SECRET`
- `EXOTEL_CALLER_ID`
- `EXOTEL_WEBHOOK_SECRET`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `JWT_SECRET_KEY`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD_HASH`
- `DATABASE_URL` (production value)

## Final Verdict

**READY FOR API CONFIGURATION**

The codebase is internally complete, imports cleanly, passes its full test suite (381/381) at
83% coverage, lints clean (Ruff/Black/isort), builds and runs correctly in Docker against a
real PostgreSQL database, and — critically — no longer has any bug that would silently corrupt
data or crash core paths (auth, webhook ingestion, migrations) once real credentials are added.
The unwired subsystems (EventBus, cost tracking, call recording, webhook signature
verification, orchestrator) are real, tested, and ready to be integrated as a deliberate next
phase — they do not block adding real API keys and exercising the core call → lead → email
pipeline, which was the objective of this audit.
