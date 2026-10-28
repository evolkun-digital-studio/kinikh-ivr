#!/usr/bin/env bash
# =============================================================================
# lint.sh — Run all linters and formatters
# Usage: ./scripts/lint.sh [--fix]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

source venv/bin/activate

FIX=${1:-}
ERRORS=0

run() {
    echo "▶  $*"
    "$@" || ERRORS=$((ERRORS + 1))
}

if [ "$FIX" = "--fix" ]; then
    echo "Running formatters (fix mode)..."
    run ruff check --fix app tests
    run ruff format app tests
    run black app tests
    run isort --profile black --line-length 100 app tests
else
    echo "Running linters (check mode)..."
    run ruff check app tests
    run ruff format --check app tests
    run black --check app tests
    run isort --check-only --profile black --line-length 100 app tests
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "✗  $ERRORS check(s) failed. Run './scripts/lint.sh --fix' to auto-fix."
    exit 1
fi

echo ""
echo "✓  All checks passed."
