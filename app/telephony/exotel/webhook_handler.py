"""ExotelWebhookHandler — validates and dispatches Exotel webhook payloads."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.core.call_logging import mask_phone
from app.core.exceptions import AuthenticationError
from app.telephony.base import BaseTelephonyProvider
from app.telephony.exotel.call_manager import CallManager

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "busy", "no-answer", "canceled"}
)


class ExotelWebhookHandler:
    """Validates signatures and dispatches incoming-call / status-change webhooks.

    Keeps CallManager in sync: registers new calls on incoming events and
    removes / updates them on status-change events.
    """

    def __init__(
        self,
        provider: BaseTelephonyProvider,
        call_manager: CallManager,
        *,
        verify_signatures: bool = True,
    ) -> None:
        self._provider = provider
        self._call_manager = call_manager
        self._verify = verify_signatures

    def _check_signature(self, payload: dict[str, Any], signature: str | None) -> None:
        """Raise AuthenticationError if signature validation fails."""
        if not self._verify:
            return
        if signature is None:
            raise AuthenticationError("Missing Exotel webhook signature")
        raw = json.dumps(payload, separators=(",", ":")).encode()
        if not self._provider.verify_webhook_signature(raw, signature):
            raise AuthenticationError("Invalid Exotel webhook signature")

    async def handle_incoming(
        self,
        payload: dict[str, Any],
        signature: str | None = None,
    ) -> dict[str, Any]:
        """Process an incoming-call webhook.

        Validates signature, parses call details, registers the call in
        CallManager, and returns an acknowledgement dict.
        """
        self._check_signature(payload, signature)
        call_info = self._provider.parse_incoming_webhook(payload)
        logger.info(
            f"[webhook] incoming call_sid={call_info.call_sid}"
            f" from={mask_phone(call_info.caller_number)}"
        )
        await self._call_manager.register(call_info.call_sid, call_info.caller_number)
        return {"status": "accepted", "call_sid": call_info.call_sid}

    async def handle_status(
        self,
        payload: dict[str, Any],
        signature: str | None = None,
    ) -> dict[str, Any]:
        """Process a call status-change webhook.

        Validates signature, parses updated call details, and removes the call
        from CallManager on terminal statuses or updates its status otherwise.
        """
        self._check_signature(payload, signature)
        call_info = self._provider.parse_status_webhook(payload)
        logger.info(
            f"[webhook] status update call_sid={call_info.call_sid}" f" status={call_info.status}"
        )
        if call_info.status in _TERMINAL_STATUSES:
            await self._call_manager.remove(call_info.call_sid)
        else:
            await self._call_manager.update_status(call_info.call_sid, call_info.status)
        return {"status": "ok", "call_sid": call_info.call_sid}
