# Exotel Voicebot Integration — Endpoints & Flow Configuration

Written after the first real end-to-end call test showed the AI pipeline was
never reached. This documents the three distinct roles Exotel's Voicebot
Applet needs, which endpoint currently plays each role, and exactly what
must be configured in Exotel's own Flow builder (external to this repo —
nothing here can verify or fix that side).

## The three roles

| Role | Endpoint | Status |
|---|---|---|
| **A. Incoming call webhook** — Exotel tells us a call arrived | `POST /api/v1/telephony/incoming` | Live, working (verified: creates a `Call` row) |
| **B. Voicebot dynamic URL resolver** — Exotel asks "where do I open the media WebSocket?" | Currently the **same endpoint as A** — `POST /api/v1/telephony/incoming` | Response contract was wrong until this fix (see below) |
| **C. Media WebSocket** — the actual bidirectional audio stream | `WS /api/v1/telephony/stream` | Live, protocol-correct for `connected`/`start`/`media`/`dtmf`/`stop` |

**A and B are not necessarily meant to be the same endpoint** — this
codebase only has one endpoint that returns anything resembling a resolver
response, so it's the only candidate here, but **only Exotel's Flow
configuration can confirm** whether the Voicebot Applet's "HTTP(S) dynamic
URL" is actually pointed at `/api/v1/telephony/incoming`, or at some other
URL not implemented in this codebase at all. This must be checked in
Exotel's dashboard, not assumed from code.

## Fix applied: response contract (Task 2)

`POST /api/v1/telephony/incoming` previously returned:
```json
{"statusCode": 200, "message": "Call accepted", "streamUrl": "https://.../telephony/stream"}
```
Exotel's Voicebot Applet dynamic resolver contract reads a field literally
named **`url`**, not `streamUrl` — so even if Exotel was correctly calling
this endpoint, it had no way to learn where to open the WebSocket. Fixed in
`app/api/v1/telephony.py::_build_stream_url` — the response now includes
both `url` (what Exotel needs) and `streamUrl` (kept for backward
compatibility, harmless extra field).

## Known remaining gap: scheme correctness (`ws` vs `wss`)

The fix derives `wss`/`ws` from the *scheme FastAPI itself observed on the
inbound request*. This is only correct if the deployment's reverse proxy
is configured to pass `X-Forwarded-Proto` **and** uvicorn is told to trust
it. Checked in this session:

- `docker/nginx.conf` **does** correctly set `proxy_set_header
  X-Forwarded-Proto $scheme;` on every relevant location block.
- `docker/Dockerfile`'s uvicorn command does **not** pass `--proxy-headers`
  (or `--forwarded-allow-ips`), so uvicorn ignores that header entirely.

Net effect: if this app is deployed behind that nginx config as-is, a
publicly-HTTPS request would still cause `request.url.scheme` to read
`http` (the plaintext scheme of the internal nginx→app hop), so the
resolver would return `ws://` instead of the `wss://` Exotel's cloud
almost certainly requires for a public-internet WebSocket. **Not fixed in
this change** — it's a deployment-config change (`--proxy-headers
--forwarded-allow-ips=...`), which is out of scope here ("do not solve
deployment automatically"). Flagging it explicitly rather than leaving it
to be rediscovered the same way the `url`/`streamUrl` bug was.

## Public reachability (Task 3) — STOP condition

**No public URL is configured anywhere in this repository** — checked
`.env`, `.env.example`, `docs/*.md` (`docs/deployment.md` and
`docs/architecture.md` are both empty files). `docker-compose.yml` +
`docker/nginx.conf` describe an *intended* production deployment (nginx
terminating TLS in front of the FastAPI app), but there is no evidence in
this session that it is actually running anywhere with a real domain.

The backend this session has been testing against runs on plain
`localhost:8000` with no reverse proxy, no TLS, and no tunnel (confirmed:
no ngrok/cloudflared/localtunnel process running). Exotel's cloud
infrastructure **cannot reach `localhost`** under any circumstance.

> **Exotel cannot reach the current backend because it is not publicly
> reachable.**

This alone would fully explain every symptom from the first real call —
independent of the `url`/`streamUrl` bug above. **Before another real call
test, confirm: (1) what URL is actually configured in Exotel's Flow for
this ExoPhone, and (2) is a real, publicly-reachable instance of this
backend actually running at that URL right now** (a deployed server, or a
temporary tunnel like ngrok pointed at this dev machine). Neither can be
determined from this codebase.

## WebSocket path & protocol (Task 4)

`WS /api/v1/telephony/stream` is correct and unchanged. `ExotelAudioStream`
(`app/telephony/exotel/stream.py`) handles `connected`, `start`, `media`,
`dtmf`, `stop`. It does **not** have explicit handling for `mark`/`clear`
events (a Twilio Media Streams concept for audio-interruption
acknowledgment) — unrecognized event types are silently ignored (no crash).
Exotel's Voicebot Applet documentation was not confirmed in this session to
require `mark`/`clear` at all; not adding speculative handling for an
unconfirmed event type. If Exotel's docs do specify these events, they are
currently a safe no-op, not a broken path.

## Caller number passing (Task 5)

`SpeechHandler`/`ExotelAudioStream` read the caller's number from
`start.customParameters.From` in the WebSocket protocol's `start` event.
Exotel's documentation (per this task) states custom values can *instead*
be passed as query parameters on the WSS URL itself. **This codebase now
supports both**, without requiring either:

- `app/api/v1/telephony.py`'s `/stream` route reads `?from=` (or `?From=`)
  from the WebSocket URL's query string as a fallback.
- If `start.customParameters.From` is present, it wins (existing behavior
  preserved).
- If neither is present, initialization proceeds anyway with an empty
  caller number — **confirmed this was already true before this change**
  (`ExotelAudioStream` only requires `call_sid` to fire `on_start`, never
  `caller_number`) and remains true now. The caller number is not, and must
  not be, mandatory for the WebSocket connection or the AI greeting to
  proceed.

**Exact Flow configuration required, if you want the caller number passed
via the URL** (skip this if you configure a `customParameter` named `From`
in the Voicebot Applet instead — either mechanism works): when the
Voicebot Applet's dynamic resolver returns the `url` field, append the
caller's number as a query parameter, e.g.
`wss://your-domain.com/api/v1/telephony/stream?from={CallFrom}` — using
whatever token-substitution syntax Exotel's Flow builder provides for the
caller's number at that point (verify the exact token name against
Exotel's current documentation; this repo cannot confirm Exotel's macro
syntax). This app already reads `from` off that URL — no further backend
change is needed to support it.

## Local test performed (Task 6)

```
$ curl -X POST http://localhost:8000/api/v1/telephony/incoming \
    -d "CallSid=CA_RESOLVER_TEST&From=%2B919876543210&To=%2B910000000000&CallStatus=ringing"

{"statusCode": 200, "message": "Call accepted",
 "url": "ws://localhost:8000/api/v1/telephony/stream",
 "streamUrl": "ws://localhost:8000/api/v1/telephony/stream"}
```

`url` present, correctly suffixed `/api/v1/telephony/stream`, no
credentials in the body. The synthetic test `Call` row was deleted
immediately after. Automated regression coverage:
`tests/test_telephony_webhook_validation.py` (`url` field present, correct
suffix, no leaked credentials, `ws`/`wss` scheme derivation) and
`tests/test_telephony_stream_query_params.py` (query-param fallback,
customParameter priority, caller number never mandatory).
