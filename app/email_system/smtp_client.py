"""Async SMTP client using aiosmtplib-compatible pattern via asyncio executor."""

from __future__ import annotations

import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import ssl

import certifi
from loguru import logger

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError


def _ssl_context() -> ssl.SSLContext:
    """Default TLS context, pinned to certifi's CA bundle.

    `ssl.create_default_context()` with no `cafile` falls back to the OS's
    trust store — on a Python build that isn't linked to the system
    keychain (common for a plain venv on macOS), that store can be empty,
    making every TLS connection fail with `CERTIFICATE_VERIFY_FAILED:
    unable to get local issuer certificate` regardless of how valid the
    server's certificate or the configured credentials actually are.
    certifi ships an up-to-date, always-present CA bundle, so pinning to it
    removes that environment dependency entirely.
    """
    return ssl.create_default_context(cafile=certifi.where())


class SMTPClient:
    """Thread-pool-based SMTP client (smtplib wrapped in executor for async compatibility)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password
        self._from_email = str(settings.smtp_from_email)
        self._from_name = settings.smtp_from_name
        self._use_tls = settings.smtp_use_tls

    def _send_sync(
        self, to: list[str], subject: str, html_body: str, text_body: str | None
    ) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self._from_name} <{self._from_email}>"
        msg["To"] = ", ".join(to)

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        context = _ssl_context()
        # Same connection rules as _test_connection_sync: port 465 is
        # implicit TLS (SMTP_SSL). Sending used to always open a plaintext
        # SMTP connection + STARTTLS, which a 465 server drops immediately —
        # the "Connection unexpectedly closed" failures in the logs.
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30, context=context) as server:
                server.ehlo()
                server.login(self._username, self._password)
                server.sendmail(self._from_email, to, msg.as_string())
            return
        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            server.ehlo()
            if self._use_tls:
                server.starttls(context=context)
                server.ehlo()
            server.login(self._username, self._password)
            server.sendmail(self._from_email, to, msg.as_string())

    async def send(
        self,
        to: list[str],
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._send_sync, to, subject, html_body, text_body)
            logger.info(f"Email sent to {to} subject='{subject}'")
        except Exception as exc:
            logger.error(f"SMTP send failed: {exc}")
            raise ExternalServiceError("SMTP", str(exc)) from exc

    def _test_connection_sync(self) -> None:
        """Connect, negotiate TLS, and authenticate — never sends a message.

        Port 465 is implicit TLS (SMTPS): the server expects a TLS handshake
        immediately, so it must use `SMTP_SSL`, not a plaintext `SMTP`
        connection followed by `starttls()` (that upgrade sequence is for
        587/25 and simply hangs until timeout against a 465 listener).
        """
        context = _ssl_context()
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=10, context=context) as server:
                server.ehlo()
                server.login(self._username, self._password)
                server.noop()
        else:
            with smtplib.SMTP(self._host, self._port, timeout=10) as server:
                server.ehlo()
                if self._use_tls:
                    server.starttls(context=context)
                    server.ehlo()
                server.login(self._username, self._password)
                server.noop()

    async def test_connection(self) -> None:
        """Connection-test entry point for the integration test service.
        Raises on any failure; never sends an email."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._test_connection_sync)
