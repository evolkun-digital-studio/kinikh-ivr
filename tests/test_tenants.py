"""Tenant config and repository import/smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class TestTenantData:
    def test_knowledge_bases_loaded(self):
        from app.tenants.knowledge_base import KNOWLEDGE_BASES

        assert "rural" in KNOWLEDGE_BASES
        assert "support" in KNOWLEDGE_BASES
        assert isinstance(KNOWLEDGE_BASES["rural"], str)

    def test_routing_rules_loaded(self):
        from app.tenants.routing_rules import ROUTING_RULES, keyword_route

        assert "rural" in ROUTING_RULES
        assert isinstance(ROUTING_RULES["rural"], list)
        assert keyword_route("I want to buy some milk") == "rural"
        assert keyword_route("complete gibberish xyz") is None

    def test_email_mapping_lookup(self):
        from app.tenants.email_mapping import DEPARTMENT_EMAIL_MAP, get_department_email

        assert "rural" in DEPARTMENT_EMAIL_MAP
        assert get_department_email("rural") == "rural@kinikh.com"
        assert get_department_email("unknown") is None

    def test_department_prompts_loaded(self):
        from app.tenants.prompts import DEPARTMENT_PROMPTS

        assert "rural" in DEPARTMENT_PROMPTS
        assert len(DEPARTMENT_PROMPTS["rural"]) > 0

    def test_company_config_dataclass(self):
        from app.tenants.company_config import CompanyConfig

        cfg = CompanyConfig(name="Test Co", website="https://test.com", phone="+910000")
        assert cfg.name == "Test Co"
        assert cfg.default_language == "en"
        assert "en" in cfg.supported_languages
        assert cfg.business_hours_start == 9


class TestAuditLogRepository:
    def test_audit_log_repository_init(self):
        from app.models.audit_log import AuditLog
        from app.repositories.audit_log_repository import AuditLogRepository

        session = AsyncMock(spec=AsyncSession)
        repo = AuditLogRepository(session)
        assert repo._model is AuditLog

    @pytest.mark.asyncio
    async def test_log_method_exists(self):
        from app.repositories.audit_log_repository import AuditLogRepository

        session = AsyncMock(spec=AsyncSession)
        repo = AuditLogRepository(session)
        assert callable(repo.log)
