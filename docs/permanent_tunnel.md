# Permanent public URL: set Exotel once, never again

## Why the URL kept changing

`cloudflared tunnel --url http://localhost:8000` starts a **quick tunnel**.
Each start gets a new random `https://xxxx.trycloudflare.com` address, so
every restart meant copying the new URL into the Exotel Voicebot applet.

The fix is a **named tunnel** on a hostname you own, for example
`https://ivr.yourdomain.com`. The hostname is fixed. You paste it into
Exotel one time, and it keeps working after restarting the tunnel, the app
or the PC.

You need a domain whose DNS is on Cloudflare (the free plan works). Any
domain you own will do, because you only use one sub-domain such as
`ivr.evolkun.com`. If you have no domain on Cloudflare, use Option C (ngrok).

---

## Option A (recommended): runs as a Windows service, no terminal needed

1. Go to https://one.dash.cloudflare.com, then **Networks > Tunnels > Create a tunnel**.
2. Choose **Cloudflared**, name it `kinikh-ivr`, then pick **Windows**.
3. Copy the command shown (`cloudflared.exe service install eyJ...`) and run it in
   an **Administrator** PowerShell. cloudflared is now a Windows service that
   starts with Windows and reconnects on its own.
4. Under **Public Hostname**, add:
   - Subdomain `ivr`, Domain `yourdomain.com`
   - Service **HTTP**, URL `localhost:8000`
5. In `.env`, set `PUBLIC_BASE_URL=https://ivr.yourdomain.com`.

From then on you only start the backend (`scripts\windows\start-ivr.ps1` or
your usual uvicorn command). The tunnel is always on.

## Option B: scripted setup (same result, from the command line)

```powershell
# one time
powershell -ExecutionPolicy Bypass -File scripts\windows\setup-permanent-tunnel.ps1 -Hostname ivr.yourdomain.com

# every day: starts the backend and the tunnel in two windows
powershell -ExecutionPolicy Bypass -File scripts\windows\start-ivr.ps1
```

The setup script logs in to Cloudflare, creates the `kinikh-ivr` tunnel,
creates the DNS record, writes the tunnel config, saves `PUBLIC_BASE_URL`
into `.env`, prints the exact Exotel URLs and copies the Voicebot URL to
your clipboard. `start-tunnel.ps1` restarts the tunnel automatically if it
drops.

## Option C: no domain? Use an ngrok static domain (free)

1. Sign up at ngrok.com and run `ngrok config add-authtoken <token>`.
2. Dashboard > **Domains**: claim your free static domain (e.g. `kinikh-ivr.ngrok-free.app`).
3. In `.env`, set `NGROK_DOMAIN=kinikh-ivr.ngrok-free.app`.
4. Run `scripts\windows\start-ivr.ps1`. It uses ngrok when no Cloudflare tunnel is configured.

---

## Exotel configuration (one time)

In **App Bazaar > your flow**:

1. **Voicebot applet**, URL:
   `https://ivr.yourdomain.com/api/v1/telephony/incoming?webhook_token=<EXOTEL_WEBHOOK_SECRET>`
   (the setup script prints this with your real secret filled in).
2. Add a **Hangup** applet **after** the Voicebot applet. When the assistant
   finishes a call (or the caller stays silent), the app closes the voice
   stream and Exotel continues to the next applet, which should end the call.
3. Optional: set the call status callback to
   `https://ivr.yourdomain.com/api/v1/telephony/status?webhook_token=<same secret>`.

Check it works by opening `https://ivr.yourdomain.com/api/v1/health` in a
browser. It should respond while the backend is running.

## Why `PUBLIC_BASE_URL` matters

When Exotel calls `/incoming`, the app replies with the WebSocket address
Exotel should stream audio to. With `PUBLIC_BASE_URL` set, that address is
always `wss://ivr.yourdomain.com/...`. Without it, the app has to guess from
proxy headers, and a wrong guess (`ws://` instead of `wss://`) silently
breaks calls.
