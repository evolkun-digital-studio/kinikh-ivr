#!/usr/bin/env bash
# =============================================================================
# test.sh — Run the test suite with coverage
# Usage: ./scripts/test.sh [extra pytest args]
# =============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

source venv/bin/activate

# Minimal env vars so config validation passes in unit tests.
export APP_ENV=testing
export SECRET_KEY="${SECRET_KEY:-ci-secret-key-that-is-long-enough-for-pydantic-32c}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://test:test@localhost/test}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-test}"
export ELEVENLABS_API_KEY="${ELEVENLABS_API_KEY:-el-test}"
export ELEVENLABS_VOICE_ID_EN="${ELEVENLABS_VOICE_ID_EN:-en-voice}"
export ELEVENLABS_VOICE_ID_HI="${ELEVENLABS_VOICE_ID_HI:-hi-voice}"
export EXOTEL_SID="${EXOTEL_SID:-sid}"
export EXOTEL_TOKEN="${EXOTEL_TOKEN:-token}"
export EXOTEL_API_KEY="${EXOTEL_API_KEY:-apikey}"
export EXOTEL_API_SECRET="${EXOTEL_API_SECRET:-apisecret}"
export EXOTEL_CALLER_ID="${EXOTEL_CALLER_ID:-+910000000000}"
export EXOTEL_WEBHOOK_SECRET="${EXOTEL_WEBHOOK_SECRET:-webhooksecret}"
export SMTP_USERNAME="${SMTP_USERNAME:-test@test.com}"
export SMTP_PASSWORD="${SMTP_PASSWORD:-pass}"
export SMTP_FROM_EMAIL="${SMTP_FROM_EMAIL:-test@test.com}"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-jwt-secret-key-that-is-long-enough-pydantic-32c}"

exec pytest tests/ \
    --cov=app \
    --cov-report=term-missing \
    --cov-fail-under=60 \
    "$@"
