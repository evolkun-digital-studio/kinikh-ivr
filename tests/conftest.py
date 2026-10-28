"""Shared pytest fixtures."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Set environment variables before importing the app to satisfy pydantic validation.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-pydantic")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ELEVENLABS_API_KEY", "el-test")
os.environ.setdefault("ELEVENLABS_VOICE_ID_EN", "test-en-voice")
os.environ.setdefault("ELEVENLABS_VOICE_ID_HI", "test-hi-voice")
os.environ.setdefault("EXOTEL_SID", "test-sid")
os.environ.setdefault("EXOTEL_TOKEN", "test-token")
os.environ.setdefault("EXOTEL_API_KEY", "test-api-key")
os.environ.setdefault("EXOTEL_API_SECRET", "test-api-secret")
os.environ.setdefault("EXOTEL_CALLER_ID", "+910000000000")
os.environ.setdefault("EXOTEL_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("SMTP_USERNAME", "test@test.com")
os.environ.setdefault("SMTP_PASSWORD", "test-password")
os.environ.setdefault("SMTP_FROM_EMAIL", "test@test.com")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-that-is-long-enough")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    "$2b$12$2OTwnBBhKsYAHfxDIf3pS.GiG1yKl5a1o9/eU.HDJsa9YvZ0TNwBu",  # "dev-only-changeme"
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _default_ai_auto_greeting():
    """The test suite assumes the receptionist's greeting fires by default —
    keep it deterministic regardless of the local .env/config default (which
    may be intentionally toggled off for manual debugging of the auto-
    greeting feature). Tests that specifically need the disabled path patch
    this themselves for their own scope."""
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.ai_auto_greeting
    settings.ai_auto_greeting = True
    yield
    settings.ai_auto_greeting = original


@pytest.fixture
def mock_db_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    return session

@pytest.fixture(autouse=True)
async def cleanup_speech_handlers():
    """Automatically track and cleanup all SpeechHandlers instantiated during a test."""
    from app.voice.speech_handler import SpeechHandler
    original_init = SpeechHandler.__init__
    handlers = []

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        handlers.append(self)

    with patch.object(SpeechHandler, "__init__", tracking_init):
        yield
        
    for handler in handlers:
        await handler.mark_stopped()


@pytest.fixture
def sample_lead_data() -> dict:
    return {
        "name": "Rajesh Kumar",
        "phone": "+919876543210",
        "email": "rajesh@example.com",
        "department": "rural",
        "requirement": "Need pricing for enterprise plan",
        "summary": "Caller wants enterprise pricing details.",
        "language": "en",
        "timestamp": "2026-06-29T10:00:00Z",
        "additional_notes": None,
    }


@pytest.fixture
def sample_call_sid() -> str:
    return f"CALL{uuid.uuid4().hex[:16].upper()}"


@pytest.fixture(autouse=True)
def _clear_greeting_audio_cache():
    """The greeting audio cache is process-wide; keep tests independent."""
    from app.voice import speech_handler

    speech_handler._GREETING_AUDIO_CACHE.clear()
    yield
    speech_handler._GREETING_AUDIO_CACHE.clear()
