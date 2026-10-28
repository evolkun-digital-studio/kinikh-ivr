"""Tests for ExotelClient's REST API authentication.

Exotel's documented auth for this API is Account SID + API Key (Basic Auth
username) + API Token (Basic Auth password) —
`https://<api_key>:<api_token>@<subdomain>/v1/Accounts/<sid>/...`. A
previous version of this client incorrectly used `exotel_api_secret` as the
Basic Auth password instead of `exotel_token`. These tests lock in the fix
and guard against regressing back to the wrong field.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_settings(**overrides):
    settings = MagicMock()
    settings.exotel_sid = "kinikh1"
    settings.exotel_api_key = "fake-api-key"
    settings.exotel_token = "fake-api-token"
    settings.exotel_subdomain = "api.exotel.com"
    settings.exotel_api_secret = None
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class TestExotelClientAuth:
    def test_base_url_uses_api_key_and_token_not_api_secret(self):
        from app.telephony.exotel.client import ExotelClient

        settings = _fake_settings(exotel_api_secret="should-never-appear")
        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            client = ExotelClient()

        assert (
            client._base_url
            == "https://fake-api-key:fake-api-token@api.exotel.com/v1/Accounts/kinikh1"
        )
        assert "should-never-appear" not in client._base_url

    def test_construction_succeeds_when_api_secret_is_none(self):
        """exotel_api_secret is deprecated/optional — the client must not
        require it to construct at all."""
        from app.telephony.exotel.client import ExotelClient

        settings = _fake_settings(exotel_api_secret=None)
        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            client = ExotelClient()

        assert client._sid == "kinikh1"
        assert not hasattr(client, "_api_secret")

    def test_construction_does_not_read_api_secret_attribute(self):
        """Even if a settings object doesn't define exotel_api_secret at
        all, construction must succeed — the client never touches it."""
        from app.telephony.exotel.client import ExotelClient

        settings = MagicMock(
            spec=["exotel_sid", "exotel_api_key", "exotel_token", "exotel_subdomain"]
        )
        settings.exotel_sid = "kinikh1"
        settings.exotel_api_key = "fake-api-key"
        settings.exotel_token = "fake-api-token"
        settings.exotel_subdomain = "api.exotel.com"

        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            ExotelClient()  # must not raise AttributeError


class TestExotelClientTestConnection:
    @pytest.mark.asyncio
    async def test_test_connection_requests_account_resource(self):
        from app.telephony.exotel.client import ExotelClient

        settings = _fake_settings()
        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            client = ExotelClient()

        fake_response = MagicMock(status_code=200, text="{}")
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.telephony.exotel.client.httpx.AsyncClient", return_value=fake_http_client):
            status_code, body = await client.test_connection()

        assert status_code == 200
        requested_url = fake_http_client.get.call_args[0][0]
        assert (
            requested_url
            == "https://fake-api-key:fake-api-token@api.exotel.com/v1/Accounts/kinikh1.json"
        )
        assert "fake-api-token" not in body  # response body, not the URL — sanity check only

    @pytest.mark.asyncio
    async def test_test_connection_returns_status_code_on_auth_failure(self):
        from app.telephony.exotel.client import ExotelClient

        settings = _fake_settings()
        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            client = ExotelClient()

        fake_response = MagicMock(status_code=401, text="Unauthorized")
        fake_http_client = AsyncMock()
        fake_http_client.get = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.telephony.exotel.client.httpx.AsyncClient", return_value=fake_http_client):
            status_code, _body = await client.test_connection()

        assert status_code == 401

    @pytest.mark.asyncio
    async def test_test_connection_never_calls_make_call_hangup_or_get_call(self):
        """Guard against the test accidentally becoming a real telephony
        operation — it must only ever perform the plain account GET."""
        from app.telephony.exotel.client import ExotelClient

        settings = _fake_settings()
        with patch("app.telephony.exotel.client.get_settings", return_value=settings):
            client = ExotelClient()

        with (
            patch.object(client, "make_call", AsyncMock()) as make_call,
            patch.object(client, "hangup", AsyncMock()) as hangup,
            patch.object(client, "get_call", AsyncMock()) as get_call,
            patch(
                "app.telephony.exotel.client.httpx.AsyncClient",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(
                        return_value=AsyncMock(
                            get=AsyncMock(return_value=MagicMock(status_code=200, text="{}"))
                        )
                    ),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ),
        ):
            await client.test_connection()

        make_call.assert_not_called()
        hangup.assert_not_called()
        get_call.assert_not_called()
