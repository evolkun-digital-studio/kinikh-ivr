"""TextToSpeech — TTS wrapper with streaming and provider fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from app.core.call_logging import describe_error
from app.core.exceptions import ExternalServiceError
from app.voice.base import BaseVoiceProvider


class TextToSpeech:
    """Wraps a primary TTS provider with optional fallback on failure.

    Falls back to the secondary provider when the primary returns empty bytes
    or raises ExternalServiceError. Returns b"" only if all providers fail.
    """

    def __init__(
        self,
        primary: BaseVoiceProvider,
        fallback: BaseVoiceProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        # True when the most recent synthesis came from the fallback provider
        # (a different voice) — callers use it to avoid caching that audio.
        self.last_used_fallback = False
        self.last_model: str | None = getattr(primary, "model_id", None)

    def supports_voice_profile(self, language: str, voice_profile: str) -> bool:
        return self._primary.supports_voice_profile(language, voice_profile)

    async def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str = "alloy",
        voice_profile: str | None = None,
    ) -> bytes:
        """Synthesize text to audio bytes, falling back on empty or error.
        `voice_profile` (a speech style) only reaches the primary provider;
        the fallback always uses its neutral voice."""
        self.last_used_fallback = False
        self.last_model = getattr(self._primary, "model_id", None)
        profile = {"voice_profile": voice_profile} if voice_profile else {}
        try:
            audio = await self._primary.synthesize(
                text, language=language, voice=voice, **profile
            )
            if audio:
                return audio
            logger.warning("[tts] primary returned empty audio")
        except ExternalServiceError as exc:
            logger.warning(f"[tts] primary failed: {describe_error(exc)}")

        if self._fallback:
            logger.warning("[tts] using fallback provider")
            self.last_used_fallback = True
            self.last_model = getattr(self._fallback, "model_id", None)
            try:
                return await self._fallback.synthesize(text, language=language, voice=voice)
            except Exception as exc:
                logger.error(f"[tts] fallback failed: {describe_error(exc)}")
        return b""

    async def synthesize_stream(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str = "alloy",
        voice_profile: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream audio chunks, falling back if primary yields nothing or errors."""
        self.last_used_fallback = False
        self.last_model = getattr(self._primary, "model_id", None)
        yielded = False
        profile = {"voice_profile": voice_profile} if voice_profile else {}
        try:
            async for chunk in self._primary.synthesize_stream(
                text, language=language, voice=voice, **profile
            ):
                yield chunk
                yielded = True
        except ExternalServiceError as exc:
            logger.warning(f"[tts] primary stream failed: {describe_error(exc)}")

        if not yielded and self._fallback:
            logger.warning("[tts] primary stream empty, using fallback")
            self.last_used_fallback = True
            self.last_model = getattr(self._fallback, "model_id", None)
            try:
                async for chunk in self._fallback.synthesize_stream(
                    text, language=language, voice=voice
                ):
                    yield chunk
            except Exception as exc:
                logger.error(f"[tts] fallback stream failed: {describe_error(exc)}")
