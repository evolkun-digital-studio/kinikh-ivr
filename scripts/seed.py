#!/usr/bin/env python3
"""Seed the database with default departments.

Usage:
    python scripts/seed.py

Set DATABASE_URL in environment before running.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://kinikh:kinikh@localhost:5432/kinikh_ivr",
)
os.environ.setdefault("SECRET_KEY", "seed-script-placeholder-not-for-prod")
os.environ.setdefault("OPENAI_API_KEY", "placeholder")
os.environ.setdefault("ELEVENLABS_API_KEY", "placeholder")
os.environ.setdefault("ELEVENLABS_VOICE_ID_EN", "placeholder")
os.environ.setdefault("ELEVENLABS_VOICE_ID_HI", "placeholder")
os.environ.setdefault("EXOTEL_SID", "placeholder")
os.environ.setdefault("EXOTEL_TOKEN", "placeholder")
os.environ.setdefault("EXOTEL_API_KEY", "placeholder")
os.environ.setdefault("EXOTEL_API_SECRET", "placeholder")
os.environ.setdefault("EXOTEL_CALLER_ID", "+910000000000")
os.environ.setdefault("EXOTEL_WEBHOOK_SECRET", "placeholder")
os.environ.setdefault("SMTP_USERNAME", "placeholder@placeholder.com")
os.environ.setdefault("SMTP_PASSWORD", "placeholder")
os.environ.setdefault("SMTP_FROM_EMAIL", "placeholder@placeholder.com")
os.environ.setdefault("JWT_SECRET_KEY", "seed-script-jwt-placeholder-not-for-prod")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.department import Department
from app.tenants.knowledge_base import KNOWLEDGE_BASES
from app.tenants.prompts import DEPARTMENT_PROMPTS
from app.tenants.routing_rules import ROUTING_RULES

SEED_DEPARTMENTS = [
    {
        "name": "rural",
        "display_name": "KINIKH Rural",
        "email": "livelihood@kinikh.com",
    },
    {
        "name": "stay",
        "display_name": "Slow Tuesday",
        "email": "stay@kinikh.com",
    },
    {
        "name": "tech",
        "display_name": "evolKun",
        "email": "evolkun@kinikh.com",
    },
    {
        "name": "brand",
        "display_name": "BrandWorks",
        "email": "brandworks@kinikh.com",
    },
    {
        "name": "infra",
        "display_name": "Kinikh Infra",
        "email": "infra@kinikh.com",
    },
    {
        "name": "land",
        "display_name": "Kinikh Landcraft",
        "email": "landcraft@kinikh.com",
    },
    {
        "name": "story",
        "display_name": "People With Purpose",
        "email": "legacy@kinikh.com",
    },
    {
        "name": "ngo",
        "display_name": "Empower Society",
        "email": "info@empowersociety.org",
    },
    {
        "name": "support",
        "display_name": "Central Support",
        "email": "connect@kinikh.com",
    },
    {
        "name": "accounts",
        "display_name": "Common Accounts",
        "email": "connect@kinikh.com",
    },
    {
        "name": "ea",
        "display_name": "Founder's Executive Assistant",
        "email": "connect@kinikh.com",
    },
]


async def seed(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    created = 0
    skipped = 0

    async with factory() as session:
        for dept_data in SEED_DEPARTMENTS:
            name = dept_data["name"]

            from sqlalchemy import select

            existing = await session.execute(select(Department).where(Department.name == name))
            existing_dept = existing.scalar_one_or_none()
            if existing_dept:
                existing_dept.display_name = dept_data["display_name"]
                existing_dept.email = dept_data["email"]
                if "greeting" in dept_data:
                    existing_dept.greeting = dept_data["greeting"]
                print(f"  update  {name}")
                created += 1
                continue

            dept = Department(
                name=name,
                display_name=dept_data["display_name"],
                email=dept_data["email"],
                greeting=dept_data.get("greeting"),
                prompt=DEPARTMENT_PROMPTS.get(name),
                knowledge_base=KNOWLEDGE_BASES.get(name),
                keywords=ROUTING_RULES.get(name, []),
                is_active=True,
            )
            session.add(dept)
            print(f"  create {name}")
            created += 1

        await session.commit()

    await engine.dispose()
    print(f"\nDone — {created} created, {skipped} skipped.")


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Seeding departments into: {db_url.split('@')[-1]}")
    asyncio.run(seed(db_url))


if __name__ == "__main__":
    main()
