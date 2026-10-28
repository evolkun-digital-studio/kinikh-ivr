"""API usage and cost tracking endpoints (protected, read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.services.usage_tracking_service import UsageTrackingService

router = APIRouter(prefix="/usage", tags=["Usage & Cost"])


class CostTotals(BaseModel):
    telephony_usd: float
    openai_usd: float
    voice_usd: float
    email_usd: float
    total_usd: float
    call_count: int
    cost_per_call_usd: float


class ProviderUsage(BaseModel):
    provider: str
    requests: int
    total_tokens: int
    total_characters: int
    estimated_cost_usd: float


class UsageSummaryResponse(BaseModel):
    totals: CostTotals
    by_provider: list[ProviderUsage]


class CostPoint(BaseModel):
    date: str
    cost_usd: float


@router.get("/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> UsageSummaryResponse:
    service = UsageTrackingService(db)
    data = await service.cost_summary()
    return UsageSummaryResponse(**data)


@router.get("/timeseries", response_model=list[CostPoint])
async def usage_timeseries(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> list[CostPoint]:
    service = UsageTrackingService(db)
    points = await service.cost_timeseries(days=days)
    return [CostPoint(**p) for p in points]
