"""ElevenLabs voice provider — TTS only."""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from app.core.call_logging import describe_error
from app.core.exceptions import ExternalServiceError
from app.voice.base import BaseVoiceProvider
from app.voice.elevenlabs import ElevenLabsClient


class ElevenLabsProvider(BaseVoiceProvider):
    """ElevenLabs text-to-speech provider. Does not support transcription."""

    def __init__(self, client: ElevenLabsClient | None = None) -> None:
        self._client = client or ElevenLabsClient()

    @property
    def supports_transcription(self) -> bool:
        return False

    @property
    def model_id(self) -> str:
        return self._client.model_id

    async def transcribe(self, audio_bytes: bytes, *, language: str | None = None) -> str:
        raise NotImplementedError("ElevenLabsProvider does not support transcription")

    def supports_voice_profile(self, language: str, voice_profile: str) -> bool:
        return self._client.has_style_voice(language, voice_profile)

    async def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str = "alloy",
        voice_profile: str | None = None,
    ) -> bytes:
        try:
            style = {"speech_style": voice_profile} if voice_profile else {}
            return await self._client.synthesize(text, language, **style)
        except Exception as exc:
            logger.error(f"[elevenlabs_provider] TTS failed: {describe_error(exc)}")
            raise ExternalServiceError("ElevenLabs", str(exc)) from exc

    async def synthesize_stream(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str = "alloy",
        voice_profile: str | None = None,
    ) -> AsyncIterator[bytes]:
        style = {"speech_style": voice_profile} if voice_profile else {}
        try:
            async for chunk in self._client.synthesize_stream(text, language, **style):
                yield chunk
        except Exception as exc:
            logger.error(f"[elevenlabs_provider] TTS stream failed: {describe_error(exc)}")
            raise ExternalServiceError("ElevenLabs", str(exc)) from exc
