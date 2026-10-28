"""Email system tests — template engine, SMTP client, sender."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest


class TestTemplateEngine:
    def test_renders_email_template(self):
        from app.email_system.template_engine import TemplateEngine

        engine = TemplateEngine()
        lead = {
            "name": "Rajesh Kumar",
            "phone": "+919876543210",
            "email": "rajesh@example.com",
            "department": "rural",
            "requirement": "Enterprise plan pricing",
            "summary": "Caller wants enterprise pricing.",
            "language": "en",
            "additional_notes": None,
        }
        html = engine.render("email.html", {"lead": lead})
        assert "Rajesh Kumar" in html
        assert "+919876543210" in html
        assert "Enterprise plan pricing" in html

    def test_renders_report_template(self):
        from app.email_system.template_engine import TemplateEngine

        engine = TemplateEngine()
        report = {
            "date": "2026-06-29",
            "year": 2026,
            "total_calls": 10,
            "total_leads": 8,
            "emails_sent": 8,
            "leads": [
                {
                    "name": "Raj",
                    "phone": "+91000",
                    "department": "rural",
                    "language": "en",
                    "requirement": "Demo",
                }
            ],
        }
        html = engine.render("report.html", {"report": report})
        assert "2026-06-29" in html
        assert "Raj" in html


class TestSMTPClient:
    def test_smtp_client_imports(self):
        from app.email_system.smtp_client import SMTPClient

        assert SMTPClient is not None

    @pytest.mark.asyncio
    async def test_send_raises_on_smtp_failure(self):
        from app.core.exceptions import ExternalServiceError
        from app.email_system.smtp_client import SMTPClient

        client = SMTPClient()

        with patch.object(client, "_send_sync", side_effect=Exception("SMTP down")):
            with pytest.raises(ExternalServiceError):
                await client.send(
                    to=["test@test.com"],
                    subject="Test",
                    html_body="<p>Hello</p>",
                )

    def test_test_connection_uses_smtp_ssl_for_port_465(self):
        """Port 465 is implicit TLS (SMTPS) — connecting with plain SMTP +
        starttls() (the 587 sequence) hangs against a 465 listener instead
        of failing fast, which is exactly the bug found while building the
        connection-test feature."""
        from app.email_system.smtp_client import SMTPClient

        client = SMTPClient()
        client._port = 465

        with patch("app.email_system.smtp_client.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = MagicMock()
            mock_ssl.return_value.__enter__.return_value = mock_server
            with patch("app.email_system.smtp_client.smtplib.SMTP") as mock_plain:
                client._test_connection_sync()

        mock_ssl.assert_called_once()
        mock_plain.assert_not_called()
        mock_server.login.assert_called_once_with(client._username, client._password)
        mock_server.noop.assert_called_once()

    def test_test_connection_uses_starttls_for_port_587(self):
        from app.email_system.smtp_client import SMTPClient

        client = SMTPClient()
        client._port = 587
        client._use_tls = True

        with patch("app.email_system.smtp_client.smtplib.SMTP") as mock_plain:
            mock_server = MagicMock()
            mock_plain.return_value.__enter__.return_value = mock_server
            with patch("app.email_system.smtp_client.smtplib.SMTP_SSL") as mock_ssl:
                client._test_connection_sync()

        mock_ssl.assert_not_called()
        mock_plain.assert_called_once()
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with(client._username, client._password)

    @pytest.mark.asyncio
    async def test_test_connection_never_sends_a_message(self):
        """The connection test must not call sendmail under any circumstance."""
        from app.email_system.smtp_client import SMTPClient

        client = SMTPClient()

        with patch.object(client, "_test_connection_sync") as mock_sync:
            await client.test_connection()

        mock_sync.assert_called_once()


class TestEmailSender:
    def test_email_sender_imports(self):
        from app.email_system.email_sender import EmailSender

        assert EmailSender is not None

    @pytest.mark.asyncio
    async def test_send_lead_report_creates_log(self):
        from app.email_system.email_sender import EmailSender

        session = AsyncMock()
        sender = EmailSender(session)

        mock_log = MagicMock()
        mock_log.id = uuid.uuid4()

        with (
            patch.object(sender._log_repo, "create", AsyncMock(return_value=mock_log)),
            patch.object(sender._log_repo, "get", AsyncMock(return_value=mock_log)),
            patch.object(sender._log_repo, "update", AsyncMock(return_value=mock_log)),
            patch.object(sender._smtp, "send", AsyncMock()),
        ):
            await sender.send_lead_report(
                recipient_emails=["dept@kinikh.com"],
                lead_data={
                    "name": "Test",
                    "phone": "+91000",
                    "email": None,
                    "department": "rural",
                    "requirement": "Needs demo",
                    "summary": "Summary here",
                    "language": "en",
                    "additional_notes": None,
                },
                lead_id=uuid.uuid4(),
            )
            sender._log_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_failed_logs_counted(self):
        from app.email_system.email_sender import EmailSender

        session = AsyncMock()
        sender = EmailSender(session)

        with patch.object(sender._log_repo, "list_pending_retry", AsyncMock(return_value=[])):
            count = await sender.retry_failed()
        assert count == 0


class TestEmailService:
    def test_email_service_imports(self):
        from app.services.email_service import EmailService

        assert EmailService is not None
