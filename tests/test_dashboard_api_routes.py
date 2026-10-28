"""HTTP-level tests for the new dashboard-support routes (calls, recordings,
emails, usage, analytics, dashboard, system, and the extended leads/departments
endpoints). Mirrors the dependency-override pattern used in
test_telephony_webhook_validation.py: override `get_db`/`get_current_user_id`
and patch the service layer, since these routes are protected and DB-backed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
import uuid

from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user_id, get_db
from app.main import app
from app.models.call import Call
from app.models.department import Department
from app.models.lead import Lead


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user_id] = lambda: "dev-admin@example.com"
    return TestClient(app, raise_server_exceptions=False)


class _Teardown:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()


class TestCallsRoutes(_Teardown):
    def test_list_calls_returns_paginated_envelope(self):
        from app.services.call_service import CallService

        call = Call(
            id=uuid.uuid4(),
            call_sid="SID1",
            caller_number="+91000",
            status="completed",
            direction="inbound",
            provider="exotel",
            created_at=datetime.now(UTC),
        )
        client = _client()
        with patch.object(CallService, "list_filtered", AsyncMock(return_value=([call], 1))):
            response = client.get("/api/v1/calls")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["call_sid"] == "SID1"

    def test_list_calls_empty_database_returns_empty_list_not_error(self):
        from app.services.call_service import CallService

        client = _client()
        with patch.object(CallService, "list_filtered", AsyncMock(return_value=([], 0))):
            response = client.get("/api/v1/calls")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_get_call_not_found_returns_404(self):
        from app.core.exceptions import NotFoundError
        from app.services.call_service import CallService

        client = _client()
        with patch.object(
            CallService, "get_by_id_or_raise", AsyncMock(side_effect=NotFoundError("Call"))
        ):
            response = client.get(f"/api/v1/calls/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_invalid_sort_param_rejected(self):
        client = _client()
        response = client.get("/api/v1/calls?sort=not_a_column")
        assert response.status_code == 422


class TestRecordingsRoutes(_Teardown):
    def test_list_recordings_empty(self):
        from app.services.call_recording_service import CallRecordingService

        client = _client()
        with patch.object(CallRecordingService, "list_filtered", AsyncMock(return_value=([], 0))):
            response = client.get("/api/v1/recordings")

        assert response.status_code == 200
        assert response.json()["total"] == 0


class TestEmailsRoutes(_Teardown):
    def test_list_emails_empty(self):
        from app.services.email_service import EmailService

        client = _client()
        with patch.object(EmailService, "list_filtered", AsyncMock(return_value=([], 0))):
            response = client.get("/api/v1/emails")

        assert response.status_code == 200
        assert response.json()["items"] == []


class TestUsageRoutes(_Teardown):
    def test_summary_route(self):
        from app.services.usage_tracking_service import UsageTrackingService

        summary = {
            "totals": {
                "telephony_usd": 0.0,
                "openai_usd": 0.0,
                "voice_usd": 0.0,
                "email_usd": 0.0,
                "total_usd": 0.0,
                "call_count": 0,
                "cost_per_call_usd": 0.0,
            },
            "by_provider": [],
        }
        client = _client()
        with patch.object(UsageTrackingService, "cost_summary", AsyncMock(return_value=summary)):
            response = client.get("/api/v1/usage/summary")

        assert response.status_code == 200
        assert response.json() == summary


class TestAnalyticsRoutes(_Teardown):
    def test_overview_route_empty_database(self):
        from app.services.call_service import CallService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService
        from app.services.usage_tracking_service import UsageTrackingService

        client = _client()
        with (
            patch.object(CallService, "calls_over_time", AsyncMock(return_value=[])),
            patch.object(LeadService, "leads_over_time", AsyncMock(return_value=[])),
            patch.object(CallService, "counts_by_department", AsyncMock(return_value=[])),
            patch.object(CallService, "counts_by_language", AsyncMock(return_value={})),
            patch.object(CallService, "completed_vs_failed", AsyncMock(return_value={})),
            patch.object(EmailService, "counts_by_status", AsyncMock(return_value={})),
            patch.object(UsageTrackingService, "cost_timeseries", AsyncMock(return_value=[])),
        ):
            response = client.get("/api/v1/analytics/overview")

        assert response.status_code == 200
        body = response.json()
        assert body["calls_over_time"] == []
        assert body["calls_by_language"] == {}


class TestDashboardRoutes(_Teardown):
    def test_summary_route(self):
        from app.services.call_service import CallService
        from app.services.lead_service import LeadService
        from app.services.usage_tracking_service import UsageTrackingService

        call_counts = {
            "total_calls": 0,
            "completed_calls": 0,
            "failed_calls": 0,
            "today_calls": 0,
            "average_duration_seconds": None,
        }
        lead_counts = {"total_leads": 0, "today_leads": 0}
        cost_summary = {"totals": {"total_usd": 0.0}, "by_provider": []}

        client = _client()
        with (
            patch.object(CallService, "dashboard_counts", AsyncMock(return_value=call_counts)),
            patch.object(LeadService, "dashboard_counts", AsyncMock(return_value=lead_counts)),
            patch.object(
                UsageTrackingService, "cost_summary", AsyncMock(return_value=cost_summary)
            ),
            patch.object(CallService, "list_filtered", AsyncMock(return_value=([], 0))),
            patch.object(LeadService, "list_filtered", AsyncMock(return_value=([], 0))),
        ):
            response = client.get("/api/v1/dashboard/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["kpis"]["total_calls"] == 0
        assert body["recent_calls"] == []
        assert body["recent_leads"] == []


class TestSystemRoutes(_Teardown):
    def test_status_route_reflects_settings(self):
        client = _client()
        response = client.get("/api/v1/system/status")

        assert response.status_code == 200
        body = response.json()
        assert body["database"] in {"connected", "error"}
        assert {p["provider"] for p in body["providers"]} == {
            "openai",
            "elevenlabs",
            "exotel",
            "email",
        }


class TestLeadsRoutesExtended(_Teardown):
    def test_list_leads_paginated_envelope(self):
        from app.services.lead_service import LeadService

        lead = Lead(id=uuid.uuid4(), phone="+91000", name="Test", created_at=datetime.now(UTC))
        client = _client()
        with patch.object(LeadService, "list_filtered", AsyncMock(return_value=([lead], 1))):
            response = client.get("/api/v1/leads")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["phone"] == "+91000"


class TestDepartmentsRoutesExtended(_Teardown):
    def test_list_departments_includes_counts(self):
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.lead_service import LeadService

        dept = Department(
            id=uuid.uuid4(),
            name="rural",
            display_name="rural",
            email="rural@test.com",
            is_active=True,
        )
        client = _client()
        with (
            patch.object(DepartmentService, "list_all", AsyncMock(return_value=[dept])),
            patch.object(LeadService, "counts_by_department", AsyncMock(return_value={})),
            patch.object(CallService, "counts_by_department", AsyncMock(return_value=[])),
        ):
            response = client.get("/api/v1/departments")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["call_count"] == 0
        assert body[0]["lead_count"] == 0

    def test_activate_route(self):
        from app.services.department_service import DepartmentService

        dept = Department(
            id=uuid.uuid4(),
            name="rural",
            display_name="rural",
            email="rural@test.com",
            is_active=True,
        )
        client = _client()
        with patch.object(DepartmentService, "activate", AsyncMock(return_value=dept)):
            response = client.post(f"/api/v1/departments/{uuid.uuid4()}/activate")

        assert response.status_code == 200
        assert response.json()["is_active"] is True


class TestUnauthorizedAccess(_Teardown):
    def test_calls_route_requires_auth_header(self):
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/calls")
        assert response.status_code in (401, 422)
