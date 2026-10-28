"""Call-specific data access operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.call import Call
from app.models.lead import Lead
from app.repositories.base import BaseRepository


class CallRepository(BaseRepository[Call]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Call, session)

    async def get_by_sid(self, call_sid: str) -> Call | None:
        result = await self._session.execute(select(Call).where(Call.call_sid == call_sid))
        return result.scalar_one_or_none()

    async def get_by_sid_for_update(self, call_sid: str) -> Call | None:
        """Serialize competing status-webhook and stream-finalizer writes."""
        result = await self._session.execute(
            select(Call).where(Call.call_sid == call_sid).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_lead(self, call_id: uuid.UUID) -> Call | None:
        result = await self._session.execute(
            select(Call)
            .where(Call.id == call_id)
            .options(selectinload(Call.lead).selectinload(Lead.department))
        )
        return result.scalar_one_or_none()

    async def list_by_caller(self, caller_number: str, limit: int = 50) -> Sequence[Call]:
        result = await self._session.execute(
            select(Call)
            .where(Call.caller_number == caller_number)
            .order_by(Call.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_by_status(self, status: str, limit: int = 100) -> Sequence[Call]:
        result = await self._session.execute(
            select(Call).where(Call.status == status).order_by(Call.created_at.desc()).limit(limit)
        )
        return result.scalars().all()

    def _apply_filters(
        self,
        stmt: Select,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        language: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        _needs_lead_join: bool = False,
    ) -> Select:
        if status:
            stmt = stmt.where(Call.status == status)
        if date_from:
            stmt = stmt.where(Call.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Call.created_at < date_to)
        if search:
            stmt = stmt.where(Call.caller_number.ilike(f"%{search}%"))
        if department_id or language or _needs_lead_join:
            stmt = stmt.join(Lead, Lead.call_id == Call.id, isouter=True)
            if department_id:
                stmt = stmt.where(Lead.department_id == department_id)
            if language:
                stmt = stmt.where(Lead.language == language)
        return stmt

    _SORTABLE_COLUMNS = {
        "created_at": Call.created_at,
        "duration_seconds": Call.duration_seconds,
        "status": Call.status,
        "caller_number": Call.caller_number,
    }

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        language: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Call]:
        stmt = self._apply_filters(
            select(Call).options(selectinload(Call.lead).selectinload(Lead.department)),
            status=status,
            department_id=department_id,
            language=language,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        column = self._SORTABLE_COLUMNS.get(sort, Call.created_at)
        stmt = stmt.order_by(column.asc() if order == "asc" else column.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().unique().all()

    async def count_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        language: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        stmt = self._apply_filters(
            select(func.count(func.distinct(Call.id))),
            status=status,
            department_id=department_id,
            language=language,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_by_status(self, status: str) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Call).where(Call.status == status)
        )
        return result.scalar_one()

    async def get_daily_sequence(self, day: date) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Call)
            .where(func.date(Call.created_at) == day)
        )
        return result.scalar_one()

    async def count_created_since(self, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Call).where(Call.created_at >= since)
        )
        return result.scalar_one()

    async def average_duration_seconds(self) -> float | None:
        result = await self._session.execute(
            select(func.avg(Call.duration_seconds)).where(Call.duration_seconds.is_not(None))
        )
        return result.scalar_one()

    async def calls_over_time(self, since: datetime) -> list[tuple[date, int]]:
        day = func.date(Call.created_at)
        result = await self._session.execute(
            select(day.label("day"), func.count())
            .where(Call.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return list(result.all())

    async def counts_by_department(self) -> list[tuple[uuid.UUID | None, str | None, int]]:
        from app.models.department import Department

        result = await self._session.execute(
            select(Department.id, Department.display_name, func.count(func.distinct(Call.id)))
            .select_from(Call)
            .join(Lead, Lead.call_id == Call.id)
            .join(Department, Department.id == Lead.department_id)
            .group_by(Department.id, Department.display_name)
        )
        return list(result.all())

    async def counts_by_language(self) -> dict[str, int]:
        result = await self._session.execute(
            select(Lead.language, func.count(func.distinct(Call.id)))
            .select_from(Call)
            .join(Lead, Lead.call_id == Call.id)
            .where(Lead.language.is_not(None))
            .group_by(Lead.language)
        )
        return dict(result.all())

    async def completed_vs_failed(self) -> dict[str, int]:
        result = await self._session.execute(
            select(Call.status, func.count())
            .where(Call.status.in_(["completed", "failed"]))
            .group_by(Call.status)
        )
        return dict(result.all())
