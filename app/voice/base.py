"""Abstract base for all voice providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class BaseVoiceProvider(ABC):
    """Contract every TTS/STT provider must satisfy.

    Implementations should raise ExternalServiceError on unrecoverable failures
    so higher-level wrappers (SpeechToText, TextToSpeech) can retry or fall back.
    """

    @property
    def supports_transcription(self) -> bool:
        """True if this provider can convert audio → text."""
        return False

    @property
    def supports_synthesis(self) -> bool:
        """True if this provider can convert text → audio."""
        return True

    def supports_voice_profile(self, language: str, voice_profile: str) -> bool:
        """True if a curated voice exists for this (language, speech style).
        Providers that support it accept a `voice_profile=` keyword on
        synthesize()/synthesize_stream(); it is only ever passed when this
        returns True."""
        return False

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, *, language: str | None = None) -> str:
        """Return transcript text for the given audio bytes.

        Raises:
            ExternalServiceError: on unrecoverable provider failure.
            NotImplementedError: if the provider does not support transcription.
        """

    @abstractmethod
    async def synthesize(self, text: str, *, language: str = "en", voice: str = "alloy") -> bytes:
        """Return audio bytes for the given text.

        Raises:
            ExternalServiceError: on unrecoverable provider failure.
            NotImplementedError: if the provider does not support synthesis.
        """

    @abstractmethod
    async def synthesize_stream(
        self, text: str, *, language: str = "en", voice: str = "alloy"
    ) -> AsyncIterator[bytes]:
        """Yield audio chunks for the given text.

        Raises:
            ExternalServiceError: on failure before or during streaming.
            NotImplementedError: if the provider does not support synthesis.
        """
