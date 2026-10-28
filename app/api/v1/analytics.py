"""Aggregate analytics endpoints (protected, read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.services.call_service import CallService
from app.services.email_service import EmailService
from app.services.lead_service import LeadService
from app.services.usage_tracking_service import UsageTrackingService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class TimeseriesPoint(BaseModel):
    date: str
    count: int


class DepartmentCount(BaseModel):
    department_id: str | None
    department_name: str | None
    count: int


class AnalyticsOverviewResponse(BaseModel):
    calls_over_time: list[TimeseriesPoint]
    leads_over_time: list[TimeseriesPoint]
    calls_by_department: list[DepartmentCount]
    calls_by_language: dict[str, int]
    completed_vs_failed: dict[str, int]
    email_status_breakdown: dict[str, int]
    cost_over_time: list[dict[str, str | float]]


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def analytics_overview(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> AnalyticsOverviewResponse:
    call_service = CallService(db)
    lead_service = LeadService(db)
    email_service = EmailService(db)
    usage_service = UsageTrackingService(db)

    calls_over_time = await call_service.calls_over_time(days=days)
    leads_over_time = await lead_service.leads_over_time(days=days)
    dept_rows = await call_service.counts_by_department()
    by_language = await call_service.counts_by_language()
    completed_vs_failed = await call_service.completed_vs_failed()
    email_status = await email_service.counts_by_status()
    cost_over_time = await usage_service.cost_timeseries(days=days)

    return AnalyticsOverviewResponse(
        calls_over_time=[TimeseriesPoint(date=str(d), count=c) for d, c in calls_over_time],
        leads_over_time=[TimeseriesPoint(date=str(d), count=c) for d, c in leads_over_time],
        calls_by_department=[
            DepartmentCount(
                department_id=str(dept_id) if dept_id else None, department_name=name, count=c
            )
            for dept_id, name, c in dept_rows
        ],
        calls_by_language=by_language,
        completed_vs_failed=completed_vs_failed,
        email_status_breakdown=email_status,
        cost_over_time=cost_over_time,
    )
