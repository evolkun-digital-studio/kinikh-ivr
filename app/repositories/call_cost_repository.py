"""CallCost data access."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_cost import CallCost
from app.repositories.base import BaseRepository


class CallCostRepository(BaseRepository[CallCost]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(CallCost, session)

    async def get_by_call_sid(self, call_sid: str) -> CallCost | None:
        result = await self._session.execute(select(CallCost).where(CallCost.call_sid == call_sid))
        return result.scalar_one_or_none()

    async def totals(self) -> dict[str, float]:
        result = await self._session.execute(
            select(
                func.coalesce(func.sum(CallCost.telephony_cost_usd), 0.0),
                func.coalesce(func.sum(CallCost.openai_cost_usd), 0.0),
                func.coalesce(func.sum(CallCost.voice_cost_usd), 0.0),
                func.coalesce(func.sum(CallCost.email_cost_usd), 0.0),
                func.coalesce(func.sum(CallCost.total_cost_usd), 0.0),
                func.count(),
            )
        )
        row = result.one()
        return {
            "telephony_usd": row[0],
            "openai_usd": row[1],
            "voice_usd": row[2],
            "email_usd": row[3],
            "total_usd": row[4],
            "call_count": row[5],
        }

    async def total_since(self, since: datetime) -> float:
        result = await self._session.execute(
            select(func.coalesce(func.sum(CallCost.total_cost_usd), 0.0)).where(
                CallCost.created_at >= since
            )
        )
        return result.scalar_one()

    async def cost_over_time(self, since: datetime) -> list[tuple[date, float]]:
        day = func.date(CallCost.created_at)
        result = await self._session.execute(
            select(day.label("day"), func.coalesce(func.sum(CallCost.total_cost_usd), 0.0))
            .where(CallCost.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return list(result.all())
