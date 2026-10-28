"""ApiUsage data access."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_usage import ApiUsage
from app.repositories.base import BaseRepository


class ApiUsageRepository(BaseRepository[ApiUsage]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ApiUsage, session)

    async def list_by_call_sid(self, call_sid: str) -> Sequence[ApiUsage]:
        result = await self._session.execute(select(ApiUsage).where(ApiUsage.call_sid == call_sid))
        return result.scalars().all()

    async def totals_by_provider(self) -> list[tuple[str, int, int, int, float]]:
        """Returns (provider, request_count, total_tokens, total_characters, total_cost)."""
        result = await self._session.execute(
            select(
                ApiUsage.provider,
                func.count(),
                func.coalesce(func.sum(ApiUsage.total_tokens), 0),
                func.coalesce(func.sum(ApiUsage.characters), 0),
                func.coalesce(func.sum(ApiUsage.estimated_cost_usd), 0.0),
            ).group_by(ApiUsage.provider)
        )
        return list(result.all())

    async def total_requests_since(self, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(ApiUsage).where(ApiUsage.created_at >= since)
        )
        return result.scalar_one()
