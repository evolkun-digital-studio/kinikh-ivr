"""Dashboard overview endpoint — KPIs + recent activity (protected, read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.calls import CallResponse
from app.api.v1.leads import LeadResponse
from app.core.dependencies import get_current_user_id, get_db
from app.services.call_service import CallService
from app.services.lead_service import LeadService
from app.services.usage_tracking_service import UsageTrackingService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class DashboardKPIs(BaseModel):
    total_calls: int
    completed_calls: int
    failed_calls: int
    total_leads: int
    today_calls: int
    today_leads: int
    total_cost_usd: float
    average_call_duration_seconds: float | None


class DashboardSummaryResponse(BaseModel):
    kpis: DashboardKPIs
    recent_calls: list[CallResponse]
    recent_leads: list[LeadResponse]


@router.get("/summary", response_model=DashboardSummaryResponse)
async def dashboard_summary(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DashboardSummaryResponse:
    call_service = CallService(db)
    lead_service = LeadService(db)
    usage_service = UsageTrackingService(db)

    call_counts = await call_service.dashboard_counts()
    lead_counts = await lead_service.dashboard_counts()
    cost_summary = await usage_service.cost_summary()

    recent_calls, _ = await call_service.list_filtered(limit=5, offset=0)
    recent_leads, _ = await lead_service.list_filtered(limit=5, offset=0)

    kpis = DashboardKPIs(
        total_calls=call_counts["total_calls"],
        completed_calls=call_counts["completed_calls"],
        failed_calls=call_counts["failed_calls"],
        total_leads=lead_counts["total_leads"],
        today_calls=call_counts["today_calls"],
        today_leads=lead_counts["today_leads"],
        total_cost_usd=cost_summary["totals"]["total_usd"],
        average_call_duration_seconds=call_counts["average_duration_seconds"],
    )

    return DashboardSummaryResponse(
        kpis=kpis,
        recent_calls=[CallResponse.from_call(c) for c in recent_calls],
        recent_leads=[LeadResponse.from_lead(lead) for lead in recent_leads],
    )
