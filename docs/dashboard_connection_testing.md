# Integration Connection Testing

Real, on-demand, non-billable connection tests for the four external
integrations (OpenAI, ElevenLabs, Exotel, SMTP), triggered from the
Settings page's **Test Connection** buttons.

## Architecture

```
Settings.tsx (button click)
  -> POST /api/v1/system/integrations/{provider}/test
    -> IntegrationConnectionService.test(provider)   [app/services/integration_test_service.py]
       1. reload_settings()                          [app/core/config.py]
       2. is_provider_configured(provider, settings)  -> not_configured, short-circuit
       3. real lightweight authenticated call to the provider
       4. map any exception to a fixed safe message
       5. record result in the in-process cache
    -> IntegrationTestResult {provider, status, message, tested_at}
  <- UI updates immediately (React Query cache invalidation, no reload)

GET /api/v1/system/status
    -> for each provider: configured (live) + cached last-test result
       -> not_configured | not_verified | connected | failed
```

No duplicate configuration system was introduced. `Settings` (pydantic
`BaseSettings`, `app/core/config.py`) remains the single source of
configuration; everything here is additive to it.

## Configuration reload — no restart required

**Problem this solves**: `get_settings()` is `@lru_cache`d — once loaded, a
running process never re-reads `.env`. Before this feature, fixing a
credential in `.env` required a full backend restart before the dashboard
would reflect it.

**Fix**: `app/core/config.py::reload_settings()`

```python
def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
```

This is the *only* place that ever clears the cache. Every other call site
in the codebase still calls plain `get_settings()`, which stays cached —
so ordinary request handling, and the live call pipeline, never see
configuration change out from under them except when this is explicitly
invoked. `IntegrationConnectionService.test()` calls it first, on every
test run, before doing anything else — so `.env` is always read fresh at
the moment "Test Connection" is clicked, with zero restart of FastAPI, the
dashboard, the database, or any background service.

Verified live in this session: `SMTP_PORT` was changed in `.env` from `465`
to `9999` **without restarting the backend process**; the next "Test
Connection" call for Email immediately produced `ConnectionRefusedError`
(port `9999` has nothing listening) instead of the previous result —
proof the change was picked up. Restoring `465` and testing again
immediately produced the original result. See `docs/dashboard_browser_qa.md`-
style verification notes below under "Manual test."

**What is *not* affected**: long-lived provider client singletons that read
settings once at construction (`get_openai_client()`'s module-level
singleton in `app/ai/openai_client.py`) are never reused by the connection
test — each test constructs its own short-lived client directly from the
freshly-reloaded `Settings` object, so testing a credential never swaps out
the client objects the live voice/call pipeline is using mid-flight.

## API

- `POST /api/v1/system/integrations/{provider}/test` — `provider` is a
  path-level enum (`openai` | `elevenlabs` | `exotel` | `email`); an
  unlisted value is rejected by FastAPI/Pydantic before any code runs
  (`422`). Requires the same bearer auth as every other endpoint. Returns:
  ```json
  {"provider": "openai", "status": "connected", "message": "Connection successful.", "tested_at": "2026-...T...Z"}
  ```
  `status` is one of `connected` | `failed` | `not_configured` — never
  `"connected"` for an unconfigured provider, and `"not_configured"` is
  never confused with `"failed"` (they mean different things: no credential
  to test at all, vs. a credential that was tested and rejected/unreachable).

- `GET /api/v1/system/status` — extended, not replaced. Each provider now
  reports `configured: bool` (cheap, live, no I/O — a local string
  comparison against known dev-mock placeholders) plus `status` derived
  from the **last explicit test result**, never a guess:
  - not configured -> `"not_configured"` (regardless of any older cached
    test result — a live "not configured" always wins over a stale cache)
  - configured, never tested -> `"not_verified"`
  - configured, last test succeeded -> `"connected"`
  - configured, last test failed -> `"failed"`

  `/system/status` never calls a provider API itself — it only reads the
  in-process cache `IntegrationConnectionService` writes to. Polling this
  endpoint is always cheap and never consumes provider credits.

## Result cache

`app/services/integration_test_service.py` keeps a plain in-process
`dict[str, IntegrationTestResult]`, one entry per provider — explicitly not
Redis (nothing else in this codebase uses Redis; introducing it for this
alone would be new infrastructure for no benefit) and not a database table
(a restart resetting every provider to "not yet verified this session" is
correct behavior, not something to work around with persistence). Holds
only `provider`, `status`, `message`, `tested_at` — never a credential.

## Security

- The browser never receives an API key, token, password, connection
  string, or Authorization header — verified by grepping the compiled
  frontend bundle for every known secret name/value (see
  `docs/dashboard_browser_qa.md`) and by every backend test in
  `tests/test_integration_test_service.py` asserting the returned message
  never contains a canary secret string, even when the underlying exception
  message did.
- Every provider exception is mapped to one of a small set of fixed,
  human-readable messages. Only the exception's **type name** is ever
  logged (`type(exc).__name__`) — never `str(exc)`, because SDK/HTTP
  exceptions can embed request/response details (headers, URLs) that carry
  credentials. Confirmed live: an `SMTPAuthenticationError` and a
  `ConnectionRefusedError` both surfaced in backend logs by type name only.
- Exotel's Basic Auth is passed via the URL's userinfo component
  (`https://api_key:api_token@host/...` — API Key as username, API Token as
  password, per Exotel's documented REST auth; the same convention the
  existing `ExotelClient._request` already uses) — `ExotelClient.test_connection()`
  never logs that URL.

## Provider-specific test behavior

| Provider | Operation | Cost | Distinguishes |
|---|---|---|---|
| OpenAI | `GET /v1/models` (list models) via a throwaway `AsyncOpenAI` client | Free, no tokens | auth failure vs. permission-denied vs. network |
| ElevenLabs | `GET /v1/user` with `xi-api-key` header | Free (no synthesis) | auth failure vs. other HTTP status |
| Exotel | `GET /v1/Accounts/{sid}.json` (account read), new `ExotelClient.test_connection()` bypassing the retry wrapper to preserve the HTTP status code | Free, no call/SMS/ExoPhone action | 401/403 (auth) vs. 404 (bad SID) vs. other (authenticated but account/telephony config incomplete) |
| Email | Connect + (implicit-TLS or STARTTLS, whichever the port implies) + login + `NOOP`, new `SMTPClient.test_connection()` | Free, no message sent | `SMTPAuthenticationError` vs. any other connection/TLS error |

None of these ever performs the real, billable operation (chat completion,
TTS synthesis, phone call/SMS, or email send).

## A note on what "safe" tests can and can't prove

Found live in this session, not fabricated:

- **ElevenLabs**: the configured API key returned `401` on `/v1/user`, and
  independently on `/v1/voices` and `/v1/models` too — the key's error body
  says it's missing `user_read`/`voices_read`/`models_read` permissions.
  This looks like a narrowly-scoped key (likely restricted to
  `text_to_speech` only, which is good security practice). There is no
  free ElevenLabs endpoint a TTS-only-scoped key can pass — proving it
  actually works would require calling the real synthesis endpoint, which
  Phase 13's own constraints ("do not consume voice credits unnecessarily")
  rule out. **A "failed" result here does not necessarily mean the key is
  bad for real use** — it means the key can't pass this specific
  non-billable check. This is a real, inherent trade-off of non-billable
  testing, not a bug in the test.
- **Email**: found and fixed a real pre-existing bug while building this —
  port `465` is implicit TLS (SMTPS) and requires `smtplib.SMTP_SSL`, not
  `smtplib.SMTP` + `starttls()` (that sequence is for port `587`/`25` and
  simply times out against a `465` listener — confirmed live, the test took
  ~10s before the fix and <0.1s after). **This same bug exists in the real
  send path** (`SMTPClient._send_sync`, used by `EmailSender` for actual
  lead-notification emails) and was *not* fixed there, since it's outside
  this task's explicit scope (adding a Test Connection button, not
  modifying the send pipeline) — flagged here so it isn't silently lost.
  After the fix, this environment's actual result is
  `SSLCertVerificationError: unable to get local issuer certificate` —
  a local machine CA-trust-store gap (common on macOS Python without a
  proper certificate bundle installed), not necessarily invalid
  credentials. The returned message ("Could not connect to the SMTP
  server...") correctly does *not* claim this is an authentication
  failure, since it isn't one.
- **Exotel**: found and fixed a real pre-existing bug in a follow-up
  session — `ExotelClient` was building Basic Auth as `api_key:api_secret`,
  but Exotel's documented REST auth for this API is `api_key:api_token`
  (Account SID + API Key + API Token; there is no separate "API Secret" in
  Exotel's standard API Credentials screen). `EXOTEL_API_SECRET` is now
  deprecated (optional, unused by any code) and `EXOTEL_TOKEN` — previously
  read into `Settings` but never actually used anywhere — is the real Basic
  Auth password. `PROVIDER_FIELDS["exotel"]` was also narrowed to
  `[exotel_sid, exotel_api_key, exotel_token]`: `exotel_caller_id`
  (outbound-call-only) and `exotel_webhook_secret` (an app-generated secret,
  never an Exotel credential) no longer gate whether Exotel shows as
  "configured" — they were never relevant to REST API authentication.

## How to update `.env` and test without restarting

1. Edit the relevant field(s) in `.env` (e.g. `SMTP_PASSWORD`,
   `OPENAI_API_KEY`).
2. Do **not** restart the backend, the dashboard, or the database.
3. In the dashboard's Settings page, click **Test Connection** on the
   affected integration's card.
4. The click reloads configuration from `.env` at that instant
   (`reload_settings()`), runs the real check, and updates that card's
   status immediately — no page reload.
