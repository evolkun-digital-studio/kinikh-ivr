"""OpenAI voice provider — Whisper STT + OpenAI TTS."""

from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from app.ai.openai_client import OpenAIClient, get_openai_client
from app.core.call_logging import describe_error
from app.core.exceptions import ExternalServiceError
from app.voice.base import BaseVoiceProvider
from app.voice.pcm import Downsampler

# OpenAI TTS "pcm" output: raw signed 16-bit little-endian mono at 24 kHz.
_OPENAI_PCM_RATE = 24000


class OpenAIVoiceProvider(BaseVoiceProvider):
    """OpenAI STT + tts-1 TTS backed by the shared OpenAIClient.

    TTS output is 8 kHz PCM16 — the same format as ElevenLabsClient — so it
    can stand in as the fallback voice on a live call. (It used to return
    MP3, which the call path streamed to Exotel as if it were raw PCM:
    every ElevenLabs outage became loud static on the caller's line.)

    Delegates all retry/backoff to the underlying OpenAIClient. Re-raises
    exhausted failures as ExternalServiceError for callers to handle.
    """

    def __init__(self, client: OpenAIClient | None = None) -> None:
        self._client = client or get_openai_client()

    @property
    def supports_transcription(self) -> bool:
        return True

    @property
    def last_stt_model(self) -> str | None:
        return self._client.last_stt_model

    @property
    def model_id(self) -> str:
        return "tts-1"

    async def transcribe(self, audio_bytes: bytes, *, language: str | None = None) -> str:
        try:
            return await self._client.transcribe(audio_bytes, language=language)
        except Exception as exc:
            logger.error(f"[openai_provider] STT failed: {describe_error(exc)}")
            raise ExternalServiceError("OpenAI", str(exc)) from exc

    async def synthesize(self, text: str, *, language: str = "en", voice: str = "alloy") -> bytes:
        try:
            pcm = await self._client.tts(text, voice=voice, response_format="pcm")
            resampler = Downsampler(_OPENAI_PCM_RATE)
            return resampler.process(pcm) + resampler.flush()
        except Exception as exc:
            logger.error(f"[openai_provider] TTS failed: {describe_error(exc)}")
            raise ExternalServiceError("OpenAI", str(exc)) from exc

    async def synthesize_stream(
        self, text: str, *, language: str = "en", voice: str = "alloy"
    ) -> AsyncIterator[bytes]:
        try:
            resampler = Downsampler(_OPENAI_PCM_RATE)
            async for chunk in self._client.tts_stream(
                text, voice=voice, response_format="pcm"
            ):
                out = resampler.process(chunk)
                if out:
                    yield out
            tail = resampler.flush()
            if tail:
                yield tail
        except Exception as exc:
            logger.error(f"[openai_provider] TTS stream failed: {describe_error(exc)}")
            raise ExternalServiceError("OpenAI", str(exc)) from exc
