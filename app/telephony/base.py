"""BaseTelephonyProvider — extended abstract base for telephony providers."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from app.telephony.adapter import CallInfo, TelephonyAdapter


class BaseTelephonyProvider(TelephonyAdapter):
    """Extends TelephonyAdapter with outbound call and transfer capabilities.

    Concrete providers must implement all abstract methods. Optional methods
    (transfer_call) raise NotImplementedError by default.
    """

    @abstractmethod
    async def make_call(
        self,
        from_number: str,
        to_number: str,
        callback_url: str,
        status_callback_url: str | None = None,
    ) -> CallInfo:
        """Initiate an outbound call. Returns CallInfo with the provider's call ID."""

    @abstractmethod
    async def get_call(self, call_sid: str) -> CallInfo:
        """Fetch current call details from the provider."""

    async def transfer_call(self, call_sid: str, destination: str) -> None:
        """Transfer an active call to a new destination.

        Raises:
            NotImplementedError: if the provider does not support call transfer.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support call transfer")

    @abstractmethod
    async def answer_call(self, call_sid: str) -> dict[str, Any]: ...

    @abstractmethod
    async def hangup_call(self, call_sid: str) -> None: ...

    @abstractmethod
    async def send_digits(self, call_sid: str, digits: str) -> None: ...

    @abstractmethod
    def parse_incoming_webhook(self, payload: dict[str, Any]) -> CallInfo: ...

    @abstractmethod
    def parse_status_webhook(self, payload: dict[str, Any]) -> CallInfo: ...

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool: ...
