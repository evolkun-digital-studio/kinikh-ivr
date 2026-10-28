"""OpenAI Realtime API client — native SDK WebSocket wrapper for voice sessions."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from openai.types.beta.realtime import RealtimeServerEvent

from app.core.config import get_settings

_AUDIO_DELTA_TYPE = "response.audio.delta"
_AUDIO_DONE_TYPE = "response.audio.done"
_TRANSCRIPT_DONE_TYPE = "response.audio_transcript.done"
_RESPONSE_DONE_TYPE = "response.done"
_ERROR_TYPE = "error"
_SESSION_CREATED_TYPE = "session.created"
_SESSION_UPDATED_TYPE = "session.updated"


class RealtimeSession:
    """
    Async context manager for a single OpenAI Realtime API WebSocket session.

    Usage::

        client = RealtimeClient()
        async with client.connect(system_prompt="You are ...") as session:
            await session.send_audio(pcm16_bytes)
            async for audio_chunk in session.stream_response():
                # send audio_chunk to caller
    """

    def __init__(
        self,
        raw_client: AsyncOpenAI,
        model: str,
        system_prompt: str,
        voice: str,
    ) -> None:
        self._raw = raw_client
        self._model = model
        self._system_prompt = system_prompt
        self._voice = voice
        self._conn: Any = None
        self._cm: Any = None

    async def __aenter__(self) -> RealtimeSession:
        self._cm = self._raw.beta.realtime.connect(model=self._model)
        self._conn = await self._cm.__aenter__()
        logger.info(f"[realtime] connected model={self._model} voice={self._voice}")

        await self._conn.session.update(
            session={
                "modalities": ["text", "audio"],
                "instructions": self._system_prompt,
                "voice": self._voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 200,
                },
                "input_audio_transcription": {"model": "whisper-1"},
            }
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as exc:
                logger.debug(f"[realtime] close error (ignored): {exc}")
        logger.info("[realtime] disconnected")

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Append PCM16 audio chunk to the server-side input buffer."""
        encoded = base64.b64encode(audio_bytes).decode()
        await self._conn.input_audio_buffer.append(audio=encoded)

    async def commit_audio(self) -> None:
        """Signal end of user audio turn (not needed in server-VAD mode)."""
        await self._conn.input_audio_buffer.commit()

    async def clear_audio(self) -> None:
        """Discard pending audio from the input buffer."""
        await self._conn.input_audio_buffer.clear()

    async def request_response(self) -> None:
        """Ask the model to generate a response from the current conversation."""
        await self._conn.response.create()

    def events(self) -> AsyncIterator[RealtimeServerEvent]:
        """Raw event stream — iterate to receive all server events."""
        return self._conn.__aiter__()

    async def stream_response(self) -> AsyncIterator[bytes]:
        """
        Yield PCM16 audio chunks from the next model response.
        Stops after response.done or error.
        """
        async for event in self._conn:
            etype = event.type
            if etype == _AUDIO_DELTA_TYPE:
                delta: str = getattr(event, "delta", "")
                if delta:
                    yield base64.b64decode(delta)
            elif etype in (_RESPONSE_DONE_TYPE, _AUDIO_DONE_TYPE):
                logger.debug(f"[realtime] {etype} — response complete")
                break
            elif etype == _ERROR_TYPE:
                error = getattr(event, "error", {})
                logger.error(f"[realtime] error event: {error}")
                break

    async def text_to_speech(self, text: str) -> AsyncIterator[bytes]:
        """
        Send a text message and stream back the audio response.
        Creates a conversation item then requests a response.
        """
        await self._conn.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self.request_response()
        async for chunk in self.stream_response():
            yield chunk

    async def collect_transcript(self, timeout: float = 10.0) -> str:
        """
        Wait for a transcript.done event and return the transcript text.
        Useful after sending audio and committing the buffer.
        """
        try:
            async with asyncio.timeout(timeout):
                async for event in self._conn:
                    etype = event.type
                    if etype == _TRANSCRIPT_DONE_TYPE:
                        return getattr(event, "transcript", "")
                    if etype in (_ERROR_TYPE, _RESPONSE_DONE_TYPE):
                        break
        except TimeoutError:
            logger.warning("[realtime] transcript collection timed out")
        return ""


class RealtimeClient:
    """
    Factory for RealtimeSession objects.

    Wraps the native OpenAI SDK Realtime connection manager so callers
    never touch AsyncOpenAI directly.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._raw = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_realtime_model

    def connect(
        self,
        system_prompt: str = "",
        voice: str = "alloy",
    ) -> RealtimeSession:
        """
        Return an unconnected RealtimeSession.
        Use as an async context manager to open the WebSocket::

            async with client.connect(system_prompt="...") as session:
                ...
        """
        return RealtimeSession(
            raw_client=self._raw,
            model=self._model,
            system_prompt=system_prompt,
            voice=voice,
        )
