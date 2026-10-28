# Live call transfer

The voice assistant can hand a caller to a person during office hours. The
handover happens in the Exotel flow, after the assistant's media stream has
closed. Base Voicebot setup is covered in
[exotel_voicebot_integration.md](exotel_voicebot_integration.md).

## Exotel flow

```
Call Start → Voicebot → Passthru ─┬─ 200 OK → Connect → Hangup
                                   └─ other  → Hangup
```

1. **Voicebot** runs the AI conversation. When the caller wants a person, the
   assistant says one short line ("Ek moment, main aapko hamari team se
   connect kar rahi hoon") and ends its reply with `[TRANSFER:<area>]`. The
   marker is never spoken. The app saves `transfer_requested`,
   `transfer_department` and `transfer_number` on the call, then closes the
   stream.
2. **Passthru** calls `transfer-check`. It gets `200` only for a call marked
   for transfer, so every other call goes to Hangup as before.
3. **Connect** dials the number from `transfer-number`, or a number fixed in
   the applet.

The lead summary and email are still sent after a transferred call.

## URLs

Replace `<host>` with the public hostname and `<secret>` with
`EXOTEL_WEBHOOK_SECRET`. All three accept GET or POST and read `CallSid` from
the query string or form body.

| Applet | URL | Response |
| --- | --- | --- |
| Voicebot | `https://<host>/api/v1/telephony/incoming?webhook_token=<secret>` | Stream URL (`url`) |
| Passthru | `https://<host>/api/v1/telephony/transfer-check?webhook_token=<secret>` | `200 {"transfer": true}` if marked; `404 {"transfer": false}` if not marked or unknown; `400` without `CallSid`; `401` with a wrong token |
| Connect (dynamic number) | `https://<host>/api/v1/telephony/transfer-number?webhook_token=<secret>` | Plain text number, e.g. `+919761615375` |

`transfer-number` returns the number saved on the call, else the number for
its business area, else `TRANSFER_DEFAULT_NUMBER`. An unknown or missing
`CallSid` also gets the default, so the Connect applet always has a number to
dial.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRANSFER_ENABLED` | `true` | `false` makes the assistant always offer a callback instead |
| `TRANSFER_DEFAULT_NUMBER` | `+919761615375` | Number for `general` and any business area not listed |
| `TRANSFER_NUMBERS` | empty | `infrastructure=+91XXXXXXXXXX,hospitality=+91XXXXXXXXXX` (area names are case-insensitive) |
| `TRANSFER_HOURS` | `Mon-Sat 10:00-19:00` | India time. Several windows are allowed: `Mon-Fri 10:00-19:00, Sat 10:00-14:00` |

Outside `TRANSFER_HOURS`, the assistant tells the caller the team is not
available right now and offers a callback. It never promises a transfer.
A `TRANSFER_HOURS` value that can't be parsed disables transfers and logs a
warning.

## Logs

- `call.transfer.requested`: call_sid, department and a masked number, logged
  when the assistant asks for a transfer.
- `exotel.webhook.received` with `kind=transfer_check` or
  `kind=transfer_number`: Exotel calling the two URLs.
