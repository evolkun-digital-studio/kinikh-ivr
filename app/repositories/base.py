"""Generic async repository with common CRUD operations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Provides standard CRUD for any SQLAlchemy model."""

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get(self, id: uuid.UUID) -> ModelT | None:
        return await self._session.get(self._model, id)

    async def get_or_raise(self, id: uuid.UUID) -> ModelT:
        from app.core.exceptions import NotFoundError

        obj = await self.get(id)
        if obj is None:
            raise NotFoundError(self._model.__name__, id)
        return obj

    async def list(self, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        result = await self._session.execute(select(self._model).limit(limit).offset(offset))
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> ModelT:
        obj = self._model(**kwargs)
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **kwargs: Any) -> ModelT:
        for key, value in kwargs.items():
            setattr(obj, key, value)
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self._session.delete(obj)
        await self._session.flush()

    async def count(self) -> int:
        from sqlalchemy import func

        result = await self._session.execute(select(func.count()).select_from(self._model))
        return result.scalar_one()
