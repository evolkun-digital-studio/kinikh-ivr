"""VoiceManager — orchestrates STT and TTS via provider abstraction."""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from app.core.call_logging import describe_error
from app.core.config import get_settings
from app.voice.openai_provider import OpenAIVoiceProvider
from app.voice.stt import SpeechToText
from app.voice.tts import TextToSpeech


def _default_tts() -> TextToSpeech:
    """TTS chain selected by TTS_PROVIDER. Providers are imported lazily so
    a provider that is not selected never needs its settings."""
    provider = get_settings().tts_provider
    if provider == "openai":
        return TextToSpeech(OpenAIVoiceProvider())
    from app.voice.elevenlabs_provider import ElevenLabsProvider

    return TextToSpeech(ElevenLabsProvider(), fallback=OpenAIVoiceProvider())


class VoiceManager:
    """Unified interface for speech-to-text and text-to-speech.

    Default configuration: OpenAI Whisper for STT; primary TTS chosen by
    TTS_PROVIDER (ElevenLabs by default) with OpenAI TTS as
    fallback, or OpenAI TTS alone.

    Accepts pre-built SpeechToText / TextToSpeech instances for testing.
    """

    def __init__(
        self,
        stt: SpeechToText | None = None,
        tts: TextToSpeech | None = None,
    ) -> None:
        if stt is None:
            stt = SpeechToText(OpenAIVoiceProvider())
        if tts is None:
            tts = _default_tts()
        self._stt = stt
        self._tts = tts

    @property
    def last_used_fallback(self) -> bool:
        """True if the most recent synthesis used the fallback TTS provider."""
        return bool(getattr(self._tts, "last_used_fallback", False))

    @property
    def last_stt_model(self) -> str | None:
        return getattr(self._stt, "last_model", None)

    @property
    def last_tts_model(self) -> str | None:
        return getattr(self._tts, "last_model", None)

    async def transcribe(self, audio_bytes: bytes, language: str | None = None) -> str:
        """Transcribe audio bytes to text. Returns "" on unrecoverable failure."""
        try:
            return await self._stt.transcribe(audio_bytes, language=language)
        except Exception as exc:
            logger.error(f"[voice_manager] transcribe failed: {describe_error(exc)}")
            return ""

    def supports_voice_profile(self, language: str, voice_profile: str) -> bool:
        """True if the primary TTS has a curated voice for this speech style."""
        supports = getattr(self._tts, "supports_voice_profile", None)
        return callable(supports) and supports(language, voice_profile) is True

    async def synthesize(
        self, text: str, language: str = "en", *, voice_profile: str | None = None
    ) -> bytes:
        """Convert text to audio bytes. Returns b"" on unrecoverable failure."""
        profile = {"voice_profile": voice_profile} if voice_profile else {}
        try:
            return await self._tts.synthesize(text, language=language, **profile)
        except Exception as exc:
            logger.error(f"[voice_manager] synthesize failed: {describe_error(exc)}")
            return b""

    async def synthesize_to_stream(
        self, text: str, language: str = "en", *, voice_profile: str | None = None
    ) -> AsyncIterator[bytes]:
        """Stream audio chunks for lower first-byte latency."""
        profile = {"voice_profile": voice_profile} if voice_profile else {}
        try:
            async for chunk in self._tts.synthesize_stream(text, language=language, **profile):
                yield chunk
        except Exception as exc:
            logger.error(f"[voice_manager] synthesize_stream failed: {describe_error(exc)}")
