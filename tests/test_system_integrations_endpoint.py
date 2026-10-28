"""HTTP-level tests for POST /api/v1/system/integrations/{provider}/test
and the updated GET /api/v1/system/status.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user_id, get_db
from app.main import app
from app.services.integration_test_service import (
    IntegrationConnectionService,
    IntegrationTestResult,
)


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user_id] = lambda: "dev-admin@example.com"
    return TestClient(app, raise_server_exceptions=False)


class _Teardown:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()
        from app.services import integration_test_service as svc

        svc._last_results.clear()


class TestIntegrationTestEndpoint(_Teardown):
    def test_authorized_access_returns_result(self):
        client = _client()
        fake_result = IntegrationTestResult(
            provider="openai", status="connected", message="Connection successful.", tested_at="t"
        )
        with patch.object(
            IntegrationConnectionService, "test", AsyncMock(return_value=fake_result)
        ):
            response = client.post("/api/v1/system/integrations/openai/test")

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "provider": "openai",
            "status": "connected",
            "message": "Connection successful.",
            "tested_at": "t",
        }

    def test_unauthorized_access_is_rejected(self):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/api/v1/system/integrations/openai/test")
        assert response.status_code in (401, 422)

    def test_invalid_provider_is_rejected(self):
        client = _client()
        response = client.post("/api/v1/system/integrations/not-a-real-provider/test")
        assert response.status_code == 422

    def test_failed_result_never_leaks_a_secret_shaped_string(self):
        client = _client()
        fake_result = IntegrationTestResult(
            provider="exotel", status="failed", message="Authentication failed.", tested_at="t"
        )
        with patch.object(
            IntegrationConnectionService, "test", AsyncMock(return_value=fake_result)
        ):
            response = client.post("/api/v1/system/integrations/exotel/test")

        assert response.status_code == 200
        body_text = response.text
        assert "sk-" not in body_text
        assert "Bearer" not in body_text
        assert "password" not in body_text.lower()

    def test_all_four_providers_are_valid_path_values(self):
        client = _client()
        for provider in ("openai", "elevenlabs", "exotel", "email"):
            fake_result = IntegrationTestResult(
                provider=provider, status="not_configured", message="Not configured.", tested_at="t"
            )
            with patch.object(
                IntegrationConnectionService, "test", AsyncMock(return_value=fake_result)
            ):
                response = client.post(f"/api/v1/system/integrations/{provider}/test")
            assert response.status_code == 200, provider
            assert response.json()["provider"] == provider


class TestSystemStatusReflectsLastTestResult(_Teardown):
    def test_configured_but_never_tested_reports_not_verified(self):
        client = _client()
        with patch("app.api.v1.system.is_provider_configured", return_value=True):
            response = client.get("/api/v1/system/status")

        assert response.status_code == 200
        for provider_status in response.json()["providers"]:
            assert provider_status["status"] == "not_verified"
            assert provider_status["configured"] is True
            assert provider_status["last_tested_at"] is None

    def test_status_reflects_most_recent_test_result(self):
        from app.services import integration_test_service as svc

        client = _client()
        # Populate the cache the way a real test run does (via the service's
        # own recording path), not by mocking test() itself — mocking the
        # whole method would bypass _record() and prove nothing about
        # /system/status actually reading the cache.
        svc._record(
            IntegrationTestResult(
                provider="openai",
                status="failed",
                message="Authentication failed.",
                tested_at="2026-01-01T00:00:00Z",
            )
        )

        with patch("app.api.v1.system.is_provider_configured", return_value=True):
            response = client.get("/api/v1/system/status")

        openai_status = next(p for p in response.json()["providers"] if p["provider"] == "openai")
        assert openai_status["status"] == "failed"
        assert openai_status["detail"] == "Authentication failed."
        assert openai_status["last_tested_at"] == "2026-01-01T00:00:00Z"

    def test_not_configured_always_wins_over_a_stale_cached_result(self):
        """If .env now shows the provider as unconfigured, an old cached
        'connected' result from before must never be shown as current."""
        from app.services import integration_test_service as svc

        client = _client()
        svc._record(
            IntegrationTestResult(
                provider="openai",
                status="connected",
                message="Connection successful.",
                tested_at="t",
            )
        )

        with patch("app.api.v1.system.is_provider_configured", return_value=False):
            response = client.get("/api/v1/system/status")

        openai_status = next(p for p in response.json()["providers"] if p["provider"] == "openai")
        assert openai_status["status"] == "not_configured"
        assert openai_status["configured"] is False
        assert openai_status["last_tested_at"] is None
