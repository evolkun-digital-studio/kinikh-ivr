"""Tests for the shared provider-configuration detection and explicit
settings-reload mechanism in app/core/config.py.

This logic used to live duplicated inside app/api/v1/system.py; it now
backs both `/system/status` and the integration connection-test service
(`app/services/integration_test_service.py`), so it's tested independently
here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings, is_mock_value, is_provider_configured, reload_settings


@pytest.fixture
def restore_settings_cache():
    """`get_settings()` is a process-wide `lru_cache`. Any test that forces a
    reload with a monkeypatched environment variable must not leak that
    cached instance into later tests once the monkeypatch reverts."""
    yield
    get_settings.cache_clear()
    get_settings()


class TestIsMockValue:
    def _settings(self, **overrides):
        settings = MagicMock()
        settings.mock_credentials_used = overrides.pop("mock_credentials_used", [])
        for key, value in overrides.items():
            setattr(settings, key, value)
        return settings

    def test_flagged_via_mock_credentials_used(self):
        settings = self._settings(
            mock_credentials_used=["openai_api_key"], openai_api_key="sk-dev-mock-not-a-real-key"
        )
        assert is_mock_value("openai_api_key", settings) is True

    def test_flagged_when_explicitly_set_to_known_mock_value(self):
        """Value present in `.env` but literally equal to the mock placeholder."""
        settings = self._settings(
            mock_credentials_used=[], openai_api_key="sk-dev-mock-not-a-real-key"
        )
        assert is_mock_value("openai_api_key", settings) is True

    def test_not_flagged_when_real_value_set(self):
        settings = self._settings(mock_credentials_used=[], openai_api_key="sk-live-real-key-xyz")
        assert is_mock_value("openai_api_key", settings) is False


class TestIsProviderConfigured:
    def _settings(self, **overrides):
        settings = MagicMock()
        settings.mock_credentials_used = overrides.pop("mock_credentials_used", [])
        for key, value in overrides.items():
            setattr(settings, key, value)
        return settings

    def test_reports_not_configured_for_openai_mock_credentials(self):
        settings = self._settings(
            mock_credentials_used=[],
            openai_api_key="sk-dev-mock-not-a-real-key",
            elevenlabs_api_key="el-real-key",
            elevenlabs_voice_id_en="real-voice-en",
            elevenlabs_voice_id_hi="real-voice-hi",
            exotel_sid="real-sid",
            exotel_api_key="real-api-key",
            exotel_token="real-token",
            smtp_username="real@company.com",
            smtp_password="real-password",
            smtp_from_email="real@company.com",
        )

        assert is_provider_configured("openai", settings) is False
        assert is_provider_configured("exotel", settings) is True
        assert is_provider_configured("elevenlabs", settings) is True
        assert is_provider_configured("email", settings) is True

    def test_exotel_configured_check_ignores_caller_id_webhook_secret_and_api_secret(self):
        """exotel_caller_id (outbound-only) and exotel_webhook_secret (an
        app-side secret, never an Exotel credential) must not gate whether
        Exotel is reported as configured — and the deprecated
        exotel_api_secret must not either, since it's unused."""
        settings = self._settings(
            mock_credentials_used=[],
            exotel_sid="real-sid",
            exotel_api_key="real-api-key",
            exotel_token="real-token",
            exotel_caller_id="+910000000000",  # still the dev-mock placeholder
            exotel_webhook_secret="dev-mock-webhook-secret",  # still the dev-mock placeholder
            exotel_api_secret="dev-mock-api-secret",  # deprecated field, still mock
        )

        assert is_provider_configured("exotel", settings) is True

    def test_exotel_not_configured_when_sid_is_still_mock(self):
        settings = self._settings(
            mock_credentials_used=[],
            exotel_sid="dev-mock-sid",
            exotel_api_key="real-api-key",
            exotel_token="real-token",
        )
        assert is_provider_configured("exotel", settings) is False

    def test_exotel_not_configured_when_api_key_is_still_mock(self):
        settings = self._settings(
            mock_credentials_used=[],
            exotel_sid="real-sid",
            exotel_api_key="dev-mock-api-key",
            exotel_token="real-token",
        )
        assert is_provider_configured("exotel", settings) is False

    def test_exotel_not_configured_when_token_is_still_mock(self):
        settings = self._settings(
            mock_credentials_used=[],
            exotel_sid="real-sid",
            exotel_api_key="real-api-key",
            exotel_token="dev-mock-token",
        )
        assert is_provider_configured("exotel", settings) is False

    def test_unknown_provider_reports_configured(self):
        """No fields defined for an unknown provider -> vacuously configured;
        the API layer's Enum path param is what actually rejects bad names."""
        settings = self._settings(mock_credentials_used=[])
        assert is_provider_configured("not-a-real-provider", settings) is True


class TestReloadSettings:
    def test_reload_picks_up_a_changed_environment_variable(
        self, monkeypatch, restore_settings_cache
    ):
        get_settings.cache_clear()
        monkeypatch.setenv("COMPANY_NAME", "Original Co")
        assert get_settings().company_name == "Original Co"

        # get_settings() alone must NOT pick up the change — only an
        # explicit reload does.
        monkeypatch.setenv("COMPANY_NAME", "Updated Co")
        assert get_settings().company_name == "Original Co"

        reloaded = reload_settings()
        assert reloaded.company_name == "Updated Co"
        assert get_settings().company_name == "Updated Co"


class TestExotelApiSecretDeprecated:
    """`exotel_api_secret` was the Basic Auth password in a previous (wrong)
    version of ExotelClient. Exotel's actual REST auth is API Key + API
    Token; exotel_api_secret must no longer be required to boot the app."""

    def test_exotel_api_secret_is_optional(self):
        from app.core.config import Settings

        field = Settings.model_fields["exotel_api_secret"]
        assert field.is_required() is False
        assert field.default is None

    def test_exotel_token_is_still_required(self):
        """exotel_token went from a dead field to the real Basic Auth
        password — it must remain a required field, not become optional."""
        from app.core.config import Settings

        assert Settings.model_fields["exotel_token"].is_required() is True

    def test_exotel_sid_and_api_key_still_required(self):
        from app.core.config import Settings

        assert Settings.model_fields["exotel_sid"].is_required() is True
        assert Settings.model_fields["exotel_api_key"].is_required() is True
