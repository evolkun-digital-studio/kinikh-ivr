"""Tests for app/services/integration_test_service.py — the real,
lightweight, non-billable connection tests behind the Settings page's
"Test Connection" buttons.

Every provider's happy path, auth-failure path, and not-configured path is
covered, plus the safe-error-mapping guarantee (no raw exception text ever
reaches the returned message) and the in-process last-result cache.
"""

from __future__ import annotations

import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai as openai_sdk
import pytest

from app.services import integration_test_service as svc
from app.services.integration_test_service import IntegrationConnectionService, get_last_result


@pytest.fixture(autouse=True)
def _clear_last_results_cache():
    svc._last_results.clear()
    yield
    svc._last_results.clear()


def _fake_settings(**overrides) -> MagicMock:
    settings = MagicMock()
    settings.mock_credentials_used = []
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _openai_error(cls, status_code: int, message: str = "error"):
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    if cls is openai_sdk.APIConnectionError:
        return cls(request=request)
    response = httpx.Response(status_code, request=request)
    return cls(message, response=response, body=None)


class TestNotConfiguredShortCircuit:
    @pytest.mark.asyncio
    async def test_not_configured_never_calls_the_provider(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
            patch.object(service, "_test_openai", AsyncMock()) as handler,
        ):
            result = await service.test("openai")

        handler.assert_not_called()
        assert result.status == "not_configured"
        assert result.provider == "openai"
        assert get_last_result("openai") is result


class TestOpenAI:
    @pytest.mark.asyncio
    async def test_successful_connection(self):
        service = IntegrationConnectionService()
        fake_client = MagicMock()
        fake_client.models.list = AsyncMock(return_value=MagicMock())
        fake_client.close = AsyncMock()

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(openai_api_key="sk-real")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc, "AsyncOpenAI", return_value=fake_client),
        ):
            result = await service.test("openai")

        assert result.status == "connected"
        assert result.provider == "openai"
        fake_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_credentials_returns_safe_failed_message(self):
        service = IntegrationConnectionService()
        fake_client = MagicMock()
        fake_client.models.list = AsyncMock(
            side_effect=_openai_error(openai_sdk.AuthenticationError, 401, "sk-secret-value-leak")
        )
        fake_client.close = AsyncMock()

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(openai_api_key="sk-bad")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc, "AsyncOpenAI", return_value=fake_client),
        ):
            result = await service.test("openai")

        assert result.status == "failed"
        assert "sk-secret-value-leak" not in result.message
        assert "credentials" in result.message.lower()

    @pytest.mark.asyncio
    async def test_missing_credentials_reports_not_configured(self):
        """Missing credentials are caught by the not-configured short circuit,
        never mis-reported as a failed connection attempt."""
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
        ):
            result = await service.test("openai")

        assert result.status == "not_configured"

    @pytest.mark.asyncio
    async def test_connection_error_gives_network_message(self):
        service = IntegrationConnectionService()
        fake_client = MagicMock()
        fake_client.models.list = AsyncMock(
            side_effect=_openai_error(openai_sdk.APIConnectionError, 0)
        )
        fake_client.close = AsyncMock()

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(openai_api_key="sk-real")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc, "AsyncOpenAI", return_value=fake_client),
        ):
            result = await service.test("openai")

        assert result.status == "failed"
        assert "reach openai" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unexpected_exception_never_leaks_into_message(self):
        service = IntegrationConnectionService()
        fake_client = MagicMock()
        fake_client.models.list = AsyncMock(
            side_effect=RuntimeError("Authorization: Bearer sk-leak")
        )
        fake_client.close = AsyncMock()

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(openai_api_key="sk-real")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc, "AsyncOpenAI", return_value=fake_client),
        ):
            result = await service.test("openai")

        assert result.status == "failed"
        assert "sk-leak" not in result.message
        assert "Authorization" not in result.message


class TestElevenLabs:
    @pytest.mark.asyncio
    async def test_successful_connection(self):
        service = IntegrationConnectionService()
        fake_response = MagicMock(status_code=200)
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(elevenlabs_api_key="el-real")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.httpx, "AsyncClient", return_value=fake_http_client),
        ):
            result = await service.test("elevenlabs")

        assert result.status == "connected"

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        service = IntegrationConnectionService()
        fake_response = MagicMock(status_code=401)
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(elevenlabs_api_key="bad")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.httpx, "AsyncClient", return_value=fake_http_client),
        ):
            result = await service.test("elevenlabs")

        assert result.status == "failed"
        assert "api key" in result.message.lower()

    @pytest.mark.asyncio
    async def test_key_valid_but_missing_read_permission_gives_distinct_message(self):
        """A key deliberately scoped to text_to_speech only (no read scopes)
        must not be reported with the generic "check your API key" message —
        that reads as "this key is wrong", when it may work fine for real
        synthesis and only fail this particular lightweight check."""
        service = IntegrationConnectionService()
        fake_response = MagicMock(status_code=401)
        fake_response.json.return_value = {
            "detail": {
                "type": "authentication_error",
                "code": "unauthorized",
                "message": "The API key you used is missing the permission user_read.",
                "status": "missing_permissions",
            }
        }
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(elevenlabs_api_key="scoped-key")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.httpx, "AsyncClient", return_value=fake_http_client),
        ):
            result = await service.test("elevenlabs")

        assert result.status == "failed"
        assert "valid but" in result.message.lower()
        assert "does not necessarily mean" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unparsable_401_body_falls_back_to_generic_message(self):
        service = IntegrationConnectionService()
        fake_response = MagicMock(status_code=401)
        fake_response.json.side_effect = ValueError("not json")
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                svc, "reload_settings", return_value=_fake_settings(elevenlabs_api_key="bad")
            ),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.httpx, "AsyncClient", return_value=fake_http_client),
        ):
            result = await service.test("elevenlabs")

        assert result.status == "failed"
        assert "check your api key" in result.message.lower()

    @pytest.mark.asyncio
    async def test_missing_credentials_reports_not_configured(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
        ):
            result = await service.test("elevenlabs")

        assert result.status == "not_configured"


class TestExotel:
    @pytest.mark.asyncio
    async def test_successful_connection(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.ExotelClient, "test_connection", AsyncMock(return_value=(200, "{}"))),
        ):
            result = await service.test("exotel")

        assert result.status == "connected"

    @pytest.mark.asyncio
    async def test_authentication_failure(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(
                svc.ExotelClient, "test_connection", AsyncMock(return_value=(401, "denied"))
            ),
        ):
            result = await service.test("exotel")

        assert result.status == "failed"
        assert "credentials" in result.message.lower()

    @pytest.mark.asyncio
    async def test_kyc_or_account_configuration_incomplete(self):
        """Authenticated (not 401/403/404) but not fully usable — distinct
        message from a plain authentication failure."""
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.ExotelClient, "test_connection", AsyncMock(return_value=(400, "kyc"))),
        ):
            result = await service.test("exotel")

        assert result.status == "failed"
        assert "authenticated" in result.message.lower()

    @pytest.mark.asyncio
    async def test_missing_credentials_reports_not_configured(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
        ):
            result = await service.test("exotel")

        assert result.status == "not_configured"

    @pytest.mark.asyncio
    async def test_unexpected_exception_never_leaks_credentials_into_message(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(
                svc.ExotelClient,
                "test_connection",
                AsyncMock(side_effect=RuntimeError("https://key123:token456@api.exotel.com/...")),
            ),
        ):
            result = await service.test("exotel")

        assert result.status == "failed"
        assert "key123" not in result.message
        assert "token456" not in result.message


class TestEmail:
    @pytest.mark.asyncio
    async def test_successful_connection(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.SMTPClient, "test_connection", AsyncMock(return_value=None)),
        ):
            result = await service.test("email")

        assert result.status == "connected"

    @pytest.mark.asyncio
    async def test_authentication_failure(self):
        service = IntegrationConnectionService()
        auth_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(svc.SMTPClient, "test_connection", AsyncMock(side_effect=auth_error)),
        ):
            result = await service.test("email")

        assert result.status == "failed"
        assert "username/password" in result.message.lower()

    @pytest.mark.asyncio
    async def test_missing_configuration_reports_not_configured(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
        ):
            result = await service.test("email")

        assert result.status == "not_configured"

    @pytest.mark.asyncio
    async def test_connection_refused_gives_safe_message_not_raw_exception(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=True),
            patch.object(
                svc.SMTPClient,
                "test_connection",
                AsyncMock(side_effect=ConnectionRefusedError("[Errno 61] Connection refused")),
            ),
        ):
            result = await service.test("email")

        assert result.status == "failed"
        assert "Errno" not in result.message


class TestLastResultCache:
    @pytest.mark.asyncio
    async def test_test_records_result_retrievable_via_get_last_result(self):
        service = IntegrationConnectionService()
        with (
            patch.object(svc, "reload_settings", return_value=_fake_settings()),
            patch.object(svc, "is_provider_configured", return_value=False),
        ):
            result = await service.test("email")

        assert get_last_result("email") == result

    def test_get_last_result_returns_none_when_never_tested(self):
        assert get_last_result("openai") is None
