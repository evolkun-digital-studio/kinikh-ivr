#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-time development environment bootstrap
# Usage: ./scripts/setup.sh
# =============================================================================
set -euo pipefail

PYTHON=${PYTHON:-python3.12}
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_ROOT"

log() { echo "▶  $*"; }
die() { echo "✗  $*" >&2; exit 1; }

# ── Check prerequisites ───────────────────────────────────────────────────────
log "Checking prerequisites..."
command -v "$PYTHON" >/dev/null 2>&1 || die "Python 3.12 not found. Install from python.org."
"$PYTHON" -c "import sys; assert sys.version_info >= (3,12), 'Need Python 3.12+'" || die "Python 3.12+ required."

# ── Create virtual environment ────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    log "Creating virtual environment with $PYTHON..."
    "$PYTHON" -m venv venv
fi

# shellcheck source=/dev/null
source venv/bin/activate

# ── Upgrade pip ───────────────────────────────────────────────────────────────
log "Upgrading pip..."
pip install --upgrade pip --quiet

# ── Install dependencies ──────────────────────────────────────────────────────
log "Installing runtime + dev dependencies..."
pip install -r requirements-dev.txt --quiet

# ── Install pre-commit hooks ──────────────────────────────────────────────────
log "Installing pre-commit hooks..."
pre-commit install --install-hooks

# ── Create .env from example ──────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    log "Creating .env from .env.example..."
    cp .env.example .env
    echo ""
    echo "⚠  .env created from .env.example."
    echo "   Fill in all required values before starting the app."
    echo ""
else
    log ".env already exists — skipping."
fi

# ── Create log directory ──────────────────────────────────────────────────────
mkdir -p logs

log "Setup complete. Activate with: source venv/bin/activate"
log "Start dev server: ./scripts/dev.sh"
