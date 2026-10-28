"""Department-specific prompt overrides loaded at runtime."""

from __future__ import annotations

DEPARTMENT_PROMPTS: dict[str, str] = {
    "rural": "Focus on rural enterprise, farm queries, organic produce, and rural enterprise.",
    "stay": "Focus on homestay, resort, and accommodation bookings.",
    "tech": "Focus on tech, software, IT, ERP, and digital tools.",
    "brand": "Focus on branding, marketing, advertising, and media.",
    "infra": "Focus on civil construction, building, and infrastructure.",
    "land": "Focus on real estate, land plotting, and estates.",
    "story": "Focus on documentary production and impactful storytelling.",
    "ngo": "Focus on social impact, NGOs, volunteering, and welfare.",
    "accounts": "Focus on billing, invoices, salary queries, and payments.",
    "support": "You are a patient support agent for Kinikh. Route general, unresolved, HR, or complaints here.",
    "ea": "Focus on scheduling meetings with the founder/management.",
}


def get_department_prompt(department: str) -> str | None:
    return DEPARTMENT_PROMPTS.get(department.lower())
