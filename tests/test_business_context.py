"""Tests for app/ai/business_context.py — connecting the existing
app/tenants/ business data (and/or DB-sourced departments) to the
receptionist's system prompt, without inventing any new business facts.
"""

from __future__ import annotations

from app.ai.business_context import (
    BusinessContext,
    DepartmentContext,
    build_business_context,
    render_business_context,
)
from app.tenants.email_mapping import DEPARTMENT_EMAIL_MAP
from app.tenants.knowledge_base import KNOWLEDGE_BASES
from app.tenants.prompts import DEPARTMENT_PROMPTS
from app.tenants.routing_rules import ROUTING_RULES


class TestBuildBusinessContext:
    def test_empty_departments_falls_back_to_tenants_data(self):
        """The current live state: the DB `departments` table has 0 rows.
        With no raw_departments supplied, every department name/email/
        prompt/knowledge_base/keyword must come from the existing
        app/tenants/*.py constants — nothing invented."""
        context = build_business_context([])

        names = {d.name for d in context.departments}
        assert names == set(ROUTING_RULES.keys())

        rural = next(d for d in context.departments if d.name == "rural")
        assert rural.email == DEPARTMENT_EMAIL_MAP["rural"]
        assert rural.prompt == DEPARTMENT_PROMPTS["rural"]
        assert rural.knowledge_base == KNOWLEDGE_BASES["rural"]
        assert rural.keywords == ROUTING_RULES["rural"]

    def test_none_departments_also_falls_back(self):
        context = build_business_context(None)
        assert len(context.departments) == len(ROUTING_RULES)

    def test_db_departments_used_when_present(self):
        """A real DB row's own values must be used exactly as given —
        no substitution when the DB already supplies the field."""
        raw = [
            {
                "name": "rural",
                "display_name": "Enterprise rural",
                "email": "enterprise-rural@kinikh.com",
                "prompt": "Custom DB-authored rural prompt.",
                "knowledge_base": "Custom DB-authored rural knowledge.",
                "keywords": ["custom-keyword"],
            }
        ]
        context = build_business_context(raw)

        assert len(context.departments) == 1
        dept = context.departments[0]
        assert dept.display_name == "Enterprise rural"
        assert dept.email == "enterprise-rural@kinikh.com"
        assert dept.prompt == "Custom DB-authored rural prompt."
        assert dept.knowledge_base == "Custom DB-authored rural knowledge."
        assert dept.keywords == ["custom-keyword"]

    def test_db_department_missing_fields_falls_back_per_field(self):
        """A DB row that only has name/email (matching the pre-fix
        SpeechHandler behavior) must still get its prompt/knowledge_base/
        keywords filled in from app/tenants/ by name — never left blank if
        the tenants module has real data for that department."""
        raw = [{"name": "support", "email": "support@kinikh.com"}]
        context = build_business_context(raw)

        dept = context.departments[0]
        assert dept.email == "support@kinikh.com"  # DB value kept
        assert dept.prompt == DEPARTMENT_PROMPTS["support"]  # tenants fallback
        assert dept.knowledge_base == KNOWLEDGE_BASES["support"]  # tenants fallback
        assert dept.keywords == ROUTING_RULES["support"]  # tenants fallback

    def test_unknown_department_name_has_no_fabricated_data(self):
        """A department name with no corresponding tenants entry must not
        have prompt/knowledge_base/keywords invented for it."""
        raw = [{"name": "unknown_dept", "email": "x@kinikh.com"}]
        context = build_business_context(raw)

        dept = context.departments[0]
        assert dept.prompt is None
        assert dept.knowledge_base is None
        assert dept.keywords == []

    def test_company_config_always_present(self):
        context = build_business_context([])
        assert context.company.name  # from settings.company_name


class TestRenderBusinessContext:
    def test_render_includes_company_name(self):
        context = build_business_context([])
        text = render_business_context(context)
        assert f"Company: {context.company.name}" in text

    def test_render_includes_department_names(self):
        context = build_business_context([])
        text = render_business_context(context)
        for dept in context.departments:
            assert dept.display_name in text

    def test_render_includes_knowledge_base_text(self):
        context = build_business_context([])
        text = render_business_context(context)
        # Actual rural knowledge-base content, not invented.
        assert "organic produce" in text

    def test_render_includes_department_instructions(self):
        context = build_business_context([])
        text = render_business_context(context)
        assert "Focus on rural enterprise" in text

    def test_render_includes_routing_keywords(self):
        context = build_business_context([])
        text = render_business_context(context)
        assert "dairy" in text and "milk" in text  # from ROUTING_RULES["rural"]

    def test_render_includes_contact_email(self):
        context = build_business_context([])
        text = render_business_context(context)
        assert "rural@kinikh.com" in text

    def test_render_with_no_departments_says_so_rather_than_inventing(self):
        context = BusinessContext(company=build_business_context([]).company, departments=[])
        text = render_business_context(context)
        assert "No department information is currently configured." in text

    def test_render_omits_fields_that_are_genuinely_absent(self):
        """A department with no knowledge_base/prompt/keywords must not have
        those section headers fabricated with empty/placeholder content."""
        context = BusinessContext(
            company=build_business_context([]).company,
            departments=[DepartmentContext(name="x", display_name="X", email=None)],
        )
        text = render_business_context(context)
        assert "Knowledge:" not in text
        assert "Instructions:" not in text
        assert "Routing keywords:" not in text
        assert "email:" not in text
