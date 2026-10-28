"""Email dispatch service — triggered after lead is created."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.email_system.email_sender import EmailSender
from app.models.email_log import EmailLog
from app.repositories.department_repository import DepartmentRepository
from app.repositories.email_log_repository import EmailLogRepository
from app.services.lead_service import LeadService


class EmailService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._sender = EmailSender(db)
        self._dept_repo = DepartmentRepository(db)
        self._lead_service = LeadService(db)
        self._log_repo = EmailLogRepository(db)

    async def dispatch_lead_email(self, lead_id: uuid.UUID) -> None:
        """Look up the lead, find its department email, and send the report."""
        lead = await self._lead_service.get(lead_id)

        recipient_emails = []
        if lead.department_id:
            dept = await self._dept_repo.get(lead.department_id)
            if dept and dept.email:
                recipient_emails.append(dept.email)

        if not recipient_emails:
            from app.core.config import get_settings

            # Fall back to company-level contact if no department email.
            settings = get_settings()
            if settings.smtp_from_email:
                recipient_emails.append(str(settings.smtp_from_email))

        lead_data: dict[str, Any] = {
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "department": lead.department.display_name if lead.department else "General",
            "requirement": lead.requirement,
            "summary": lead.summary,
            "language": lead.language,
            "additional_notes": lead.additional_notes,
        }
        
        # Load the call relation to get the reference
        # The LeadRepository get() might not eagerly load the call. Let's just retrieve it here if not loaded.
        from sqlalchemy.orm import selectinload
        from sqlalchemy import select
        from app.models.lead import Lead
        
        lead_with_call = await self._db.execute(
            select(Lead).where(Lead.id == lead.id).options(selectinload(Lead.call))
        )
        lwc = lead_with_call.scalar_one_or_none()
        if lwc and lwc.call:
            lead_data["reference"] = lwc.call.reference

        try:
            await self._sender.send_lead_report(
                recipient_emails=recipient_emails,
                lead_data=lead_data,
                lead_id=lead.id,
            )
        except Exception as exc:
            logger.error(f"Lead email dispatch failed for {lead_id}: {exc}")

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[EmailLog], int]:
        items = await self._log_repo.list_filtered(
            status=status,
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        total = await self._log_repo.count_filtered(
            status=status,
            department_id=department_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        return items, total

    async def counts_by_status(self) -> dict[str, int]:
        return await self._log_repo.counts_by_status()

    async def list_by_lead(self, lead_id: uuid.UUID) -> Sequence[EmailLog]:
        return await self._log_repo.list_by_lead(lead_id)
