"""ExotelProvider — full BaseTelephonyProvider implementation for Exotel."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from loguru import logger

from app.core.call_logging import mask_phone
from app.core.config import get_settings
from app.core.exceptions import TelephonyError
from app.telephony.adapter import CallInfo
from app.telephony.base import BaseTelephonyProvider
from app.telephony.exotel.client import ExotelClient
from app.telephony.exotel.event_parser import ExotelEventParser


class ExotelProvider(BaseTelephonyProvider):
    """Exotel implementation of BaseTelephonyProvider.

    Uses ExotelClient for REST API calls and ExotelEventParser for parsing
    incoming webhook payloads.
    """

    def __init__(
        self,
        client: ExotelClient | None = None,
        parser: ExotelEventParser | None = None,
    ) -> None:
        self._client = client or ExotelClient()
        self._parser = parser or ExotelEventParser()

    # ------------------------------------------------------------------
    # Call control
    # ------------------------------------------------------------------

    async def answer_call(self, call_sid: str) -> dict[str, Any]:
        """Return an acknowledgement dict (Exotel answers via WebSocket, not REST)."""
        logger.info(f"[exotel_provider] answer call_sid={call_sid}")
        return {"CallSid": call_sid, "action": "answered"}

    async def hangup_call(self, call_sid: str) -> None:
        """Terminate an active call via Exotel REST API."""
        logger.info(f"[exotel_provider] hangup call_sid={call_sid}")
        await self._client.hangup(call_sid)

    async def make_call(
        self,
        from_number: str,
        to_number: str,
        callback_url: str,
        status_callback_url: str | None = None,
    ) -> CallInfo:
        """Initiate an outbound call and return its CallInfo."""
        logger.info(
            f"[exotel_provider] make_call from={mask_phone(from_number)} to={mask_phone(to_number)}"
        )
        data = await self._client.make_call(
            from_number, to_number, callback_url, status_callback_url
        )
        call = data.get("Call", data)
        return CallInfo(
            call_sid=str(call.get("Sid", "")),
            caller_number=str(call.get("From", from_number)),
            callee_number=str(call.get("To", to_number)),
            status=str(call.get("Status", "queued")),
            direction="outbound",
            raw=data,
        )

    async def get_call(self, call_sid: str) -> CallInfo:
        """Fetch current call details from the Exotel API."""
        data = await self._client.get_call(call_sid)
        call = data.get("Call", data)
        raw_duration = call.get("Duration")
        return CallInfo(
            call_sid=str(call.get("Sid", call_sid)),
            caller_number=str(call.get("From", "")),
            callee_number=call.get("To"),
            status=str(call.get("Status", "unknown")),
            direction=str(call.get("Direction", "inbound")),
            duration=int(raw_duration) if raw_duration else None,
            recording_url=call.get("RecordingUrl"),
            raw=data,
        )

    async def send_digits(self, call_sid: str, digits: str) -> None:
        raise NotImplementedError("Exotel does not support mid-call DTMF injection via API")

    # ------------------------------------------------------------------
    # Webhook parsing
    # ------------------------------------------------------------------

    def parse_incoming_webhook(self, payload: dict[str, Any]) -> CallInfo:
        return self._parser.parse_incoming_call(payload)

    def parse_status_webhook(self, payload: dict[str, Any]) -> CallInfo:
        return self._parser.parse_status_change(payload)

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify the HMAC-SHA256 signature on an Exotel webhook payload."""
        try:
            secret = get_settings().exotel_webhook_secret.encode()
            expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as exc:
            logger.error(f"[exotel_provider] signature verification failed: {exc}")
            raise TelephonyError(f"Webhook signature error: {exc}") from exc
