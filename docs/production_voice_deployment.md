# Production voice deployment

## Webhook authentication

Exotel does not sign its webhooks, and its Voicebot dynamic URL resolver calls
`/api/v1/telephony/incoming` with **GET** (`CallSid`, `CallFrom`, `CallTo` in
the query string). Both `/incoming` and `/status` accept GET and POST and
reject (HTTP 401) any request that does not prove knowledge of
`EXOTEL_WEBHOOK_SECRET` in one of these forms:

1. `?webhook_token=<secret>` on the URL configured in the Exotel flow, e.g.
   `https://<host>/api/v1/telephony/incoming?webhook_token=<secret>` — the
   form Exotel can send directly;
2. HTTP Basic auth whose password is the secret;
3. `X-Exotel-Signature`: lowercase hex HMAC-SHA256 of the unmodified request
   body, for a trusted signing proxy. A present-but-wrong signature is always
   rejected, even if a valid token is also supplied.

Exotel recommends not relying on a URL secret alone, so also set
`EXOTEL_ALLOWED_CIDRS` to Exotel's published egress ranges. The bundled nginx
overwrites `X-Forwarded-For` with the real peer address so this check cannot
be spoofed; if another proxy/CDN sits in front of nginx, adjust that first.
nginx logs `/api/v1/telephony/*` without query strings, and the application
removes `webhook_token` before parsing or logging a payload. Generate the
secret with `openssl rand -hex 32` and keep it out of source control.

Unrecognised `CallStatus`/`Status` values are rejected with 422 rather than
stored. Failure outcomes (`busy`, `no-answer`, `failed`, `canceled`) are never
overwritten by the stream's own end-of-call `completed`, whichever arrives
first, and neither completion path can blank out fields the other stored.

## Media stream

`/api/v1/telephony/incoming` returns a short-lived (`STREAM_TOKEN_TTL_SECONDS`, default 60), call-bound token in
the WebSocket URL. The WebSocket route verifies it before accepting, loads the
call from PostgreSQL, rejects terminal calls, and checks the `start.call_sid`
and supplied account SID before starting AI work. Treat this URL as a bearer
credential. The bundled Nginx configuration disables access logging for the
WebSocket location so the token query parameter is not recorded there. A
second concurrent stream for the same call is refused (per worker process).

## Live conversation

Caller audio is always monitored, including while the assistant speaks.
Speech over audible assistant audio must pass the stricter barge-in detector
(`BARGE_IN_MIN_RMS_ENERGY`, `BARGE_IN_MIN_ACTIVE_MS`, `BARGE_IN_GAP_TOLERANCE_MS`)
so line echo does not interrupt; confirmed speech cancels the reply, sends
Exotel a `clear` event, and becomes the caller's next turn. Replies are
streamed to TTS sentence by sentence and paced at most `PLAYBACK_MAX_LEAD_MS`
ahead of playback. Each turn logs one `voice.turn.metrics` line with STT,
LLM time-to-first-token, TTS startup and end-of-turn to first-audio latency.

## Deployment

Follow the [production runbook](../README.md) for server setup, GitHub CI/CD,
backups and rollback. Use one worker and drain calls before deployment.

Set `DB_PASSWORD` and `TLS_CERT_DIR` before running the production Compose
file. `TLS_CERT_DIR` must contain `fullchain.pem` and `privkey.pem`. The
deployment waits for Alembic migrations before starting FastAPI. Nginx serves
the React build at `/`, proxies `/api/*`, and upgrades the existing
`/api/v1/telephony/stream` route. The dashboard uses the same origin for API
requests in this build.

The Exotel Voicebot dynamic URL resolver must target the public HTTPS
`/api/v1/telephony/incoming?webhook_token=<secret>` endpoint. Configure the
status callback the same way (`/api/v1/telephony/status?webhook_token=<secret>`). The returned stream URL needs to
remain intact, including its `token` query parameter. Verify the proxy's
`X-Forwarded-Proto` and `Host` forwarding with a public call; the API
generates `wss://` from the trusted HTTPS scheme.

Provider-hosted recording URLs are stored as metadata. The application does
not download recording audio. The authenticated `/recordings/{id}/audio`
endpoint serves only local audio files, so provider-hosted recordings are
listed without local playback.
