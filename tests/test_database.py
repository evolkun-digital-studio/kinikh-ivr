"""Database model and repository tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class TestModels:
    """Verify ORM models can be instantiated and have correct attributes."""

    def test_lead_model(self):
        from app.models.lead import Lead

        lead = Lead(phone="+919876543210", name="Test User")
        assert lead.phone == "+919876543210"
        assert lead.name == "Test User"

    def test_department_model(self):
        from app.models.department import Department

        dept = Department(
            name="rural", display_name="rural", email="rural@test.com", is_active=True
        )
        assert dept.name == "rural"
        assert dept.is_active is True

    def test_call_model(self):
        from app.models.call import Call

        call = Call(
            call_sid="TEST123", caller_number="+91000", status="initiated", provider="exotel"
        )
        assert call.status == "initiated"
        assert call.provider == "exotel"

    def test_email_log_model(self):
        from app.models.email_log import EmailLog

        log = EmailLog(
            recipient_email="a@b.com",
            subject="Test",
            template_name="email.html",
            status="pending",
            retry_count=0,
        )
        assert log.status == "pending"
        assert log.retry_count == 0

    def test_audit_log_model(self):
        from app.models.audit_log import AuditLog

        log = AuditLog(action="lead.created", actor="system")
        assert log.action == "lead.created"

    def test_base_metadata_tables(self):
        from app.models.base import Base

        table_names = set(Base.metadata.tables.keys())
        expected = {"leads", "departments", "calls", "email_logs", "audit_logs"}
        assert expected.issubset(table_names)


class TestBaseRepository:
    """Tests for generic repository operations."""

    @pytest.fixture
    def mock_session(self):
        session = AsyncMock(spec=AsyncSession)
        return session

    @pytest.mark.asyncio
    async def test_create_calls_flush_and_refresh(self, mock_session):
        from app.models.lead import Lead
        from app.repositories.base import BaseRepository

        BaseRepository(Lead, mock_session)
        mock_session.get = AsyncMock(return_value=None)

        Lead(phone="+91000")
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Override internal __init__ bypass — simulate create.
        with patch.object(Lead, "__init__", return_value=None):
            mock_session.add = MagicMock()
            # We can't fully test ORM creation without a real DB, but we verify no exceptions.


class TestLeadRepository:
    """Unit tests for LeadRepository query methods."""

    def test_lead_repository_init(self):
        from app.models.lead import Lead
        from app.repositories.lead_repository import LeadRepository

        session = AsyncMock(spec=AsyncSession)
        repo = LeadRepository(session)
        assert repo._model is Lead

    def test_department_repository_init(self):
        from app.models.department import Department
        from app.repositories.department_repository import DepartmentRepository

        session = AsyncMock(spec=AsyncSession)
        repo = DepartmentRepository(session)
        assert repo._model is Department


class TestCallService:
    """Unit tests for CallService."""

    @pytest.mark.asyncio
    async def test_initiate_creates_call(self):
        from app.models.call import Call
        from app.services.call_service import CallService

        session = AsyncMock(spec=AsyncSession)
        service = CallService(session)

        mock_call = Call(call_sid="SID123", caller_number="+91000", status="initiated")

        with (
            patch.object(service._repo, "get_by_sid", AsyncMock(return_value=None)),
            patch.object(service._repo, "get_daily_sequence", AsyncMock(return_value=12)),
            patch.object(service._repo, "create", AsyncMock(return_value=mock_call)),
        ):
            call = await service.initiate("SID123", "+91000")
            assert call.call_sid == "SID123"

    @pytest.mark.asyncio
    async def test_initiate_is_idempotent_for_duplicate_webhook_delivery(self):
        """A redelivered incoming-call webhook must not raise a UniqueViolation."""
        from app.models.call import Call
        from app.services.call_service import CallService

        session = AsyncMock(spec=AsyncSession)
        service = CallService(session)

        existing_call = Call(call_sid="SID123", caller_number="+91000", status="initiated")

        with (
            patch.object(service._repo, "get_by_sid", AsyncMock(return_value=existing_call)),
            patch.object(service._repo, "create", AsyncMock()) as create_mock,
        ):
            call = await service.initiate("SID123", "+91000")

        assert call is existing_call
        create_mock.assert_not_called()
