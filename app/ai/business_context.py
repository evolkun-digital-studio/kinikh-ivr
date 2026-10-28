"""Builds the business-context block supplied to the receptionist's system
prompt: company info + per-department email/instructions/knowledge/routing.

Prefers live PostgreSQL `departments` data (so admins can edit it via the
dashboard later), but falls back to the existing app/tenants/ scaffolding
whenever the database has no active departments — which is the current
live state (the departments table has 0 rows and scripts/seed.py has never
been run). This is a fallback, not a replacement: nothing here invents any
business fact — every value comes from either a DB row or the existing
app/tenants/*.py constants.

The receptionist answers basic company/service questions using RAG.
In full mode, the complete RAG context is loaded here. In retrieval mode,
context is injected per-turn in SpeechHandler.
whether departments are configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field


from app.tenants.company_config import CompanyConfig, get_company_config
from app.tenants.email_mapping import DEPARTMENT_EMAIL_MAP
from app.tenants.knowledge_base import KNOWLEDGE_BASES
from app.tenants.prompts import DEPARTMENT_PROMPTS
from app.tenants.routing_rules import ROUTING_RULES


@dataclass(frozen=True)
class DepartmentContext:
    name: str
    display_name: str
    email: str | None = None
    prompt: str | None = None
    knowledge_base: str | None = None
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "email": self.email,
            "prompt": self.prompt,
            "knowledge_base": self.knowledge_base,
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True)
class BusinessContext:
    company: CompanyConfig
    departments: list[DepartmentContext]


def build_business_context(
    raw_departments: list[dict[str, object]] | None = None,
) -> BusinessContext:
    """raw_departments: dicts as read from the DB (name, display_name, email,
    prompt, knowledge_base, keywords). Each field is enriched from the
    app/tenants/ constants only when the DB value is missing/empty — DB data
    always wins when present. Pass None/[] to use the tenants module as the
    sole source (today's live state)."""
    company = get_company_config()

    if raw_departments:
        departments = [
            DepartmentContext(
                name=str(d["name"]),
                display_name=str(d.get("display_name") or str(d["name"]).title()),
                email=d.get("email") or DEPARTMENT_EMAIL_MAP.get(str(d["name"])),
                prompt=d.get("prompt") or DEPARTMENT_PROMPTS.get(str(d["name"])),
                knowledge_base=d.get("knowledge_base") or KNOWLEDGE_BASES.get(str(d["name"])),
                keywords=list(d.get("keywords") or ROUTING_RULES.get(str(d["name"]), [])),
            )
            for d in raw_departments
        ]
    else:
        # No departments configured in the database yet — use the existing
        # app/tenants/ scaffolding directly so the receptionist still has
        # real business data instead of an empty department list.
        DISPLAY_NAMES = {
            "rural": "KINIKH Rural",
            "stay": "Slow Tuesday",
            "tech": "evolKun",
            "brand": "BrandWorks",
            "infra": "Kinikh Infra",
            "land": "Kinikh Landcraft",
            "story": "People With Purpose",
            "ngo": "Empower Society",
            "accounts": "Common Accounts",
            "support": "Central Support",
            "ea": "Founder's Executive Assistant"
        }
        departments = [
            DepartmentContext(
                name=name,
                display_name=DISPLAY_NAMES.get(name, name.replace("_", " ").title()),
                email=DEPARTMENT_EMAIL_MAP.get(name),
                prompt=DEPARTMENT_PROMPTS.get(name),
                knowledge_base=KNOWLEDGE_BASES.get(name),
                keywords=list(ROUTING_RULES.get(name, [])),
            )
            for name in sorted(ROUTING_RULES.keys())
        ]

    return BusinessContext(company=company, departments=departments)


def render_business_context(context: BusinessContext) -> str:
    """Render a BusinessContext into the plain-text block inserted into the
    system prompt. Only includes fields that actually have a value — never
    fabricates a placeholder for missing data."""
    lines = [f"Company: {context.company.name}"]
    if context.company.website:
        lines.append(f"Website: {context.company.website}")
    if context.company.phone:
        lines.append(f"Phone: {context.company.phone}")

    if not context.departments:
        lines.append("\nNo department information is currently configured.")
    else:
        lines.append("\nDepartments:")
        for dept in context.departments:
            contact = f" (email: {dept.email})" if dept.email else ""
            lines.append(f"- {dept.display_name}{contact}")
            if dept.knowledge_base:
                knowledge = " ".join(dept.knowledge_base.split())  # collapse whitespace/newlines
                lines.append(f"  Knowledge: {knowledge}")
            if dept.prompt:
                lines.append(f"  Instructions: {dept.prompt}")
            if dept.keywords:
                lines.append(f"  Routing keywords: {', '.join(dept.keywords)}")

    # Company information (RAG)
    # When RAG is in "full" mode, we inject the entire knowledge base here.
    # In "retrieval" mode, this is skipped and chunks are provided per-turn.
    from app.core.config import get_settings
    from app.rag.index import get_knowledge_index
    settings = get_settings()
    index = get_knowledge_index()
    if index.mode == "full":
        kinikh_knowledge = index.full_context()
        if kinikh_knowledge:
            lines.append(f"\n--- Company information ---\n{kinikh_knowledge}")

    return "\n".join(lines)
