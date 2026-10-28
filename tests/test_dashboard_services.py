"""Behavioral tests for the dashboard-support service methods added during the
admin dashboard build: filtered/paginated listing, aggregate counts, cost
summaries, and the honest provider-configuration status logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class TestCallServiceDashboardMethods:
    @pytest.mark.asyncio
    async def test_list_filtered_returns_items_and_total(self):
        from app.models.call import Call
        from app.services.call_service import CallService

        session = AsyncMock(spec=AsyncSession)
        service = CallService(session)
        calls = [Call(call_sid="A", caller_number="+91000", status="completed")]

        with (
            patch.object(service._repo, "list_filtered", AsyncMock(return_value=calls)),
            patch.object(service._repo, "count_filtered", AsyncMock(return_value=1)),
        ):
            items, total = await service.list_filtered(status="completed", limit=10, offset=0)

        assert items == calls
        assert total == 1

    @pytest.mark.asyncio
    async def test_dashboard_counts_shape(self):
        from app.services.call_service import CallService

        session = AsyncMock(spec=AsyncSession)
        service = CallService(session)

        with (
            patch.object(service._repo, "count", AsyncMock(return_value=10)),
            patch.object(service._repo, "count_by_status", AsyncMock(side_effect=[6, 2])),
            patch.object(service._repo, "count_created_since", AsyncMock(return_value=3)),
            patch.object(service._repo, "average_duration_seconds", AsyncMock(return_value=42.5)),
        ):
            counts = await service.dashboard_counts()

        assert counts == {
            "total_calls": 10,
            "completed_calls": 6,
            "failed_calls": 2,
            "today_calls": 3,
            "average_duration_seconds": 42.5,
        }

    @pytest.mark.asyncio
    async def test_dashboard_counts_handles_empty_database(self):
        """An empty database must report zeros/None, never fabricated numbers."""
        from app.services.call_service import CallService

        session = AsyncMock(spec=AsyncSession)
        service = CallService(session)

        with (
            patch.object(service._repo, "count", AsyncMock(return_value=0)),
            patch.object(service._repo, "count_by_status", AsyncMock(return_value=0)),
            patch.object(service._repo, "count_created_since", AsyncMock(return_value=0)),
            patch.object(service._repo, "average_duration_seconds", AsyncMock(return_value=None)),
        ):
            counts = await service.dashboard_counts()

        assert counts["total_calls"] == 0
        assert counts["average_duration_seconds"] is None


class TestLeadServiceDashboardMethods:
    @pytest.mark.asyncio
    async def test_list_filtered_returns_items_and_total(self):
        from app.models.lead import Lead
        from app.services.lead_service import LeadService

        session = AsyncMock(spec=AsyncSession)
        service = LeadService(session)
        leads = [Lead(phone="+91000", name="Test")]

        with (
            patch.object(service._repo, "list_filtered", AsyncMock(return_value=leads)),
            patch.object(service._repo, "count_filtered", AsyncMock(return_value=1)),
        ):
            items, total = await service.list_filtered(search="Test", limit=10, offset=0)

        assert items == leads
        assert total == 1

    @pytest.mark.asyncio
    async def test_dashboard_counts(self):
        from app.services.lead_service import LeadService

        session = AsyncMock(spec=AsyncSession)
        service = LeadService(session)

        with (
            patch.object(service._repo, "count", AsyncMock(return_value=5)),
            patch.object(service._repo, "count_created_since", AsyncMock(return_value=1)),
        ):
            counts = await service.dashboard_counts()

        assert counts == {"total_leads": 5, "today_leads": 1}


class TestEmailServiceDashboardMethods:
    @pytest.mark.asyncio
    async def test_list_filtered_returns_items_and_total(self):
        from app.models.email_log import EmailLog
        from app.services.email_service import EmailService

        session = AsyncMock(spec=AsyncSession)
        service = EmailService(session)
        logs = [EmailLog(recipient_email="a@b.com", subject="Hi", template_name="t", status="sent")]

        with (
            patch.object(service._log_repo, "list_filtered", AsyncMock(return_value=logs)),
            patch.object(service._log_repo, "count_filtered", AsyncMock(return_value=1)),
        ):
            items, total = await service.list_filtered(status="sent", limit=10, offset=0)

        assert items == logs
        assert total == 1

    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        from app.services.email_service import EmailService

        session = AsyncMock(spec=AsyncSession)
        service = EmailService(session)

        with patch.object(
            service._log_repo, "counts_by_status", AsyncMock(return_value={"sent": 2, "failed": 1})
        ):
            counts = await service.counts_by_status()

        assert counts == {"sent": 2, "failed": 1}


class TestUsageTrackingServiceCostSummary:
    @pytest.mark.asyncio
    async def test_cost_summary_computes_cost_per_call(self):
        from app.services.usage_tracking_service import UsageTrackingService

        session = AsyncMock(spec=AsyncSession)
        service = UsageTrackingService(session)

        totals = {
            "telephony_usd": 1.0,
            "openai_usd": 2.0,
            "voice_usd": 0.5,
            "email_usd": 0.1,
            "total_usd": 3.6,
            "call_count": 4,
        }
        provider_rows = [("openai", 10, 1000, 0, 2.0)]

        with (
            patch.object(service._cost_repo, "totals", AsyncMock(return_value=totals)),
            patch.object(
                service._usage_repo, "totals_by_provider", AsyncMock(return_value=provider_rows)
            ),
        ):
            summary = await service.cost_summary()

        assert summary["totals"]["cost_per_call_usd"] == 0.9
        assert summary["by_provider"][0]["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_cost_summary_zero_calls_does_not_divide_by_zero(self):
        """An empty database must not raise ZeroDivisionError computing cost/call."""
        from app.services.usage_tracking_service import UsageTrackingService

        session = AsyncMock(spec=AsyncSession)
        service = UsageTrackingService(session)

        totals = {
            "telephony_usd": 0.0,
            "openai_usd": 0.0,
            "voice_usd": 0.0,
            "email_usd": 0.0,
            "total_usd": 0.0,
            "call_count": 0,
        }

        with (
            patch.object(service._cost_repo, "totals", AsyncMock(return_value=totals)),
            patch.object(service._usage_repo, "totals_by_provider", AsyncMock(return_value=[])),
        ):
            summary = await service.cost_summary()

        assert summary["totals"]["cost_per_call_usd"] == 0.0
        assert summary["by_provider"] == []


class TestDepartmentServiceDashboardMethods:
    @pytest.mark.asyncio
    async def test_list_all_returns_active_and_inactive(self):
        from app.models.department import Department
        from app.services.department_service import DepartmentService

        session = AsyncMock(spec=AsyncSession)
        service = DepartmentService(session)
        depts = [
            Department(name="a", display_name="A", email="a@b.com", is_active=True),
            Department(name="b", display_name="B", email="b@b.com", is_active=False),
        ]

        with patch.object(service._repo, "list_all", AsyncMock(return_value=depts)):
            result = await service.list_all()

        assert result == depts

    @pytest.mark.asyncio
    async def test_activate_sets_is_active_true(self):
        from app.models.department import Department
        from app.services.department_service import DepartmentService

        session = AsyncMock(spec=AsyncSession)
        service = DepartmentService(session)
        dept = Department(name="a", display_name="A", email="a@b.com", is_active=False)
        activated = Department(name="a", display_name="A", email="a@b.com", is_active=True)

        with (
            patch.object(service._repo, "get_or_raise", AsyncMock(return_value=dept)),
            patch.object(service._repo, "update", AsyncMock(return_value=activated)) as update_mock,
        ):
            result = await service.activate(dept.id)

        update_mock.assert_awaited_once_with(dept, is_active=True)
        assert result.is_active is True
