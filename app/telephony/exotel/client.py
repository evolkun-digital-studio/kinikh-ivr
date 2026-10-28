"""Exotel REST API client with retry logic."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from app.core.config import get_settings
from app.core.exceptions import TelephonyError

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds


class ExotelClient:
    """Low-level async HTTP client for Exotel v2 API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._sid = settings.exotel_sid
        self._api_key = settings.exotel_api_key
        self._token = settings.exotel_token
        self._subdomain = settings.exotel_subdomain
        # Exotel's documented REST auth: API Key as the Basic Auth username,
        # API Token as the password — https://<api_key>:<api_token>@<subdomain>/...
        # (exotel_api_secret is deprecated and deliberately not used here.)
        self._base_url = (
            f"https://{self._api_key}:{self._token}@{self._subdomain}/v1/Accounts/{self._sid}"
        )

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_exc: Exception = RuntimeError("No attempt made")

        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(_MAX_RETRIES):
                try:
                    response = await client.request(method, url, data=data)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    logger.warning(
                        f"Exotel HTTP error {exc.response.status_code} attempt={attempt + 1}"
                    )
                    if exc.response.status_code < 500:
                        raise TelephonyError("Exotel API request rejected") from exc
                except httpx.RequestError as exc:
                    last_exc = exc
                    logger.warning(
                        f"Exotel request error attempt={attempt + 1} type={type(exc).__name__}"
                    )

                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))

        raise TelephonyError(f"Exotel request failed after {_MAX_RETRIES} attempts") from last_exc

    async def make_call(
        self,
        from_number: str,
        to_number: str,
        callback_url: str,
        status_callback_url: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "From": from_number,
            "To": to_number,
            "CallerId": get_settings().exotel_caller_id,
            "Url": callback_url,
        }
        if status_callback_url:
            data["StatusCallback"] = status_callback_url
        return await self._request("POST", "/Calls/connect.json", data=data)

    async def send_sms(self, to_number: str, text: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "From": get_settings().exotel_caller_id,
            "To": to_number,
            "Body": text,
        }
        return await self._request("POST", "/Sms/send.json", data=data)

    async def hangup(self, call_sid: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/Calls/{call_sid}.json",
            data={"Status": "completed"},
        )

    async def get_call(self, call_sid: str) -> dict[str, Any]:
        return await self._request("GET", f"/Calls/{call_sid}.json")

    async def test_connection(self) -> tuple[int, str]:
        """Lightweight authenticated account read for connection testing.

        Bypasses `_request`'s retry/error-wrapping (which discards the HTTP
        status code) so callers can distinguish auth failure from other
        problems. Single attempt, no retries. Never initiates a call, sends
        an SMS, purchases an ExoPhone, or changes any call flow — this is a
        plain read of the account resource. The request URL carries the
        API key/token as Basic Auth via userinfo (same convention as
        `_base_url` above) and must never be logged.
        """
        url = f"{self._base_url}.json"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            return response.status_code, response.text
