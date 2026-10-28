"""CallRecording data access."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_recording import CallRecording
from app.repositories.base import BaseRepository


class CallRecordingRepository(BaseRepository[CallRecording]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(CallRecording, session)

    async def get_by_call_sid(self, call_sid: str) -> CallRecording | None:
        result = await self._session.execute(
            select(CallRecording).where(CallRecording.call_sid == call_sid)
        )
        return result.scalar_one_or_none()

    def _apply_filters(
        self,
        stmt: Select,
        *,
        status: str | None = None,
        department: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> Select:
        if status:
            stmt = stmt.where(CallRecording.status == status)
        if department:
            stmt = stmt.where(CallRecording.department == department)
        if search:
            stmt = stmt.where(CallRecording.caller_number.ilike(f"%{search}%"))
        if date_from:
            stmt = stmt.where(CallRecording.created_at >= date_from)
        if date_to:
            stmt = stmt.where(CallRecording.created_at < date_to)
        return stmt

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[CallRecording]:
        stmt = self._apply_filters(
            select(CallRecording),
            status=status,
            department=department,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = stmt.order_by(CallRecording.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_filtered(
        self,
        *,
        status: str | None = None,
        department: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        stmt = self._apply_filters(
            select(func.count()).select_from(CallRecording),
            status=status,
            department=department,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
