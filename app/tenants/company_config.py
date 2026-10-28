"""Company-level configuration and branding."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class CompanyConfig:
    name: str
    website: str
    phone: str
    default_language: str = "en"
    supported_languages: tuple = ("en", "hi", "hi-en")
    business_hours_start: int = 9
    business_hours_end: int = 18
    timezone: str = "Asia/Kolkata"


def get_company_config() -> CompanyConfig:
    settings = get_settings()
    return CompanyConfig(
        name=settings.company_name,
        website=settings.company_website,
        phone=settings.company_phone,
    )
