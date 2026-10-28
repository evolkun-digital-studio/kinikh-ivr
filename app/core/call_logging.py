"""Structured, safe diagnostic logging for the live inbound-call pipeline.

Added to trace exactly how far a real Exotel call gets through
Exotel → webhook → call creation → AI session → voice → finalization →
persistence, without guessing. Each event is one JSON-encoded log line so a
single real call's log output can be read top-to-bottom.

Never log: API keys, tokens, passwords, webhook secrets, Authorization
headers, or a full caller phone number. `mask_phone` must be used on any
caller-identifying number before it's passed to `log_call_event`.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


def mask_phone(number: str | None) -> str:
    """Keep enough of a phone number to correlate log lines, never enough to
    identify the caller: leading digits (country/prefix) + last 2 digits."""
    if not number:
        return "unknown"
    if len(number) <= 5:
        return "*" * len(number)
    return f"{number[:3]}{'*' * (len(number) - 5)}{number[-2:]}"


def log_call_event(event: str, **fields: Any) -> None:
    """Emit one structured, greppable log line for a call-lifecycle event.

    `fields` values are logged as-is — callers are responsible for never
    passing a credential or an unmasked phone number.
    """
    logger.info(json.dumps({"event": event, **fields}, default=str))


def describe_error(exc: BaseException) -> str:
    """Log-safe description of a provider/transport failure: the exception
    type plus HTTP status when there is one — never its message. Provider
    error messages can echo request data: OpenAI's auth errors include a
    partially masked API key, SMTP errors include addresses, and HTTP client
    errors include URLs with query strings."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    name = type(exc).__name__
    return f"{name} status={status}" if isinstance(status, int) else name
