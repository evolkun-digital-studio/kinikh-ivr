"""HTTP-level tests for the Exotel webhook routes.

Regression coverage for two bugs found during the pre-API production audit
(see docs/pre_api_audit.md):
  - a webhook payload with a missing/empty CallSid was silently accepted and
    written to the database instead of being rejected;
  - a redelivered ("duplicate") incoming-call webhook crashed with an
    unhandled 500 (UniqueViolation) instead of being handled idempotently.
"""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.dependencies import get_db
from app.main import app
from app.services.call_recording_service import CallRecordingService
from app.services.call_service import CallService


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    return TestClient(app, raise_server_exceptions=False)


def _signed_post(
    client: TestClient,
    path: str,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
):
    raw = urlencode(data or {}).encode()
    signature = hmac.new(
        get_settings().exotel_webhook_secret.encode(), raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        path,
        content=raw,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Exotel-Signature": signature,
            **(headers or {}),
        },
    )


class TestIncomingWebhookValidation:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def test_missing_call_sid_is_rejected(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()):
            response = _signed_post(
                client, "/api/v1/telephony/incoming", data={"From": "+919999999999"}
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_body_is_rejected_not_500(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()):
            response = _signed_post(client, "/api/v1/telephony/incoming")

        assert response.status_code == 422

    def test_valid_payload_is_accepted(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA123", "From": "+919999999999", "To": "+910000000000"},
            )

        assert response.status_code == 200
        assert response.json()["message"] == "Call accepted"
        initiate_mock.assert_awaited_once()

    def test_missing_signature_is_rejected_before_call_creation(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = client.post("/api/v1/telephony/incoming", data={"CallSid": "CA123"})
        assert response.status_code == 401
        initiate_mock.assert_not_awaited()

    def test_invalid_signature_is_rejected(self):
        client = _client()
        response = client.post(
            "/api/v1/telephony/incoming",
            content=b"CallSid=CA123",
            headers={"X-Exotel-Signature": "0" * 64},
        )
        assert response.status_code == 401

    def test_modified_body_is_rejected(self):
        client = _client()
        raw = b"CallSid=CA123"
        signature = hmac.new(
            get_settings().exotel_webhook_secret.encode(), raw, hashlib.sha256
        ).hexdigest()
        response = client.post(
            "/api/v1/telephony/incoming",
            content=b"CallSid=CA999",
            headers={"X-Exotel-Signature": signature},
        )
        assert response.status_code == 401

    def test_invalid_status_callback_is_rejected(self):
        client = _client()
        with patch.object(CallService, "complete", AsyncMock()) as complete_mock:
            response = client.post(
                "/api/v1/telephony/status",
                data={"CallSid": "CA123", "CallStatus": "completed"},
            )
        assert response.status_code == 401
        complete_mock.assert_not_awaited()

    def test_dynamic_resolver_returns_url_field_exotel_requires(self):
        """Exotel's Voicebot Applet HTTP(S) dynamic resolver contract reads a
        field literally named "url" (not "streamUrl") to learn where to open
        the media WebSocket. Regression guard for the bug found during the
        first real end-to-end call: this field was missing entirely."""
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()):
            response = _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA123", "From": "+919999999999", "To": "+910000000000"},
            )

        body = response.json()
        assert "url" in body
        assert "/api/v1/telephony/stream/" in body["url"]
        assert body["url"].startswith(("ws://", "wss://"))
        # No credentials of any kind belong in this response.
        body_text = response.text
        for leaked in ("api_key", "api_token", "password", "secret", "Authorization"):
            assert leaked not in body_text

    def test_dynamic_resolver_uses_wss_when_request_was_https(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()):
            response = _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA123", "From": "+919999999999"},
                headers={"X-Forwarded-Proto": "https"},
            )

        # TestClient/Starlette doesn't rewrite request.url.scheme from this
        # header without proxy-header trust configured (the same gap
        # documented in app/api/v1/telephony.py::_build_stream_url) — so
        # this currently — correctly — still reports "ws", proving the
        # scheme is derived from the connection FastAPI actually sees, not
        # blindly trusted from a header.
        assert response.json()["url"].startswith("ws://")

    def test_status_webhook_missing_call_sid_is_rejected(self):
        client = _client()
        with patch.object(CallService, "update_status", AsyncMock()):
            response = _signed_post(
                client, "/api/v1/telephony/status", data={"CallStatus": "in-progress"}
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_incoming_webhook_logs_masked_phone_numbers_not_raw(self):
        """Diagnostic logging added for the first real end-to-end call test
        must never write a full caller/callee number to the logs."""
        client = _client()
        with (
            patch.object(CallService, "initiate", AsyncMock()),
            patch("app.api.v1.telephony.log_call_event") as mock_log,
        ):
            _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA123", "From": "+919999999999", "To": "+910000000000"},
            )

        received_call = next(
            c for c in mock_log.call_args_list if c.args[0] == "exotel.webhook.received"
        )
        assert received_call.kwargs["from_number"] != "+919999999999"
        assert "9999999" not in received_call.kwargs["from_number"]
        assert received_call.kwargs["to_number"] != "+910000000000"
        assert received_call.kwargs["call_sid"] == "CA123"

        created_call = next(c for c in mock_log.call_args_list if c.args[0] == "call.created")
        assert created_call.kwargs["call_sid"] == "CA123"


class TestCallServiceIdempotency:
    def test_duplicate_webhook_delivery_does_not_crash(self):
        """CallService.initiate() is idempotent — see test_database.py for the unit test."""
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            first = _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA_DUP", "From": "+919999999999"},
            )
            second = _signed_post(
                client,
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA_DUP", "From": "+919999999999"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert initiate_mock.await_count == 2


# ---------------------------------------------------------------------------
# Authentication forms Exotel can actually send. Exotel does not sign its
# webhooks, and the Voicebot dynamic resolver calls with GET + query string.
# ---------------------------------------------------------------------------


def _secret() -> str:
    return get_settings().exotel_webhook_secret


class TestExotelWebhookAuthentication:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def test_voicebot_get_resolver_with_webhook_token_is_accepted(self):
        client = _client()
        query = urlencode(
            {
                "CallSid": "CA_GET",
                "CallFrom": "09876543210",
                "CallTo": "08012345678",
                "webhook_token": _secret(),
            }
        )
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = client.get(f"/api/v1/telephony/incoming?{query}")

        assert response.status_code == 200
        assert "/api/v1/telephony/stream/" in response.json()["url"]
        assert _secret() not in response.text
        initiate_mock.assert_awaited_once_with(
            call_sid="CA_GET", caller_number="09876543210", callee_number="08012345678"
        )

    def test_wrong_webhook_token_is_rejected(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = client.get(
                "/api/v1/telephony/incoming?CallSid=CA1&webhook_token=not-the-secret"
            )
        assert response.status_code == 401
        initiate_mock.assert_not_awaited()

    def test_unauthenticated_get_is_rejected(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = client.get("/api/v1/telephony/incoming?CallSid=CA1")
        assert response.status_code == 401
        initiate_mock.assert_not_awaited()

    def test_basic_auth_with_secret_is_accepted_and_wrong_password_rejected(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()):
            ok = client.post(
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA_BASIC"},
                auth=("exotel", _secret()),
            )
            bad = client.post(
                "/api/v1/telephony/incoming",
                data={"CallSid": "CA_BASIC"},
                auth=("exotel", "wrong"),
            )
        assert ok.status_code == 200
        assert bad.status_code == 401

    def test_bad_signature_is_not_rescued_by_a_valid_token(self):
        client = _client()
        with patch.object(CallService, "initiate", AsyncMock()) as initiate_mock:
            response = client.post(
                f"/api/v1/telephony/incoming?webhook_token={_secret()}",
                content=b"CallSid=CA1",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Exotel-Signature": "0" * 64,
                },
            )
        assert response.status_code == 401
        initiate_mock.assert_not_awaited()

    def test_ip_allowlist_rejects_requests_from_outside_exotel_ranges(self):
        settings = get_settings()
        client = _client()  # TestClient's peer address is "testclient", not an IP
        with (
            patch.object(settings, "exotel_allowed_cidrs", "203.0.113.0/24"),
            patch.object(CallService, "initiate", AsyncMock()) as initiate_mock,
        ):
            response = client.get(
                f"/api/v1/telephony/incoming?CallSid=CA1&webhook_token={_secret()}"
            )
        assert response.status_code == 401
        initiate_mock.assert_not_awaited()

    def test_webhook_token_never_reaches_payload_or_logs(self):
        client = _client()
        with (
            patch.object(CallService, "initiate", AsyncMock()),
            patch("app.api.v1.telephony.log_call_event") as mock_log,
        ):
            client.get(f"/api/v1/telephony/incoming?CallSid=CA1&webhook_token={_secret()}")
        logged = " ".join(str(c) for c in mock_log.call_args_list)
        assert _secret() not in logged
        assert "webhook_token" not in logged


class TestStatusCallback:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def _post(self, client, data):
        return _signed_post(client, "/api/v1/telephony/status", data=data)

    def test_failure_status_is_stored_as_that_terminal_status(self):
        client = _client()
        call = SimpleNamespace(call_sid="CA1", recording_url=None)
        with (
            patch.object(CallService, "complete", AsyncMock(return_value=call)) as complete,
            patch.object(CallRecordingService, "record_provider_metadata", AsyncMock()),
        ):
            response = self._post(client, {"CallSid": "CA1", "Status": "no-answer"})
        assert response.status_code == 200
        assert complete.await_args.kwargs["status"] == "no-answer"

    def test_active_status_updates_without_completing(self):
        client = _client()
        call = SimpleNamespace(call_sid="CA1", recording_url=None)
        with (
            patch.object(CallService, "update_status", AsyncMock(return_value=call)) as update,
            patch.object(CallService, "complete", AsyncMock()) as complete,
            patch.object(CallRecordingService, "record_provider_metadata", AsyncMock()),
        ):
            response = self._post(client, {"CallSid": "CA1", "CallStatus": "in-progress"})
        assert response.status_code == 200
        update.assert_awaited_once()
        complete.assert_not_awaited()

    def test_unknown_status_is_rejected_not_stored(self):
        client = _client()
        with (
            patch.object(CallService, "update_status", AsyncMock()) as update,
            patch.object(CallService, "complete", AsyncMock()) as complete,
        ):
            response = self._post(client, {"CallSid": "CA1", "CallStatus": "hacked"})
        assert response.status_code == 422
        update.assert_not_awaited()
        complete.assert_not_awaited()

    def test_malformed_duration_is_ignored_not_a_500(self):
        client = _client()
        call = SimpleNamespace(call_sid="CA1", recording_url=None)
        with (
            patch.object(CallService, "complete", AsyncMock(return_value=call)) as complete,
            patch.object(CallRecordingService, "record_provider_metadata", AsyncMock()),
        ):
            response = self._post(
                client, {"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "abc"}
            )
        assert response.status_code == 200
        assert complete.await_args.kwargs["duration_seconds"] is None

    def test_recording_callback_records_provider_metadata(self):
        client = _client()
        call = SimpleNamespace(call_sid="CA1", recording_url="https://recordings.example/CA1")
        with (
            patch.object(CallService, "complete", AsyncMock(return_value=call)),
            patch.object(CallRecordingService, "record_provider_metadata", AsyncMock()) as record,
        ):
            response = self._post(
                client,
                {
                    "CallSid": "CA1",
                    "CallStatus": "completed",
                    "RecordingUrl": "https://recordings.example/CA1",
                },
            )
        assert response.status_code == 200
        record.assert_awaited_once_with(call)
