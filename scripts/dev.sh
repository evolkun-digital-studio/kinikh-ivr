#!/usr/bin/env bash
# =============================================================================
# dev.sh — Start the FastAPI development server with hot-reload
# Usage: ./scripts/dev.sh
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f "venv/bin/activate" ]; then
    echo "venv not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi

# shellcheck source=/dev/null
source venv/bin/activate

if [ ! -f ".env" ]; then
    echo "No .env found. Copy .env.example to .env and fill in credentials." >&2
    exit 1
fi

# Export env vars
set -a
# shellcheck source=/dev/null
source .env
set +a

echo "Starting Kinikh IVR development server..."
echo "  Docs: http://localhost:${APP_PORT:-8000}/docs"
echo "  Health: http://localhost:${APP_PORT:-8000}/api/v1/health"
echo ""

exec uvicorn app.main:app \
    --host "${APP_HOST:-0.0.0.0}" \
    --port "${APP_PORT:-8000}" \
    --reload \
    --reload-dir app \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --log-level debug
