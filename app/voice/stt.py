"""SpeechToText — STT wrapper with retry logic and provider fallback."""

from __future__ import annotations

import asyncio

from loguru import logger

from app.core.call_logging import describe_error
from app.core.exceptions import ExternalServiceError
from app.voice.base import BaseVoiceProvider


class SpeechToText:
    """Wraps a BaseVoiceProvider with retry and optional fallback.

    Retries on ExternalServiceError up to max_retries times,
    then delegates to the fallback provider. Returns "" if everything fails.
    """

    def __init__(
        self,
        provider: BaseVoiceProvider,
        *,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        fallback: BaseVoiceProvider | None = None,
    ) -> None:
        if not provider.supports_transcription:
            raise ValueError(f"{type(provider).__name__} does not support transcription")
        self._provider = provider
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._fallback = fallback

    @property
    def last_model(self) -> str | None:
        return getattr(self._provider, "last_stt_model", None)

    async def transcribe(self, audio_bytes: bytes, *, language: str | None = None) -> str:
        """Transcribe audio with retry and fallback. Always returns a string."""
        for attempt in range(1, self._max_retries + 1):
            try:
                # An empty transcript is a valid answer ("no speech in this
                # audio") — retrying it only added a second paid STT call and
                # ~0.5 s of dead air on every noise burst.
                return await self._provider.transcribe(audio_bytes, language=language)
            except ExternalServiceError as exc:
                logger.warning(f"[stt] provider error attempt={attempt}: {describe_error(exc)}")
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_delay * attempt)

        if self._fallback and self._fallback.supports_transcription:
            logger.warning("[stt] primary exhausted, trying fallback")
            try:
                return await self._fallback.transcribe(audio_bytes, language=language)
            except Exception as exc:
                logger.error(f"[stt] fallback failed: {describe_error(exc)}")
        return ""
