"""Exotel webhook parser and request authenticator."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import hashlib
import hmac
import ipaddress
from typing import Any

from app.core.config import get_settings
from app.telephony.adapter import CallInfo, TelephonyAdapter
from app.telephony.exotel.client import ExotelClient

# Query parameter carrying the shared webhook secret in the callback URL
# configured in the Exotel flow. Stripped from the payload before parsing or
# logging — it is a credential, never call data.
WEBHOOK_TOKEN_PARAM = "webhook_token"
SIGNATURE_HEADER = "x-exotel-signature"


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    """Exotel's applets name the same field differently (the Voicebot
    resolver sends CallFrom/CallTo, Passthru and status callbacks send
    From/To, Status vs CallStatus) — take the first one present."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_duration(raw: Any) -> int | None:
    """Exotel sends durations as strings; anything malformed is treated as
    absent rather than raising a 500 on the callback."""
    if raw in (None, ""):
        return None
    try:
        seconds = int(float(raw))
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _basic_auth_password(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    _, sep, password = decoded.partition(":")
    return password if sep else None


def _secret_matches(candidate: str, secret: str) -> bool:
    return hmac.compare_digest(candidate.encode(), secret.encode())


def _ip_allowed(client_ip: str | None) -> bool:
    networks = get_settings().exotel_allowed_networks
    if not networks:
        return True
    if not client_ip:
        return False
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


class ExotelAdapter(TelephonyAdapter):
    """Exotel implementation of TelephonyAdapter."""

    def __init__(self) -> None:
        self._client = ExotelClient()

    async def answer_call(self, call_sid: str) -> dict[str, Any]:
        return {"CallSid": call_sid, "action": "answered"}

    async def hangup_call(self, call_sid: str) -> None:
        await self._client.hangup(call_sid)

    async def send_digits(self, call_sid: str, digits: str) -> None:
        raise NotImplementedError("Exotel does not support mid-call DTMF injection via API")

    def parse_incoming_webhook(self, payload: dict[str, Any]) -> CallInfo:
        return CallInfo(
            call_sid=payload.get("CallSid", ""),
            caller_number=_first(payload, "From", "CallFrom") or "",
            callee_number=_first(payload, "To", "CallTo"),
            status=_first(payload, "CallStatus", "Status") or "initiated",
            direction=payload.get("Direction", "inbound"),
            raw=payload,
        )

    def parse_status_webhook(self, payload: dict[str, Any]) -> CallInfo:
        return CallInfo(
            call_sid=payload.get("CallSid", ""),
            caller_number=_first(payload, "From", "CallFrom") or "",
            callee_number=_first(payload, "To", "CallTo"),
            status=_first(payload, "CallStatus", "Status") or "unknown",
            direction=payload.get("Direction", "inbound"),
            duration=_parse_duration(
                _first(payload, "CallDuration", "ConversationDuration", "Duration")
            ),
            recording_url=_first(payload, "RecordingUrl"),
            raw=payload,
        )

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        secret = get_settings().exotel_webhook_secret.encode()
        expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def authenticate_webhook(
        self,
        *,
        body: bytes,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        client_ip: str | None,
    ) -> bool:
        """Decide whether a webhook request really comes from our Exotel flow.

        Exotel does not sign its webhooks (developer.exotel.com, "Webhook
        Authentication & Security"), so a native signature cannot be
        required. Accepted proofs of the shared EXOTEL_WEBHOOK_SECRET, in
        order: our HMAC-SHA256 body signature (a trusted signing proxy), the
        `webhook_token` query parameter configured in the Exotel flow URL, or
        HTTP Basic auth whose password is the secret. A signature header
        that is present but wrong is a hard rejection — it never falls
        through to the other forms. When EXOTEL_ALLOWED_CIDRS is set the
        source address must also be inside it.
        """
        if not _ip_allowed(client_ip):
            return False
        secret = get_settings().exotel_webhook_secret
        signature = headers.get(SIGNATURE_HEADER)
        if signature is not None:
            return self.verify_webhook_signature(body, signature)
        token = query.get(WEBHOOK_TOKEN_PARAM)
        if token:
            return _secret_matches(token, secret)
        password = _basic_auth_password(headers.get("authorization"))
        if password:
            return _secret_matches(password, secret)
        return False
