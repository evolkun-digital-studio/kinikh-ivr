"""Department-specific data access operations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.repositories.base import BaseRepository


class DepartmentRepository(BaseRepository[Department]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Department, session)

    async def get_by_name(self, name: str) -> Department | None:
        result = await self._session.execute(select(Department).where(Department.name == name))
        return result.scalar_one_or_none()

    async def list_active(self) -> Sequence[Department]:
        result = await self._session.execute(
            select(Department).where(Department.is_active.is_(True)).order_by(Department.name)
        )
        return result.scalars().all()

    async def list_all(self) -> Sequence[Department]:
        result = await self._session.execute(select(Department).order_by(Department.name))
        return result.scalars().all()
