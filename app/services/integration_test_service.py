"""Real, lightweight, non-billable connection tests for each external
integration, plus an in-process cache of the most recent result per provider.

Every test method:
  - reloads configuration explicitly (`reload_settings()`) so a credential
    just fixed in `.env` is picked up without restarting the process;
  - performs the cheapest authenticated read the provider's API offers
    (never a chat completion, TTS synthesis, phone call, SMS, or email send);
  - never raises a raw provider exception to the caller — every failure is
    mapped to a fixed, safe, human-readable message. Provider SDK/HTTP
    exceptions can embed request/response details (headers, URLs) that may
    carry credentials, so only the exception's *type name* is ever logged,
    never `str(exc)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import smtplib

import httpx
from loguru import logger
import openai as openai_sdk
from openai import AsyncOpenAI

from app.core.config import Settings, is_provider_configured, reload_settings
from app.email_system.smtp_client import SMTPClient
from app.telephony.exotel.client import ExotelClient

Status = str  # "connected" | "failed" | "not_configured"


@dataclass(frozen=True)
class IntegrationTestResult:
    provider: str
    status: Status
    message: str
    tested_at: str


# In-process cache of the last test result per provider. Explicitly NOT
# Redis and NOT a DB table — nothing else in this project uses Redis, and a
# restart legitimately resetting every provider to "not yet verified this
# session" is the correct behavior, not a bug to work around with
# persistence. Never holds credentials, only the fields on the dataclass
# above.
_last_results: dict[str, IntegrationTestResult] = {}


def get_last_result(provider: str) -> IntegrationTestResult | None:
    return _last_results.get(provider)


def _record(result: IntegrationTestResult) -> IntegrationTestResult:
    _last_results[result.provider] = result
    return result


def _is_elevenlabs_missing_permission(response: httpx.Response) -> bool:
    """True if ElevenLabs rejected the request because the key lacks a read
    scope (e.g. a key deliberately restricted to `text_to_speech` only) —
    distinct from an invalid/revoked key. Never raises: an unparsable body
    just means "can't tell", so the caller falls back to the generic
    auth-failure message.
    """
    try:
        detail = response.json().get("detail", {})
        return detail.get("status") == "missing_permissions"
    except Exception:
        return False


class IntegrationConnectionService:
    """Runs an explicit connection test for one provider on demand."""

    async def test(self, provider: str) -> IntegrationTestResult:
        settings = reload_settings()

        if not is_provider_configured(provider, settings):
            return _record(
                self._result(
                    provider,
                    "not_configured",
                    "Not configured — using development placeholder credentials.",
                )
            )

        handler = {
            "openai": self._test_openai,
            "elevenlabs": self._test_elevenlabs,
            "exotel": self._test_exotel,
            "email": self._test_email,
        }[provider]
        result = await handler(settings)
        return _record(result)

    def _result(self, provider: str, status: Status, message: str) -> IntegrationTestResult:
        return IntegrationTestResult(
            provider=provider,
            status=status,
            message=message,
            tested_at=datetime.now(UTC).isoformat(),
        )

    def _ok(self, provider: str) -> IntegrationTestResult:
        return self._result(provider, "connected", "Connection successful.")

    def _fail(self, provider: str, message: str) -> IntegrationTestResult:
        return self._result(provider, "failed", message)

    # -- OpenAI ---------------------------------------------------------

    async def _test_openai(self, settings: Settings) -> IntegrationTestResult:
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=10, max_retries=0)
        try:
            await client.models.list()
            return self._ok("openai")
        except openai_sdk.AuthenticationError:
            return self._fail("openai", "Authentication failed. Please check your API credentials.")
        except openai_sdk.PermissionDeniedError:
            return self._fail(
                "openai",
                "Credentials authenticated, but access to the required API is not permitted.",
            )
        except (openai_sdk.APIConnectionError, openai_sdk.APITimeoutError):
            return self._fail(
                "openai", "Could not reach OpenAI. Please check network connectivity."
            )
        except Exception as exc:
            logger.warning(f"[integration-test] openai unexpected error: {type(exc).__name__}")
            return self._fail("openai", "Connection failed. Please check your configuration.")
        finally:
            await client.close()

    # -- ElevenLabs -------------------------------------------------------

    async def _test_elevenlabs(self, settings: Settings) -> IntegrationTestResult:
        headers = {"xi-api-key": settings.elevenlabs_api_key}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get("https://api.elevenlabs.io/v1/user", headers=headers)
        except httpx.RequestError:
            return self._fail(
                "elevenlabs", "Could not reach ElevenLabs. Please check network connectivity."
            )
        except Exception as exc:
            logger.warning(f"[integration-test] elevenlabs unexpected error: {type(exc).__name__}")
            return self._fail("elevenlabs", "Connection failed. Please check your configuration.")

        if response.status_code == 200:
            return self._ok("elevenlabs")
        if response.status_code in (401, 403):
            if _is_elevenlabs_missing_permission(response):
                return self._fail(
                    "elevenlabs",
                    "API key is valid but is scoped without read access, so this check can't "
                    "verify it. This does not necessarily mean text-to-speech requests fail — "
                    "grant the key read access in ElevenLabs to allow full verification.",
                )
            return self._fail("elevenlabs", "Authentication failed. Please check your API key.")
        return self._fail(
            "elevenlabs", f"Unexpected response from ElevenLabs (HTTP {response.status_code})."
        )

    # -- Exotel -----------------------------------------------------------

    async def _test_exotel(self, settings: Settings) -> IntegrationTestResult:
        client = ExotelClient()
        try:
            status_code, _body = await client.test_connection()
        except Exception as exc:
            logger.warning(f"[integration-test] exotel unexpected error: {type(exc).__name__}")
            return self._fail(
                "exotel", "Could not reach Exotel. Please check network connectivity."
            )

        if 200 <= status_code < 300:
            return self._ok("exotel")
        if status_code in (401, 403):
            return self._fail("exotel", "Authentication failed. Please check your API credentials.")
        if status_code == 404:
            return self._fail("exotel", "Credentials rejected, or the Account SID was not found.")
        return self._fail(
            "exotel",
            "Credentials authenticated, but ExoPhone/calling configuration is incomplete.",
        )

    # -- Email / SMTP -------------------------------------------------------

    async def _test_email(self, settings: Settings) -> IntegrationTestResult:
        client = SMTPClient()
        try:
            await client.test_connection()
            return self._ok("email")
        except smtplib.SMTPAuthenticationError:
            return self._fail(
                "email", "Authentication failed. Please check your SMTP username/password."
            )
        except Exception as exc:
            logger.warning(f"[integration-test] email unexpected error: {type(exc).__name__}")
            return self._fail(
                "email",
                "Could not connect to the SMTP server. Please check host/port/TLS settings.",
            )
