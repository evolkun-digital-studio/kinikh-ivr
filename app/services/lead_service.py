"""Lead capture and retrieval service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.repositories.lead_repository import LeadRepository


class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = LeadRepository(session)

    async def create_from_call(
        self,
        phone: str,
        call_id: uuid.UUID | None = None,
        name: str | None = None,
        email: str | None = None,
        department_id: uuid.UUID | None = None,
        requirement: str | None = None,
        summary: str | None = None,
        language: str | None = None,
        additional_notes: str | None = None,
        raw_transcript: str | None = None,
    ) -> Lead:
        return await self._repo.create(
            phone=phone,
            call_id=call_id,
            name=name,
            email=email,
            department_id=department_id,
            requirement=requirement,
            summary=summary,
            language=language,
            additional_notes=additional_notes,
            raw_transcript=raw_transcript,
        )

    async def get(self, lead_id: uuid.UUID) -> Lead:
        return await self._repo.get_or_raise(lead_id)

    async def get_with_relations(self, lead_id: uuid.UUID) -> Lead:
        from app.core.exceptions import NotFoundError

        lead = await self._repo.get_with_relations(lead_id)
        if lead is None:
            raise NotFoundError("Lead", lead_id)
        return lead

    async def get_by_phone(self, phone: str) -> Lead | None:
        return await self._repo.get_by_phone(phone)

    async def get_by_call_id(self, call_id: uuid.UUID) -> Lead | None:
        return await self._repo.get_by_call_id(call_id)

    async def list(self, limit: int = 100, offset: int = 0) -> Sequence[Lead]:
        return await self._repo.list(limit=limit, offset=offset)

    async def list_by_department(self, department_id: uuid.UUID) -> Sequence[Lead]:
        return await self._repo.list_by_department(department_id)

    async def list_filtered(
        self,
        *,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Lead], int]:
        items = await self._repo.list_filtered(
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_filtered(
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        return items, total

    async def update(self, lead_id: uuid.UUID, **kwargs: Any) -> Lead:
        lead = await self._repo.get_or_raise(lead_id)
        return await self._repo.update(lead, **kwargs)

    async def dashboard_counts(self) -> dict[str, int]:
        since_today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await self._repo.count()
        today = await self._repo.count_created_since(since_today)
        return {"total_leads": total, "today_leads": today}

    async def leads_over_time(self, days: int = 30) -> list[tuple[date, int]]:
        since = datetime.now(UTC) - timedelta(days=days)
        return await self._repo.leads_over_time(since)

    async def counts_by_department(self) -> dict[uuid.UUID, int]:
        return await self._repo.counts_by_department()
