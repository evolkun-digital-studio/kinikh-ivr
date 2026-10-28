"""Lead management endpoints (protected)."""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.models.email_log import EmailLog
from app.models.lead import Lead
from app.services.lead_service import LeadService

router = APIRouter(prefix="/leads", tags=["Leads"])


class DepartmentBrief(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str

    model_config = {"from_attributes": True}


class CallBrief(BaseModel):
    id: uuid.UUID
    call_sid: str
    status: str

    model_config = {"from_attributes": True}


class LeadResponse(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str
    email: str | None
    requirement: str | None
    summary: str | None
    language: str | None
    department_id: uuid.UUID | None
    department: DepartmentBrief | None = None
    call: CallBrief | None = None
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_lead(cls, lead: Lead) -> LeadResponse:
        return cls(
            id=lead.id,
            name=lead.name,
            phone=lead.phone,
            email=lead.email,
            requirement=lead.requirement,
            summary=lead.summary,
            language=lead.language,
            department_id=lead.department_id,
            department=DepartmentBrief.model_validate(lead.department) if lead.department else None,
            call=CallBrief.model_validate(lead.call) if lead.call else None,
            created_at=lead.created_at.isoformat(),
        )


class EmailLogBrief(BaseModel):
    id: uuid.UUID
    recipient_email: str
    status: str
    sent_at: str | None
    retry_count: int
    error_message: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_log(cls, log: EmailLog) -> EmailLogBrief:
        return cls(
            id=log.id,
            recipient_email=log.recipient_email,
            status=log.status,
            sent_at=log.sent_at.isoformat() if log.sent_at else None,
            retry_count=log.retry_count,
            error_message=log.error_message,
        )


class LeadDetailResponse(LeadResponse):
    additional_notes: str | None
    raw_transcript: str | None
    email_logs: list[EmailLogBrief] = []

    @classmethod
    def from_lead(cls, lead: Lead) -> LeadDetailResponse:
        base = LeadResponse.from_lead(lead)
        return cls(
            **base.model_dump(),
            additional_notes=lead.additional_notes,
            raw_transcript=lead.raw_transcript,
            email_logs=[EmailLogBrief.from_log(log) for log in (lead.email_logs or [])],
        )


class PaginatedLeadsResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=PaginatedLeadsResponse)
async def list_leads(
    department_id: uuid.UUID | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = Query("created_at", pattern="^(created_at|name)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> PaginatedLeadsResponse:
    service = LeadService(db)
    leads, total = await service.list_filtered(
        department_id=department_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return PaginatedLeadsResponse(
        items=[LeadResponse.from_lead(row) for row in leads],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{lead_id}", response_model=LeadDetailResponse)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> LeadDetailResponse:
    service = LeadService(db)
    lead = await service.get_with_relations(lead_id)
    return LeadDetailResponse.from_lead(lead)
