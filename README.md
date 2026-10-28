# Kinikh IVR

AI phone receptionist with a FastAPI backend, React/TypeScript operator dashboard,
PostgreSQL, Redis, Exotel telephony, OpenAI speech/conversation, optional ElevenLabs
voices, and SMTP notifications.

The repository includes a **single-server production deployment baseline** and GitHub
Actions CI/CD. Infrastructure, real provider credentials, DNS, certificate issuance,
and the live acceptance checks below must be completed before serving customers.
This is a single-operator application; the configured admin account has dashboard
access. Multi-user roles, high availability, and uninterrupted deployments require
additional application work.

## What you need for production

| Requirement | Starting configuration / action |
| --- | --- |
| Server | Linux x86-64 VPS; Ubuntu 24.04 LTS is a suitable starting point. Budget **2 vCPU, 4 GB RAM, 40 GB SSD** for a small pilot; **4 vCPU, 8 GB RAM, 80 GB SSD** gives more headroom. These are planning estimates, not measured call capacity. |
| Domain | Dedicated hostname such as `ivr.company.com`, DNS A record to the VPS, and HTTPS certificate. Add AAAA only if IPv6 is configured. |
| Runtime | Docker Engine, Docker Compose v2.20+ (or v5), Git, OpenSSL, `flock` (util-linux), Certbot, and SSH. Docker access effectively grants root access; restrict the deploy user. |
| Network | Public inbound TCP 80/443; restrict SSH 22 to your administration source where practical. No public ports for PostgreSQL, Redis, or FastAPI. Outbound HTTPS to providers, DNS, and SMTP submission to your mail service. |
| Exotel | Active account, phone number, Voicebot/AgentStream capability, account SID, API key/token, correct regional API hostname, and configured callbacks. Confirm concurrency and provider limits with your account. |
| OpenAI | Funded project, API key, access to configured conversation/transcription/TTS models, and sufficient rate limits. Validate the selected models using your account. |
| Voice | ElevenLabs API key and EN/HI voice IDs if `TTS_PROVIDER=elevenlabs`; set `TTS_PROVIDER=openai` to use OpenAI TTS. |
| Email | SMTP account, TLS-enabled submission, verified sending domain/address, and working delivery to your team. |
| Secrets | Independent application/JWT/webhook secrets, random database password, admin email, and a unique bcrypt password hash. |
| Delivery | GitHub repository with Actions enabled, GHCR image access, and an SSH deploy key plus verified host key. |
| Operations | External uptime alerts, disk/CPU/RAM monitoring, provider budget alerts, encrypted off-server backups, and tested restores. |
| Data handling | Decide access, retention and deletion for phone numbers, transcripts, recordings, logs, and backups; configure caller notices/consent appropriate to your operation. |

Choose a server region close to your callers and telephony/media providers. Measure
concurrent real calls and voice latency before promising capacity. Audio processing
and provider rate limits determine capacity more than dashboard traffic.

## Architecture and limits

```text
Browser / Exotel HTTPS and WSS
               |
          Nginx :443  -- React static dashboard
               |
         FastAPI :8000 (one worker)
           |        |       |
       PostgreSQL Redis   External AI / Exotel / SMTP
```

Production Compose exposes only 80/443. It persists PostgreSQL, Redis, local
recordings/RAG data, and application logs in named volumes. The backend runs as a
non-root user. Docker logs rotate. Nginx applies TLS, login/API throttling, query-free
access logs, and long-lived WebSocket forwarding. App readiness returns HTTP 503
when PostgreSQL cannot be queried; this does **not** prove provider or Redis health.

Keep **one backend replica and one worker**. Active calls and some duplicate-stream
protection are process-local. Increasing replicas needs shared call ownership,
reconnection/draining design, shared recording storage, and load testing.
Deployment restarts the backend and can interrupt calls: pause new calls at Exotel,
wait for active calls and finalization to finish, and use a maintenance window.
Provider-hosted recording URLs are metadata; the app does not download those audio
files. Local audio playback requires files in the recordings volume.

## Local development

Use Python 3.12 and Node.js 22.12+ to match CI/container builds.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env for development and provider settings; never commit it.
docker compose -f docker/docker-compose.dev.yml up -d db redis
# Use the dev database credentials from docker/docker-compose.dev.yml in .env.
# Alembic reads DATABASE_URL from the process environment, not automatically from .env:
export DATABASE_URL=postgresql+asyncpg://kinikh:password@localhost:5432/kinikh_ivr
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd dashboard
npm ci
cp .env.example .env
npm run dev
```

Dashboard: `http://localhost:3000`; API: `http://localhost:8000`.
Development mock credentials allow startup, but cannot complete real calls.

```bash
APP_ENV=testing python -m pytest tests/ -q
cd dashboard
npm run lint
npm test
npm run build
```

## VPS deployment: first setup

For an existing Compose installation, inspect its project/volume names and back it
up before switching configurations. Set `COMPOSE_PROJECT_NAME` in `.env` to the
original project name if needed to reuse its database volume. This baseline uses
named storage/log volumes; migrate any existing host-directory recordings/logs
into those volumes before cutover. Never assume a new empty database is your old one.

1. Provision Linux x86-64, configure SSH key access/firewall, and install Docker
   Engine with Compose, Git, OpenSSL, Certbot and util-linux. Use the vendor's
   installation instructions for your distribution. Ensure the deploy user can run
   Docker without an interactive password. Keep OS security updates enabled.
2. Create DNS for your hostname. Open 80/443 in both provider and host firewalls.
   Keep the VPS hostname direct to Nginx initially. A CDN/load balancer requires
   explicit trusted-client-IP configuration before enabling webhook CIDR checks.
3. Clone this repository to `/opt/kinikh-ivr`, owned by the deploy user. Private
   repositories need a **read-only repository deploy key** on the VPS for fetches.
   Keep it separate from the GitHub Actions SSH key that logs into the VPS.

   ```bash
   git clone YOUR_REPOSITORY_URL /opt/kinikh-ivr
   cd /opt/kinikh-ivr
   cp docker/production.env.example .env
   chmod 600 .env
   openssl rand -hex 32
   # Run again separately for each secret and DB_PASSWORD; put values in .env.
   ```

4. Edit `.env`. Replace every `CHANGE_ME` value and example hostname/address.
   `IMAGE_REPOSITORY` is `ghcr.io/<lowercase-owner>/<lowercase-repository>` **without**
   `-app`, `-web`, or tag; CI adds those. Use a hex-only `DB_PASSWORD` to avoid URL
   escaping problems. Set `PUBLIC_BASE_URL` to your HTTPS origin, and
   `CORS_ORIGINS` to the JSON array containing that same origin. Never shell-source
   this file. Keep the bcrypt hash single-quoted so Compose preserves `$` symbols.

   Generate the admin hash in an isolated temporary container (prompted password,
   no plaintext password in shell history):

   ```bash
   docker run --rm -it python:3.12-slim sh -c 'pip install --quiet bcrypt==4.0.1 && python -c "import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"'
   ```

   Set `TRANSFER_ENABLED=true` only after configuring **your** verified business
   routing numbers and hours. The broader `.env.example` documents optional voice,
   routing, and latency controls. The production template explicitly disables
   transfer to prevent accidental use of the source code's example numbers.

5. Issue a certificate **before starting Nginx**. Replace the domain/email below.
   Certbot standalone needs port 80 free on this first run:

   ```bash
   sudo certbot certonly --standalone -d ivr.company.com --email ops@company.com --agree-tos
   sudo install -d -m 700 /opt/kinikh-ivr/certs
   sudo install -m 644 /etc/letsencrypt/live/ivr.company.com/fullchain.pem /opt/kinikh-ivr/certs/fullchain.pem
   sudo install -m 600 /etc/letsencrypt/live/ivr.company.com/privkey.pem /opt/kinikh-ivr/certs/privkey.pem
   sudo install -d -m 755 /var/www/certbot
   ```

   Keep `TLS_CERT_DIR=/opt/kinikh-ivr/certs`. Copy certificate **contents** as above;
   mounting only Certbot's `live/<domain>` directory breaks its relative symlinks.
   For renewal, change this certificate's Certbot authenticator to `webroot` with
   `/var/www/certbot` (using `certbot reconfigure --cert-name ivr.company.com
   --authenticator webroot --webroot-path /var/www/certbot` on supporting versions).
   Install a root-owned renewal deploy hook that repeats the two `install` commands
   and runs `docker compose --project-directory /opt/kinikh-ivr --env-file
   /opt/kinikh-ivr/.env -f /opt/kinikh-ivr/docker/docker-compose.yml exec -T nginx
   nginx -s reload`. Test `sudo certbot renew --dry-run` after first deployment and
   confirm the renewal timer is enabled. Monitor certificate expiry externally.

6. Merge the reviewed `development` changes into your production branch `main` and push it to GitHub. Wait for **CI and release images** to
   pass and publish both image packages. On the VPS, authenticate for private GHCR
   packages using a token with `read:packages` and package access:

   ```bash
   docker login ghcr.io
   # Enter username/token at prompts; avoid putting tokens in command arguments.
   bash scripts/deploy.sh FULL_40_CHARACTER_MAIN_COMMIT_SHA
   ```

   The script pulls matching backend/web images, validates production settings,
   starts database/cache, makes a PostgreSQL dump, stops ingress/backend, runs
   migrations once, starts the backend, then recreates Nginx and waits for health.
   It records successful releases in `.deployed-release` and `.previous-release`.
   A failure stops the deployment and returns a nonzero exit; inspect logs before
   opening traffic. It does not automatically reverse database changes.

## GitHub CI/CD configuration

`.github/workflows/ci.yml` runs on pull requests and pushes to `main` or `development`:

- Install backend dependencies, check dependency consistency and known security advisories, compile source, run
  PostgreSQL migrations up/down/up on an isolated database, and run backend tests.
- Install the locked dashboard dependencies, audit for high/critical advisories, lint, run tests, and build it.
- After both jobs pass, build both Docker targets. Only trusted `main` runs publish
  GHCR images tagged with the full commit SHA (`linux/amd64`). Pull requests do not
  publish images or receive deployment secrets.

`.github/workflows/deploy.yml` is **manually dispatched** after successful CI. It
uses the GitHub `production` environment, a full commit SHA, pinned SSH host keys,
and `/opt/kinikh-ivr`. It fetches `main`, checks the release is on its history,
checks out that release's deployment configuration, and runs the deployment script.
Images must have been published successfully; arbitrary commits without images fail.
Keep the server checkout clean; store local configuration in `.env` only.

Create these GitHub environment secrets under **Settings → Environments → production**:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | VPS IPv4 or DNS hostname (SSH port 22 in the included workflow) |
| `DEPLOY_USER` | Linux deploy username |
| `DEPLOY_SSH_KEY` | Private Ed25519 key whose public key is in that user's `authorized_keys` |
| `DEPLOY_KNOWN_HOSTS` | Verified SSH `known_hosts` entry for that host |

Verify the SSH fingerprint using your VPS console or another trusted channel before
saving `DEPLOY_KNOWN_HOSTS`; blindly trusting an SSH network scan defeats host pinning.
Protect `main` with required CI checks/reviews; restrict the `production` environment
to `main` and configure required reviewers if your GitHub plan supports it. CI uses
its built-in `GITHUB_TOKEN` with `packages:write` to publish. Runtime provider secrets
stay in the VPS `.env`. The deployment workflow needs no provider credentials.
Dependabot checks Python, npm, Docker and Actions weekly. Review advisories and
update dependencies/base images regularly; passing tests alone is not a security audit.

## Connect Exotel and accept the release

Configure the Exotel flow using your actual public origin and webhook secret:

```text
Voicebot resolver: https://ivr.company.com/api/v1/telephony/incoming?webhook_token=YOUR_SECRET
Status callback:   https://ivr.company.com/api/v1/telephony/status?webhook_token=YOUR_SECRET
```

Keep the resolver's returned `wss://.../api/v1/telephony/stream/...?...` URL intact,
including its short-lived token. Set provider-confirmed `EXOTEL_ALLOWED_CIDRS` if
available. See [voice deployment details](docs/production_voice_deployment.md).

Before enabling customer traffic:

Create your departments and verified notification recipients through authenticated
`POST /api/v1/departments` (see [API notes](docs/api.md)). A new database starts
empty. Review `app/tenants/` and the company knowledge/prompts for your business;
the development seed contains preset recipients and is not run in production.
Without a department recipient, lead emails fall back to `SMTP_FROM_EMAIL`.

- `curl --fail https://ivr.company.com/api/v1/health` must show `status: ok` and
  `database: ok`; verify dashboard deep links load and `/docs` is disabled.
- Sign in with your configured admin password; verify protected APIs deny access
  without a token. Check login throttling and provider connection tests.
- Make actual inbound EN/HI calls. Verify greeting, two-way audio, interruption,
  latency, call completion, lead extraction, dashboard history, SMTP delivery,
  callbacks, and transfer behavior if enabled. Verify recording expectations.
- Test restart/recovery, an off-server backup restore, monitoring alerts, and TLS
  renewal. Confirm provider balance/rate limits and your call concurrency target.

## Operations, backups and rollback

Run from the repository root:

```bash
docker compose --env-file .env -f docker/docker-compose.yml ps
docker compose --env-file .env -f docker/docker-compose.yml logs --tail=100 app nginx
bash scripts/backup.sh
```

Schedule `backup.sh` at least daily with a service/cron job owned by the deploy user;
it serializes with deployments. A PostgreSQL dump and successful `pg_restore --list`
confirm a readable archive, **not** a full restore. Copy dumps to encrypted,
access-controlled off-server storage, set retention (for example 7 daily/4 weekly
copies), alert on failure, and rehearse restoration into a disposable database.
Back up `.env` securely and the `kinikh-ivr_app_storage` volume separately; database
dumps do not contain recording audio. Keep enough disk for backup retention.

For restoration during an agreed maintenance window, first stop ingress/backend,
restore a selected dump into a **new empty database** using `pg_restore -U kinikh
-d <restore_database> --no-owner --no-acl --exit-on-error`, validate it, then plan
cutover with matching application/schema versions. Never restore over a live
production database casually. Do not run `docker compose down -v`; that deletes data.
Changing `DB_PASSWORD` in `.env` does not rotate an existing PostgreSQL user's password;
coordinate database-side password rotation with the application configuration.

For an application rollback with a schema still compatible with the old release:

```bash
# Read .previous-release first; then supply that exact SHA explicitly.
bash scripts/deploy.sh PREVIOUS_FULL_COMMIT_SHA
```

For an older deployment configuration, check out that release first, as the GitHub
workflow does. The script runs `alembic upgrade head`; it cannot downgrade a newer
schema automatically. Inspect migration compatibility before rollback. If a failed
migration changed data/schema, leave traffic paused and perform a reviewed migration
repair or restore. Deployment backups are a recovery input, not an automatic rollback.

Monitor external HTTPS readiness, certificate expiry, error rate, call failures,
STT/LLM/TTS latency (`voice.turn.metrics`), provider spending/rate limits, DB space,
backup age, and filesystem use. App log retention is 30 days (errors 90); adjust to
your privacy policy and forward logs to your chosen monitoring service. Voice logs
may contain caller information: restrict access.

## Other hosting options

| Option | Fit and required adaptations |
| --- | --- |
| Another VPS / dedicated VM | Same Docker Compose deployment. Choose a provider with backups, a nearby region, reliable networking, and enough measured call capacity. |
| Managed container platform | Use a long-running container service supporting inbound WebSockets, suitable connection timeouts, outbound provider access, persistent storage and a one-off migration job. Supply managed PostgreSQL/Redis and platform secrets. Adapt the database/cache URL overrides in Compose or deploy the runtime image directly. |
| AWS/GCP/Azure container or VM services | Good when you need managed database backups, centralized logs, secrets, and load balancers. Configure HTTPS/WSS timeouts, a private DB/cache network and persistent media storage. Use one backend instance until call ownership is redesigned. |
| Static hosting for dashboard only | Serve `dashboard/dist`; set public `VITE_API_BASE_URL` at build time and add that dashboard origin to backend CORS. Host the backend separately on a long-running service. The VPS preflight intentionally enforces same-origin; adapt it for this topology. |
| Short-lived function hosting | A poor match for this backend's long-lived audio streams and in-process call sessions. It can host ancillary jobs if designed separately. |

The included workflow builds x86-64 images. ARM VPS instances need tested multi-arch
builds before deployment. Managed hosting is an alternative architecture, not an
included provider-specific deployment; validate your chosen plan's current limits.

Reference: [Docker production Compose guidance](https://docs.docker.com/compose/how-tos/production/),
[Compose startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/),
[Certbot renewal guidance](https://eff-certbot.readthedocs.io/en/stable/using.html#modifying-the-renewal-configuration-of-existing-certificates),
and [GitHub package publishing](https://docs.github.com/en/packages/managing-github-packages-using-github-actions-workflows/publishing-and-installing-a-package-with-github-actions).
