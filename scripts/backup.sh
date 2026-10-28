#!/usr/bin/env bash
# Logical PostgreSQL backup; copy the result to encrypted off-server storage.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
if [[ "${1:-}" != --locked ]]; then
  exec 9>.deploy.lock
  flock -n 9 || { echo 'A deployment or backup is running' >&2; exit 1; }
fi
mkdir -p backups
backup_file="backups/database-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
compose=(docker compose --env-file .env -f docker/docker-compose.yml)
"${compose[@]}" exec -T db pg_dump -U kinikh -d kinikh_ivr -Fc > "$backup_file.partial"
"${compose[@]}" exec -T db pg_restore --list < "$backup_file.partial" > /dev/null
mv "$backup_file.partial" "$backup_file"
echo "Database backup: $backup_file"
