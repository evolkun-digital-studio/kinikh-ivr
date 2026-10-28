"""Department management service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.repositories.department_repository import DepartmentRepository


class DepartmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = DepartmentRepository(session)

    async def get(self, department_id: uuid.UUID) -> Department:
        return await self._repo.get_or_raise(department_id)

    async def get_by_name(self, name: str) -> Department | None:
        return await self._repo.get_by_name(name)

    async def list_active(self) -> Sequence[Department]:
        return await self._repo.list_active()

    async def list_all(self) -> Sequence[Department]:
        return await self._repo.list_all()

    async def create(
        self,
        name: str,
        display_name: str,
        email: str,
        greeting: str | None = None,
        prompt: str | None = None,
        knowledge_base: str | None = None,
        keywords: list[str] | None = None,
    ) -> Department:
        return await self._repo.create(
            name=name,
            display_name=display_name,
            email=email,
            greeting=greeting,
            prompt=prompt,
            knowledge_base=knowledge_base,
            keywords=keywords,
        )

    async def update(self, department_id: uuid.UUID, **kwargs: Any) -> Department:
        dept = await self._repo.get_or_raise(department_id)
        return await self._repo.update(dept, **kwargs)

    async def deactivate(self, department_id: uuid.UUID) -> Department:
        dept = await self._repo.get_or_raise(department_id)
        return await self._repo.update(dept, is_active=False)

    async def activate(self, department_id: uuid.UUID) -> Department:
        dept = await self._repo.get_or_raise(department_id)
        return await self._repo.update(dept, is_active=True)
