"""High-level email sender: renders template, sends, logs, retries on failure."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.email_system.smtp_client import SMTPClient
from app.email_system.template_engine import TemplateEngine
from app.repositories.email_log_repository import EmailLogRepository

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0


class EmailSender:
    """Sends templated emails and persists delivery status to EmailLog."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._smtp = SMTPClient()
        self._renderer = TemplateEngine()
        self._log_repo = EmailLogRepository(db)

    async def send_lead_report(
        self,
        recipient_emails: list[str],
        lead_data: dict[str, Any],
        lead_id: uuid.UUID | None = None,
    ) -> None:
        reference = lead_data.get('reference', 'NO-REF')
        product_service = lead_data.get('department', 'General')
        subject = f"KINIKH Lead | {reference} | {product_service}"
        html_body = self._renderer.render("email.html", {"lead": lead_data})

        for email in recipient_emails:
            log = await self._log_repo.create(
                lead_id=lead_id,
                recipient_email=email,
                subject=subject,
                template_name="email.html",
                status="pending",
            )
            await self._send_with_retry(log.id, [email], subject, html_body)

    async def _send_with_retry(
        self,
        log_id: uuid.UUID,
        to: list[str],
        subject: str,
        html_body: str,
    ) -> None:
        log = await self._log_repo.get(log_id)
        if log is None:
            return

        for attempt in range(_MAX_RETRIES):
            try:
                await self._smtp.send(to=to, subject=subject, html_body=html_body)
                await self._log_repo.update(
                    log,
                    status="sent",
                    sent_at=datetime.now(UTC),
                    retry_count=attempt,
                )
                return
            except Exception as exc:
                logger.warning(f"Email attempt {attempt + 1} failed for {to}: {exc}")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))

        await self._log_repo.update(
            log,
            status="failed",
            error_message=f"All {_MAX_RETRIES} attempts failed",
            retry_count=_MAX_RETRIES,
        )

    async def retry_failed(self) -> int:
        """Re-send emails that previously failed. Returns number retried."""
        failed = await self._log_repo.list_pending_retry()
        count = 0
        for log in failed:
            html_body = ""
            try:
                await self._smtp.send(
                    to=[log.recipient_email],
                    subject=log.subject,
                    html_body=html_body,
                )
                await self._log_repo.update(log, status="sent", sent_at=datetime.now(UTC))
                count += 1
            except Exception as exc:
                await self._log_repo.update(
                    log, retry_count=log.retry_count + 1, error_message=str(exc)
                )
        return count
