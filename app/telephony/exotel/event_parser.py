"""Exotel event parser — typed dataclasses for stream and webhook events."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
from typing import Any

from loguru import logger

from app.telephony.adapter import CallInfo

# ---------------------------------------------------------------------------
# Stream event types (WebSocket)
# ---------------------------------------------------------------------------


@dataclass
class ExotelStreamEvent:
    """Base class for all Exotel WebSocket stream events."""

    event_type: str
    raw: dict[str, Any]


@dataclass
class StreamConnectedEvent(ExotelStreamEvent):
    """Sent once when the WebSocket connection is established."""


@dataclass
class StreamStartEvent(ExotelStreamEvent):
    """Sent when Exotel begins streaming audio for a call."""

    stream_sid: str = ""
    call_sid: str = ""


@dataclass
class StreamMediaEvent(ExotelStreamEvent):
    """Carries a chunk of μ-law audio from the caller."""

    stream_sid: str = ""
    audio_bytes: bytes = field(default_factory=bytes)
    track: str = "inbound"


@dataclass
class StreamDtmfEvent(ExotelStreamEvent):
    """Carries a DTMF digit pressed by the caller."""

    digit: str = ""


@dataclass
class StreamStopEvent(ExotelStreamEvent):
    """Sent when the audio stream is terminated."""

    stream_sid: str = ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class ExotelEventParser:
    """Parses raw JSON messages from Exotel into typed event objects."""

    def parse_stream_message(self, raw: str) -> ExotelStreamEvent | None:
        """Parse a raw WebSocket message string into a typed stream event.

        Returns None if the message is malformed or the event type is unknown.
        """
        try:
            msg: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.debug(f"[event_parser] invalid JSON: {exc}")
            return None

        event_type = msg.get("event", "")

        if event_type == "connected":
            return StreamConnectedEvent(event_type=event_type, raw=msg)

        if event_type == "start":
            start = msg.get("start", {})
            return StreamStartEvent(
                event_type=event_type,
                raw=msg,
                stream_sid=str(msg.get("streamSid", "")),
                call_sid=str(start.get("callSid", "")),
            )

        if event_type == "media":
            media = msg.get("media", {})
            encoded = media.get("payload", "")
            audio_bytes = base64.b64decode(encoded) if encoded else b""
            return StreamMediaEvent(
                event_type=event_type,
                raw=msg,
                stream_sid=str(msg.get("streamSid", "")),
                audio_bytes=audio_bytes,
                track=str(media.get("track", "inbound")),
            )

        if event_type == "dtmf":
            return StreamDtmfEvent(
                event_type=event_type,
                raw=msg,
                digit=str(msg.get("dtmf", {}).get("digit", "")),
            )

        if event_type == "stop":
            return StreamStopEvent(
                event_type=event_type,
                raw=msg,
                stream_sid=str(msg.get("streamSid", "")),
            )

        logger.debug(f"[event_parser] unknown event type: {event_type!r}")
        return None

    def parse_incoming_call(self, payload: dict[str, Any]) -> CallInfo:
        """Parse an incoming-call webhook payload into a CallInfo."""
        return CallInfo(
            call_sid=str(payload.get("CallSid", "")),
            caller_number=str(payload.get("From", "")),
            callee_number=payload.get("To"),
            status=str(payload.get("CallStatus", "initiated")),
            direction=str(payload.get("Direction", "inbound")),
            raw=payload,
        )

    def parse_status_change(self, payload: dict[str, Any]) -> CallInfo:
        """Parse a status-change webhook payload into a CallInfo."""
        raw_duration = payload.get("CallDuration")
        return CallInfo(
            call_sid=str(payload.get("CallSid", "")),
            caller_number=str(payload.get("From", "")),
            callee_number=payload.get("To"),
            status=str(payload.get("CallStatus", "unknown")),
            direction=str(payload.get("Direction", "inbound")),
            duration=int(raw_duration) if raw_duration else None,
            recording_url=payload.get("RecordingUrl"),
            raw=payload,
        )
