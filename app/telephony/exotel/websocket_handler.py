"""ExotelWebSocketHandler — orchestrates an Exotel audio streaming session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.core.call_logging import describe_error
from app.telephony.exotel.call_manager import CallManager
from app.telephony.exotel.stream import ExotelAudioStream


async def _noop_audio(audio_bytes: bytes) -> None:
    pass


class ExotelWebSocketHandler:
    """Manages the lifecycle of a single Exotel WebSocket audio stream.

    Receives audio chunks and DTMF events from an Exotel WebSocket connection
    and forwards them to caller-supplied async callbacks. Automatically removes
    the call from CallManager when the session ends.

    Typical usage in a FastAPI WebSocket route::

        handler = ExotelWebSocketHandler(call_manager)
        await handler.run(websocket, on_audio=my_audio_callback)
    """

    def __init__(self, call_manager: CallManager) -> None:
        self._call_manager = call_manager
        self._stream: ExotelAudioStream | None = None

    @property
    def call_sid(self) -> str | None:
        return self._stream.call_sid if self._stream else None

    @property
    def stream_sid(self) -> str | None:
        return self._stream.stream_sid if self._stream else None

    async def run(
        self,
        websocket: Any,
        on_audio: Callable[[bytes], Awaitable[None]] | None = None,
        on_dtmf: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Start the streaming session and block until it ends.

        Args:
            websocket: FastAPI/Starlette WebSocket with `iter_text()` and
                       `send_text()` methods.
            on_audio:  Async callback invoked with each μ-law audio chunk.
            on_dtmf:   Async callback invoked with each DTMF digit string.
        """
        self._stream = ExotelAudioStream(
            websocket,
            on_audio=on_audio or _noop_audio,
            on_dtmf=on_dtmf,
        )

        logger.info("[ws_handler] starting Exotel audio stream")
        try:
            await self._stream.start()
        except Exception as exc:
            logger.error(f"[ws_handler] stream error: {describe_error(exc)}")
            raise
        finally:
            call_sid = self._stream.call_sid
            if call_sid:
                await self._call_manager.remove(call_sid)
                logger.info(f"[ws_handler] call {call_sid} removed from manager")

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio bytes back to the caller through the active stream."""
        if self._stream:
            await self._stream.send_audio(audio_bytes)

    async def stop(self) -> None:
        """Gracefully stop the audio stream."""
        if self._stream:
            await self._stream.stop()
