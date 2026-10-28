#!/usr/bin/env bash
# Deploy an already-published commit image. Never evaluate .env as shell code.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${1:?Usage: scripts/deploy.sh <40-character commit SHA>}"
[[ "$1" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full lowercase commit SHA' >&2; exit 1; }
export IMAGE_TAG="$1"
exec 9>.deploy.lock
flock -n 9 || { echo 'Another deployment or backup is running' >&2; exit 1; }
compose=(docker compose --env-file .env -f docker/docker-compose.yml)
"${compose[@]}" config --quiet
"${compose[@]}" pull app migrate nginx
"${compose[@]}" run --rm --no-deps app python scripts/check_production.py
"${compose[@]}" up -d --wait --wait-timeout 120 db redis
# Back up before any schema changes. Fail closed if the dump fails.
bash scripts/backup.sh --locked
# Call state is process-local. Schedule a maintenance window and drain calls first.
"${compose[@]}" stop nginx app
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 app
# Recreate nginx so it resolves the replacement backend container's address.
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 nginx
if [[ -f .deployed-release ]]; then cp .deployed-release .previous-release; fi
printf '%s\n' "$IMAGE_TAG" > .deployed-release
printf 'Deployed %s. Verify the public HTTPS endpoint and a real phone call.\n' "$IMAGE_TAG"
