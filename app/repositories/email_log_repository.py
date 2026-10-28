"""EmailLog-specific data access operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.email_log import EmailLog
from app.models.lead import Lead
from app.repositories.base import BaseRepository


class EmailLogRepository(BaseRepository[EmailLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(EmailLog, session)

    async def list_by_lead(self, lead_id: uuid.UUID) -> Sequence[EmailLog]:
        result = await self._session.execute(
            select(EmailLog).where(EmailLog.lead_id == lead_id).order_by(EmailLog.created_at.desc())
        )
        return result.scalars().all()

    async def list_pending_retry(self, max_retries: int = 3) -> Sequence[EmailLog]:
        result = await self._session.execute(
            select(EmailLog)
            .where(EmailLog.status == "failed", EmailLog.retry_count < max_retries)
            .order_by(EmailLog.created_at)
        )
        return result.scalars().all()

    def _apply_filters(
        self,
        stmt: Select,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        _needs_lead_join: bool = False,
    ) -> Select:
        if status:
            stmt = stmt.where(EmailLog.status == status)
        if search:
            stmt = stmt.where(EmailLog.recipient_email.ilike(f"%{search}%"))
        if date_from:
            stmt = stmt.where(EmailLog.created_at >= date_from)
        if date_to:
            stmt = stmt.where(EmailLog.created_at < date_to)
        if department_id or _needs_lead_join:
            stmt = stmt.join(Lead, Lead.id == EmailLog.lead_id, isouter=True)
            if department_id:
                stmt = stmt.where(Lead.department_id == department_id)
        return stmt

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[EmailLog]:
        stmt = self._apply_filters(
            select(EmailLog).options(selectinload(EmailLog.lead).selectinload(Lead.department)),
            status=status,
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = stmt.order_by(EmailLog.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().unique().all()

    async def count_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        stmt = self._apply_filters(
            select(func.count(func.distinct(EmailLog.id))),
            status=status,
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def counts_by_status(self) -> dict[str, int]:
        result = await self._session.execute(
            select(EmailLog.status, func.count()).group_by(EmailLog.status)
        )
        return dict(result.all())
