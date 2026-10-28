"""Abstract telephony provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CallInfo:
    call_sid: str
    caller_number: str
    callee_number: str | None
    status: str
    direction: str = "inbound"
    duration: int | None = None
    recording_url: str | None = None
    raw: dict[str, Any] | None = None


class TelephonyAdapter(ABC):
    """Contract every telephony provider must satisfy."""

    @abstractmethod
    async def answer_call(self, call_sid: str) -> dict[str, Any]:
        """Return provider-specific response to answer an incoming call."""

    @abstractmethod
    async def hangup_call(self, call_sid: str) -> None:
        """Terminate an active call."""

    @abstractmethod
    async def send_digits(self, call_sid: str, digits: str) -> None:
        """Send DTMF digits on a call."""

    @abstractmethod
    def parse_incoming_webhook(self, payload: dict[str, Any]) -> CallInfo:
        """Deserialise an incoming webhook payload into a CallInfo."""

    @abstractmethod
    def parse_status_webhook(self, payload: dict[str, Any]) -> CallInfo:
        """Deserialise a status-change webhook payload into a CallInfo."""

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Return True if the webhook signature is valid."""
