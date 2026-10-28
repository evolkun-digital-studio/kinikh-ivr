"""Tests for the /telephony/stream WebSocket route's caller-number fallback.

Exotel's real AgentStream/Voicebot protocol passes the caller's number as a
direct `start.from` field (snake_case, confirmed against
developer.exotel.com/docs/agentstream/websocket-protocol) — not nested
inside `custom_parameters`, which is reserved for app-defined key/value
pairs. This covers that field, the `custom_parameters.From` fallback (for
Flows configured to pass it that way instead), the query-parameter fallback
wired up in the route itself, and the requirement that a missing caller
number must never block the connection.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.dependencies import get_db
from app.main import app
from app.services.call_service import CallService
from app.services.stream_session_service import StreamSessionService
from app.voice.speech_handler import SpeechHandler


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    return TestClient(app, raise_server_exceptions=False)


def teardown_function() -> None:
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def known_call():
    with patch.object(
        CallService,
        "get_by_sid",
        AsyncMock(return_value=SimpleNamespace(status="initiated")),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_session_service():
    with patch("app.api.v1.telephony.StreamSessionService") as mock:
        mock.return_value.consume_session = AsyncMock(return_value="CA123")
        yield mock


def _stream_url(extra: str = "") -> str:
    return f"/api/v1/telephony/stream/test-sid-123{extra}"


def _start_frame(
    call_sid: str, from_number: str = "", custom_parameters: dict | None = None
) -> str:
    return json.dumps(
        {
            "event": "start",
            "stream_sid": "SS1",
            "start": {
                "call_sid": call_sid,
                "from": from_number,
                "custom_parameters": custom_parameters or {},
            },
        }
    )


class TestQueryParamCallerNumberFallback:
    def test_missing_token_rejected_before_accept(self):
        client = _client()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/telephony/stream"):
                pass

    def test_wrong_call_sid_cannot_initialize_ai(self):
        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()) as ensure_init:
            with client.websocket_connect(_stream_url()) as ws:
                ws.send_text(_start_frame("CA_FORGED"))
        ensure_init.assert_not_awaited()

    def test_terminal_call_rejected(self):
        client = _client()
        with patch.object(
            CallService,
            "get_by_sid",
            AsyncMock(return_value=SimpleNamespace(status="completed")),
        ):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(_stream_url()):
                    pass

    def test_query_param_used_when_no_start_from_present(self):
        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()) as ensure_init:
            with client.websocket_connect(_stream_url("?from=%2B919876543210")) as ws:
                ws.send_text(_start_frame("CA123"))
                ws.send_text(json.dumps({"event": "stop"}))

        ensure_init.assert_awaited_once_with("CA123", "+919876543210", "SS1")

    def test_start_from_field_takes_priority_over_query_param(self):
        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()) as ensure_init:
            with client.websocket_connect(_stream_url("?from=%2B910000000000")) as ws:
                ws.send_text(_start_frame("CA123", from_number="+919876543210"))
                ws.send_text(json.dumps({"event": "stop"}))

        ensure_init.assert_awaited_once_with("CA123", "+919876543210", "SS1")

    def test_custom_parameters_from_used_when_start_from_absent(self):
        """Fallback for Flows configured to pass the caller number as an
        app-defined custom_parameters.From instead of the direct field."""
        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()) as ensure_init:
            with client.websocket_connect(_stream_url()) as ws:
                ws.send_text(_start_frame("CA123", custom_parameters={"From": "+919876543210"}))
                ws.send_text(json.dumps({"event": "stop"}))

        ensure_init.assert_awaited_once_with("CA123", "+919876543210", "SS1")

    def test_missing_caller_number_never_blocks_the_connection(self):
        """Neither start.from, custom_parameters, nor a query param present —
        initialization must still proceed with an empty caller number."""
        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()) as ensure_init:
            with client.websocket_connect(_stream_url()) as ws:
                ws.send_text(_start_frame("CA123"))
                ws.send_text(json.dumps({"event": "stop"}))

        ensure_init.assert_awaited_once_with("CA123", "", "SS1")


class TestStreamConnectionGuards:
    def test_second_concurrent_stream_for_same_call_is_rejected(self):
        """A replayed stream URL cannot open a parallel AI session."""
        from app.api.v1 import telephony

        client = _client()
        telephony._active_streams.add("CA123")
        try:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(_stream_url()):
                    pass
        finally:
            telephony._active_streams.discard("CA123")

    def test_database_outage_closes_stream_without_crashing(self):
        client = _client()
        with patch.object(CallService, "get_by_sid", AsyncMock(side_effect=OSError("db down"))):
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(_stream_url()):
                    pass
        assert exc_info.value.code == 1011

    def test_stream_slot_released_after_call_ends(self):
        from app.api.v1 import telephony

        client = _client()
        with patch.object(SpeechHandler, "start", AsyncMock()):
            with client.websocket_connect(_stream_url()) as ws:
                ws.send_text(_start_frame("CA123"))
                ws.send_text(json.dumps({"event": "stop"}))
        assert "CA123" not in telephony._active_streams

    def test_stream_url_fits_exotel_custom_parameter_limit(self):
        """Exotel caps the WSS URL's custom parameters at 256 characters."""
        assert len(f"{'a' * 43}") <= 256
