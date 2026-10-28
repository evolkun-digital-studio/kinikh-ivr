#!/usr/bin/env bash
# =============================================================================
# migrate.sh — Run Alembic migrations
# Usage: ./scripts/migrate.sh [upgrade head | downgrade -1 | revision --autogenerate -m "msg"]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

source venv/bin/activate

if [ -f ".env" ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

COMMAND="${*:-upgrade head}"
echo "Running: alembic $COMMAND"
# shellcheck disable=SC2086
exec alembic $COMMAND
