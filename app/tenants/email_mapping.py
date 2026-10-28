"""Static fallback department-to-email mapping (overridden by DB config)."""

from __future__ import annotations

DEPARTMENT_EMAIL_MAP: dict[str, str] = {
    "rural": "rural@kinikh.com",
    "stay": "stay@kinikh.com",
    "tech": "tech@kinikh.com",
    "brand": "brand@kinikh.com",
    "infra": "infra@kinikh.com",
    "land": "land@kinikh.com",
    "story": "story@kinikh.com",
    "ngo": "ngo@kinikh.com",
    "accounts": "accounts@kinikh.com",
    "support": "support@kinikh.com",
    "ea": "ea@kinikh.com",
}


def get_department_email(department_name: str) -> str | None:
    return DEPARTMENT_EMAIL_MAP.get(department_name.lower())
