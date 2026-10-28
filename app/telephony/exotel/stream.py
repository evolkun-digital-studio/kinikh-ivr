"""Exotel audio stream handler — bridges Exotel WS stream to the AI pipeline."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Callable, Coroutine
import json
from typing import Any

from loguru import logger
from starlette.websockets import WebSocketState

from app.core.call_logging import log_call_event, mask_phone


class ExotelAudioStream:
    """Receives μ-law audio from Exotel WebSocket and feeds it to consumer callbacks.

    Lifecycle callbacks:
    - on_start(call_sid, caller_number) — fired once when stream begins
    - on_audio(bytes)                   — fired for each media chunk
    - on_dtmf(digit)                    — fired for each DTMF keypress
    - on_stop(call_sid)                 — fired once, immediately when the
      'stop' event is parsed (before the receive loop even finishes), so the
      call can be marked closed before finalize() runs
    """

    def __init__(
        self,
        websocket: Any,
        on_audio: Callable[[bytes], Coroutine[Any, Any, None]],
        on_dtmf: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_start: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
        on_stop: Callable[[str | None], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._ws = websocket
        self._on_audio = on_audio
        self._on_dtmf = on_dtmf
        self._on_start = on_start
        self._on_stop = on_stop
        self._stream_sid: str | None = None
        self._call_sid: str | None = None
        self._account_sid: str | None = None
        self._caller_number: str = ""
        self._running = False
        # Now that audio flushes run as background tasks (see AudioBuffer),
        # more than one AI turn can finish and try to send its reply around
        # the same time — serialize actual writes to the one shared socket.
        self._send_lock = asyncio.Lock()

    @property
    def call_sid(self) -> str | None:
        return self._call_sid

    @property
    def stream_sid(self) -> str | None:
        return self._stream_sid

    @property
    def caller_number(self) -> str:
        return self._caller_number

    @property
    def account_sid(self) -> str | None:
        return self._account_sid

    async def start(self) -> None:
        self._running = True
        try:
            async for raw in self._ws.iter_text():
                if not self._running:
                    break
                await self._handle_message(raw)
        except Exception as exc:
            logger.error(
                f"ExotelAudioStream error call_sid={self._call_sid} type={type(exc).__name__}"
            )
            raise

    async def _handle_message(self, raw: str) -> None:
        # A malformed frame is dropped, never allowed to end the whole call.
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[EXOTEL] dropped non-JSON frame call_sid={self._call_sid}")
            return
        if not isinstance(msg, dict):
            logger.warning(f"[EXOTEL] dropped non-object frame call_sid={self._call_sid}")
            return

        event = msg.get("event")

        if event == "connected":
            logger.info("Exotel stream connected")
            logger.info("[EXOTEL] connected event")

        elif event == "start":
            if self._call_sid is not None:
                raise ValueError("Duplicate stream start")
            start = msg.get("start")
            if not isinstance(start, dict):
                logger.warning("[EXOTEL] dropped start event without a start object")
                return
            # Exotel's real AgentStream/Voicebot protocol uses snake_case
            # (confirmed against developer.exotel.com/docs/agentstream/
            # websocket-protocol) — NOT the camelCase this previously read,
            # which meant call_sid/stream_sid were always None on every real
            # call despite the start event arriving correctly.
            self._stream_sid = msg.get("stream_sid") or start.get("stream_sid")
            self._call_sid = start.get("call_sid")
            self._account_sid = start.get("account_sid")
            # The caller's number is a direct field of `start` ("from"), not
            # nested inside custom_parameters (that dict is for app-defined
            # key/value pairs). custom_parameters.From/from is kept as a
            # fallback for backward compatibility with any Flow configured
            # to pass it that way instead.
            custom = start.get("custom_parameters")
            custom = custom if isinstance(custom, dict) else {}
            self._caller_number = (
                start.get("from") or custom.get("From") or custom.get("from") or ""
            )
            logger.info(
                f"Stream started sid={self._stream_sid}"
                f" call={self._call_sid} from={mask_phone(self._caller_number)}"
            )
            logger.info("[EXOTEL] start event")
            logger.info(f"[EXOTEL] CallSid={self._call_sid}")
            logger.info(f"[EXOTEL] StreamSid={self._stream_sid}")
            log_call_event(
                "audio.stream.started",
                call_sid=self._call_sid,
                stream_sid=self._stream_sid,
                from_number=mask_phone(self._caller_number),
                had_custom_parameters=bool(custom),
            )
            if self._on_start and self._call_sid is not None:
                await self._on_start(self._call_sid, self._caller_number)

        elif event == "media":
            media = msg.get("media")
            encoded = media.get("payload", "") if isinstance(media, dict) else ""
            if not encoded:
                return
            try:
                audio_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                logger.warning(f"[EXOTEL] dropped undecodable media call_sid={self._call_sid}")
                return
            # Per-packet (~50/s) — DEBUG only, off the hot path in production.
            logger.debug(f"[EXOTEL] media bytes={len(audio_bytes)} call_sid={self._call_sid}")
            await self._on_audio(audio_bytes)

        elif event == "dtmf":
            dtmf = msg.get("dtmf")
            digit = dtmf.get("digit", "") if isinstance(dtmf, dict) else ""
            if digit and self._on_dtmf:
                await self._on_dtmf(digit)

        elif event == "stop":
            self._running = False
            # Exotel's documented AgentStream/Voicebot "stop" event carries
            # no distinguishing reason code (hangup vs. Exotel-initiated
            # vs. error) — logging anything beyond "we received Exotel's own
            # stop event, with this payload" would be invented, not
            # observed. `stop_payload` is logged as-is (empty if Exotel
            # sends nothing beyond the event type) so a live call can show
            # exactly what Exotel actually included, without guessing.
            stop_payload = msg.get("stop")
            stop_keys = sorted(stop_payload) if isinstance(stop_payload, dict) else []
            logger.info(
                f"[EXOTEL_STOP] call_sid={self._call_sid} stream_sid={self._stream_sid} "
                f"source=exotel_websocket_stop_event payload_keys={stop_keys}"
            )
            log_call_event(
                "audio.stream.ended", call_sid=self._call_sid, stream_sid=self._stream_sid
            )
            if self._on_stop:
                await self._on_stop(self._call_sid)

    def _is_connected(self) -> bool:
        """Best-effort check of the underlying WebSocket's state. Only
        blocks when the state is explicitly DISCONNECTED — anything else
        (a real CONNECTED state, a missing attribute on a hand-written test
        fake, or an auto-vivified attribute on a plain Mock/AsyncMock) is
        treated as "proceed", so this never breaks existing tests."""
        app_state = getattr(self._ws, "application_state", None)
        return app_state != WebSocketState.DISCONNECTED

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send μ-law or base64-encoded audio back to the caller."""
        if self._stream_sid is None:
            logger.warning("Dropping outgoing audio: no stream_sid yet (start event not received)")
            return
        if not self._is_connected():
            logger.warning(
                f"Dropping outgoing audio: WebSocket no longer connected call_sid={self._call_sid}"
            )
            return
        payload = base64.b64encode(audio_bytes).decode()
        message = json.dumps(
            {
                "event": "media",
                "stream_sid": self._stream_sid,
                "media": {"payload": payload},
            }
        )
        # Raw PCM16LE/8000Hz/mono, no container — duration is bytes/2/8000s.
        duration_ms = len(audio_bytes) / 2 / 8000 * 1000
        logger.debug(
            f"[OUTBOUND_AUDIO] call_sid={self._call_sid} stream_sid={self._stream_sid} "
            f"bytes={len(audio_bytes)} duration_ms={duration_ms:.0f}"
        )
        # Flushes now run as background tasks, so more than one reply can be
        # ready around the same time — serialize writes to the shared socket.
        async with self._send_lock:
            if not self._is_connected():
                logger.warning(
                    f"Dropping outgoing audio: WebSocket disconnected while "
                    f"waiting to send call_sid={self._call_sid}"
                )
                return
            await self._ws.send_text(message)

    async def clear_audio(self) -> None:
        """Discard Exotel's already-buffered assistant speech on barge-in."""
        if self._stream_sid is None or not self._is_connected():
            return
        async with self._send_lock:
            if self._is_connected():
                await self._ws.send_text(
                    json.dumps({"event": "clear", "stream_sid": self._stream_sid})
                )

    async def stop(self) -> None:
        self._running = False
        if not self._is_connected():
            return
        message = json.dumps({"event": "stop", "stream_sid": self._stream_sid})
        try:
            async with self._send_lock:
                await self._ws.send_text(message)
        except Exception as exc:
            logger.debug(f"Stream stop send failed type={type(exc).__name__}")
