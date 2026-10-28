"""Lead-specific data access operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.lead import Lead
from app.repositories.base import BaseRepository


class LeadRepository(BaseRepository[Lead]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Lead, session)

    async def get_by_phone(self, phone: str) -> Lead | None:
        result = await self._session.execute(
            select(Lead).where(Lead.phone == phone).order_by(Lead.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_call_id(self, call_id: uuid.UUID) -> Lead | None:
        result = await self._session.execute(select(Lead).where(Lead.call_id == call_id))
        return result.scalar_one_or_none()

    async def list_by_department(
        self, department_id: uuid.UUID, limit: int = 100
    ) -> Sequence[Lead]:
        result = await self._session.execute(
            select(Lead)
            .where(Lead.department_id == department_id)
            .order_by(Lead.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    def _apply_filters(
        self,
        stmt: Select,
        *,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Select:
        if department_id:
            stmt = stmt.where(Lead.department_id == department_id)
        if date_from:
            stmt = stmt.where(Lead.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Lead.created_at < date_to)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                (Lead.name.ilike(like)) | (Lead.phone.ilike(like)) | (Lead.email.ilike(like))
            )
        return stmt

    _SORTABLE_COLUMNS = {
        "created_at": Lead.created_at,
        "name": Lead.name,
    }

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
    ) -> Sequence[Lead]:
        stmt = self._apply_filters(
            select(Lead).options(selectinload(Lead.department), selectinload(Lead.call)),
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        column = self._SORTABLE_COLUMNS.get(sort, Lead.created_at)
        stmt = stmt.order_by(column.asc() if order == "asc" else column.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_filtered(
        self,
        *,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        stmt = self._apply_filters(
            select(func.count()).select_from(Lead),
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_with_relations(self, lead_id: uuid.UUID) -> Lead | None:
        result = await self._session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .options(
                selectinload(Lead.department),
                selectinload(Lead.call),
                selectinload(Lead.email_logs),
            )
        )
        return result.scalar_one_or_none()

    async def count_created_since(self, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Lead).where(Lead.created_at >= since)
        )
        return result.scalar_one()

    async def leads_over_time(self, since: datetime) -> list[tuple[date, int]]:
        day = func.date(Lead.created_at)
        result = await self._session.execute(
            select(day.label("day"), func.count())
            .where(Lead.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return list(result.all())

    async def counts_by_department(self) -> dict[uuid.UUID, int]:
        result = await self._session.execute(
            select(Lead.department_id, func.count())
            .where(Lead.department_id.is_not(None))
            .group_by(Lead.department_id)
        )
        return dict(result.all())
