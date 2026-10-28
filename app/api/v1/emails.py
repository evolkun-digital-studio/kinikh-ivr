"""Email delivery log endpoints (protected, read-only)."""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.models.email_log import EmailLog
from app.services.email_service import EmailService

router = APIRouter(prefix="/emails", tags=["Emails"])


class LeadBrief(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str
    department_name: str | None = None

    model_config = {"from_attributes": True}


class EmailLogResponse(BaseModel):
    id: uuid.UUID
    recipient_email: str
    subject: str
    status: str
    sent_at: str | None
    retry_count: int
    error_message: str | None
    created_at: str
    lead: LeadBrief | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_log(cls, log: EmailLog) -> EmailLogResponse:
        lead_brief = None
        if log.lead:
            lead_brief = LeadBrief(
                id=log.lead.id,
                name=log.lead.name,
                phone=log.lead.phone,
                department_name=log.lead.department.display_name if log.lead.department else None,
            )
        return cls(
            id=log.id,
            recipient_email=log.recipient_email,
            subject=log.subject,
            status=log.status,
            sent_at=log.sent_at.isoformat() if log.sent_at else None,
            retry_count=log.retry_count,
            error_message=log.error_message,
            created_at=log.created_at.isoformat(),
            lead=lead_brief,
        )


class PaginatedEmailsResponse(BaseModel):
    items: list[EmailLogResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=PaginatedEmailsResponse)
async def list_emails(
    status: str | None = None,
    department_id: uuid.UUID | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> PaginatedEmailsResponse:
    service = EmailService(db)
    items, total = await service.list_filtered(
        status=status,
        department_id=department_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PaginatedEmailsResponse(
        items=[EmailLogResponse.from_log(log) for log in items],
        total=total,
        limit=limit,
        offset=offset,
    )
