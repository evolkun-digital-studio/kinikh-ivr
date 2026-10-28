# API Reference

Base path: `/api/v1`. All endpoints except `/health`, `/auth/login`,
`/auth/refresh`, and the `/telephony/*` webhooks require
`Authorization: Bearer <access_token>`.

## Auth

- `POST /auth/login` — `{email, password}` → `{access_token, refresh_token, token_type}`.
- `POST /auth/refresh` — `{refresh_token}` → new token pair.

## Health / System

- `GET /health` — DB connectivity + version. Unauthenticated.
- `GET /system/status` — environment, app version, DB status, and per-provider
  configuration status (`openai`, `elevenlabs`, `exotel`, `email`), each
  `"connected"` or `"configuration_pending"`. Never fabricates "connected" —
  computed from whether the live setting still matches a known dev-mock
  placeholder value.

## Calls

- `GET /calls` — query params: `status`, `department_id`, `language`, `search`
  (matches caller number), `date_from`, `date_to`, `sort`
  (`created_at`|`duration_seconds`|`status`|`caller_number`), `order`
  (`asc`|`desc`), `limit` (1–200, default 50), `offset`. Returns
  `{items, total, limit, offset}`.
- `GET /calls/{id}` — full detail: call fields, `lead` (brief, incl.
  department name + language), `transcript`, `error_message`, `cost`
  (telephony/openai/voice/email/total breakdown), `recording` (id, path,
  status, duration).

## Leads

- `GET /leads` — query params: `department_id`, `search` (name/phone/email),
  `date_from`, `date_to`, `sort` (`created_at`|`name`), `order`, `limit`,
  `offset`. Returns `{items, total, limit, offset}`.
- `GET /leads/{id}` — full detail incl. `department`, `call`, `email_logs`,
  `additional_notes`, `raw_transcript`.

## Departments

- `GET /departments` — all departments (active + inactive), each with
  `call_count`/`lead_count`.
- `POST /departments` — create. Body: `name`, `display_name`, `email`,
  `greeting?`, `prompt?`, `knowledge_base?`, `keywords?`.
- `PATCH /departments/{id}` — partial update, same body shape.
- `POST /departments/{id}/activate` — reactivate.
- `DELETE /departments/{id}` — deactivate (soft; no hard delete).

## Recordings

- `GET /recordings` — query params: `status`, `department`, `search` (caller
  number), `date_from`, `date_to`, `limit`, `offset`. Each item exposes only
  `has_audio: bool` — never the underlying filesystem path.
- `GET /recordings/{id}` — adds `transcript`, `summary`, `errors`.
- `GET /recordings/{id}/audio` — streams the recording's audio file.
  Authenticated like every other endpoint here. Resolves
  `CallRecording.audio_file_path` (always relative, never an absolute path)
  against `settings.recordings_dir` (`app/core/media.py`), rejecting path
  traversal, absolute paths, and disallowed extensions — any of those, a
  missing recording, or a missing file on disk all return a plain `404` with
  no distinguishing detail. Supports `Range` requests (206 Partial Content)
  for browser seeking, via Starlette's `FileResponse`. The frontend never
  points an `<audio src>` at this URL directly (it can't attach the bearer
  header) — it fetches the file with `fetch()` + `Authorization` header and
  plays it from an object URL instead.

## Emails

- `GET /emails` — query params: `status`, `department_id`, `search`
  (recipient email), `date_from`, `date_to`, `limit`, `offset`. Each item
  includes the linked lead's name/phone/department when present.

## Usage & Cost

- `GET /usage/summary` — `totals` (per-category + total cost, call count,
  cost-per-call) and `by_provider` (requests, tokens, characters, cost per
  API provider).
- `GET /usage/timeseries?days=30` — daily cost totals for the last N days.

## Analytics

- `GET /analytics/overview?days=30` — `calls_over_time`, `leads_over_time`,
  `calls_by_department`, `calls_by_language`, `completed_vs_failed`,
  `email_status_breakdown`, `cost_over_time`.

## Dashboard

- `GET /dashboard/summary` — `kpis` (total/completed/failed calls, total
  leads, today's calls/leads, total cost, average call duration) plus the 5
  most recent calls and leads.

## Telephony (webhooks — not used by the dashboard)

- `POST /telephony/incoming`, `POST /telephony/status`, and the audio
  WebSocket stream. Exotel-facing; unauthenticated, HMAC-validated where
  applicable. See `app/telephony/exotel/`.

## Error shape

Non-2xx responses (except FastAPI's own validation errors) use:

```json
{"error": {"code": "NOT_FOUND", "message": "Call '...' not found", "details": {}}}
```
