"""Integration tests for the Exotel telephony module.

Tests cover individual components (CallManager, ExotelEventParser, ExotelProvider,
ExotelWebhookHandler, ExotelWebSocketHandler) as well as multi-component scenarios
that mirror real call lifecycles.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AuthenticationError
from app.telephony.adapter import CallInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_incoming_payload(
    call_sid: str = "CA123",
    from_number: str = "+919876543210",
    to_number: str = "+911234567890",
    status: str = "ringing",
) -> dict:
    return {
        "CallSid": call_sid,
        "From": from_number,
        "To": to_number,
        "CallStatus": status,
        "Direction": "inbound",
    }


def _make_status_payload(
    call_sid: str = "CA123",
    status: str = "completed",
    duration: str = "60",
    recording_url: str | None = None,
) -> dict:
    d: dict = {
        "CallSid": call_sid,
        "From": "+919876543210",
        "To": "+911234567890",
        "CallStatus": status,
        "Direction": "inbound",
        "CallDuration": duration,
    }
    if recording_url:
        d["RecordingUrl"] = recording_url
    return d


def _hmac_sign(payload: dict, secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class _FakeWebSocket:
    """Synchronous iterator-based fake WebSocket for testing stream.start()."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent: list[str] = []

    async def iter_text(self):
        for msg in self._messages:
            yield msg

    async def send_text(self, msg: str) -> None:
        self.sent.append(msg)


def _stream_start_msg(stream_sid: str = "MZ1", call_sid: str = "CA123") -> str:
    return json.dumps(
        {
            "event": "start",
            "streamSid": stream_sid,
            "start": {"callSid": call_sid},
        }
    )


def _stream_media_msg(audio: bytes = b"\x00\x01", stream_sid: str = "MZ1") -> str:
    return json.dumps(
        {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode(), "track": "inbound"},
        }
    )


def _stream_stop_msg(stream_sid: str = "MZ1") -> str:
    return json.dumps({"event": "stop", "streamSid": stream_sid})


# NOTE: the three helpers above intentionally keep the old camelCase shape —
# they exist to test ExotelEventParser (app/telephony/exotel/event_parser.py),
# which is dead/unused code that still expects that (also-buggy, but out of
# scope) shape. The helpers below use Exotel's real AgentStream/Voicebot
# snake_case protocol (confirmed against developer.exotel.com/docs/
# agentstream/websocket-protocol) and back ExotelWebSocketHandler tests,
# since that class shares the now-fixed ExotelAudioStream parser.


def _agentstream_start_msg(stream_sid: str = "MZ1", call_sid: str = "CA123") -> str:
    return json.dumps(
        {
            "event": "start",
            "stream_sid": stream_sid,
            "start": {"stream_sid": stream_sid, "call_sid": call_sid},
        }
    )


def _agentstream_media_msg(audio: bytes = b"\x00\x01", stream_sid: str = "MZ1") -> str:
    return json.dumps(
        {
            "event": "media",
            "stream_sid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode(), "track": "inbound"},
        }
    )


def _agentstream_stop_msg(stream_sid: str = "MZ1") -> str:
    return json.dumps({"event": "stop", "stream_sid": stream_sid})


# ---------------------------------------------------------------------------
# TestCallManager
# ---------------------------------------------------------------------------


class TestCallManager:
    @pytest.mark.asyncio
    async def test_register_creates_state(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        state = await mgr.register("CA1", "+91999")

        assert state.call_sid == "CA1"
        assert state.caller_number == "+91999"
        assert state.status == "initiated"

    @pytest.mark.asyncio
    async def test_register_sets_language(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        state = await mgr.register("CA1", "+91999", language="hi")
        assert state.language == "hi"

    @pytest.mark.asyncio
    async def test_get_returns_state(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91999")
        result = await mgr.get("CA1")
        assert result is not None
        assert result.call_sid == "CA1"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        assert await mgr.get("UNKNOWN") is None

    @pytest.mark.asyncio
    async def test_update_status_changes_value(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91999")
        result = await mgr.update_status("CA1", "in-progress")

        assert result is True
        state = await mgr.get("CA1")
        assert state.status == "in-progress"

    @pytest.mark.asyncio
    async def test_update_status_returns_false_for_unknown(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        assert await mgr.update_status("NOPE", "completed") is False

    @pytest.mark.asyncio
    async def test_set_language(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91999")
        ok = await mgr.set_language("CA1", "hi-en")

        assert ok is True
        assert (await mgr.get("CA1")).language == "hi-en"

    @pytest.mark.asyncio
    async def test_add_transcript_appends(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91999")
        await mgr.add_transcript("CA1", "Hello")
        await mgr.add_transcript("CA1", "World")

        state = await mgr.get("CA1")
        assert state.transcripts == ["Hello", "World"]

    @pytest.mark.asyncio
    async def test_remove_returns_state(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91999")
        removed = await mgr.remove("CA1")

        assert removed is not None
        assert removed.call_sid == "CA1"
        assert await mgr.get("CA1") is None

    @pytest.mark.asyncio
    async def test_remove_returns_none_for_unknown(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        assert await mgr.remove("GHOST") is None

    @pytest.mark.asyncio
    async def test_list_active_returns_all(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        await mgr.register("CA1", "+91111")
        await mgr.register("CA2", "+91222")

        active = await mgr.list_active()
        sids = {s.call_sid for s in active}
        assert sids == {"CA1", "CA2"}

    @pytest.mark.asyncio
    async def test_count(self):
        from app.telephony.exotel.call_manager import CallManager

        mgr = CallManager()
        assert await mgr.count() == 0
        await mgr.register("CA1", "+91111")
        await mgr.register("CA2", "+91222")
        assert await mgr.count() == 2
        await mgr.remove("CA1")
        assert await mgr.count() == 1


# ---------------------------------------------------------------------------
# TestExotelEventParser
# ---------------------------------------------------------------------------


class TestExotelEventParser:
    def test_parse_connected_event(self):
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamConnectedEvent

        parser = ExotelEventParser()
        event = parser.parse_stream_message(json.dumps({"event": "connected"}))

        assert isinstance(event, StreamConnectedEvent)
        assert event.event_type == "connected"

    def test_parse_start_event(self):
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamStartEvent

        parser = ExotelEventParser()
        event = parser.parse_stream_message(_stream_start_msg("MZ99", "CA42"))

        assert isinstance(event, StreamStartEvent)
        assert event.stream_sid == "MZ99"
        assert event.call_sid == "CA42"

    def test_parse_media_event_decodes_audio(self):
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamMediaEvent

        parser = ExotelEventParser()
        audio = b"\xde\xad\xbe\xef"
        event = parser.parse_stream_message(_stream_media_msg(audio))

        assert isinstance(event, StreamMediaEvent)
        assert event.audio_bytes == audio
        assert event.track == "inbound"

    def test_parse_dtmf_event(self):
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamDtmfEvent

        parser = ExotelEventParser()
        msg = json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}})
        event = parser.parse_stream_message(msg)

        assert isinstance(event, StreamDtmfEvent)
        assert event.digit == "5"

    def test_parse_stop_event(self):
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamStopEvent

        parser = ExotelEventParser()
        event = parser.parse_stream_message(_stream_stop_msg("MZ1"))

        assert isinstance(event, StreamStopEvent)
        assert event.stream_sid == "MZ1"

    def test_parse_unknown_event_returns_none(self):
        from app.telephony.exotel.event_parser import ExotelEventParser

        parser = ExotelEventParser()
        event = parser.parse_stream_message(json.dumps({"event": "custom_unknown"}))
        assert event is None

    def test_parse_invalid_json_returns_none(self):
        from app.telephony.exotel.event_parser import ExotelEventParser

        parser = ExotelEventParser()
        event = parser.parse_stream_message("not json at all {")
        assert event is None

    def test_parse_incoming_call_webhook(self):
        from app.telephony.exotel.event_parser import ExotelEventParser

        parser = ExotelEventParser()
        payload = _make_incoming_payload("CA55", "+91111", "+91222", "ringing")
        info = parser.parse_incoming_call(payload)

        assert isinstance(info, CallInfo)
        assert info.call_sid == "CA55"
        assert info.caller_number == "+91111"
        assert info.callee_number == "+91222"
        assert info.status == "ringing"
        assert info.direction == "inbound"

    def test_parse_status_change_webhook(self):
        from app.telephony.exotel.event_parser import ExotelEventParser

        parser = ExotelEventParser()
        payload = _make_status_payload("CA55", "completed", "120", "https://rec.example.com")
        info = parser.parse_status_change(payload)

        assert info.call_sid == "CA55"
        assert info.status == "completed"
        assert info.duration == 120
        assert info.recording_url == "https://rec.example.com"

    def test_parse_status_no_duration(self):
        from app.telephony.exotel.event_parser import ExotelEventParser

        parser = ExotelEventParser()
        payload = {
            "CallSid": "CA1",
            "From": "+91111",
            "CallStatus": "in-progress",
            "Direction": "inbound",
        }
        info = parser.parse_status_change(payload)
        assert info.duration is None


# ---------------------------------------------------------------------------
# TestExotelProvider
# ---------------------------------------------------------------------------


class TestExotelProvider:
    def _make_provider(self, client=None):
        from app.telephony.exotel.provider import ExotelProvider

        return ExotelProvider(client=client or MagicMock())

    @pytest.mark.asyncio
    async def test_answer_call_returns_ack(self):
        provider = self._make_provider()
        result = await provider.answer_call("CA1")
        assert result["CallSid"] == "CA1"
        assert result["action"] == "answered"

    @pytest.mark.asyncio
    async def test_hangup_delegates_to_client(self):
        mock_client = MagicMock()
        mock_client.hangup = AsyncMock(return_value={})
        provider = self._make_provider(client=mock_client)

        await provider.hangup_call("CA1")

        mock_client.hangup.assert_called_once_with("CA1")

    @pytest.mark.asyncio
    async def test_make_call_returns_call_info(self):
        mock_client = MagicMock()
        mock_client.make_call = AsyncMock(
            return_value={
                "Call": {"Sid": "CA99", "From": "+91111", "To": "+91222", "Status": "queued"}
            }
        )
        provider = self._make_provider(client=mock_client)

        info = await provider.make_call("+91111", "+91222", "https://cb.example.com")

        assert info.call_sid == "CA99"
        assert info.direction == "outbound"
        assert info.status == "queued"

    @pytest.mark.asyncio
    async def test_get_call_returns_call_info(self):
        mock_client = MagicMock()
        mock_client.get_call = AsyncMock(
            return_value={
                "Call": {
                    "Sid": "CA1",
                    "From": "+91111",
                    "Status": "in-progress",
                    "Direction": "inbound",
                    "Duration": "30",
                }
            }
        )
        provider = self._make_provider(client=mock_client)

        info = await provider.get_call("CA1")

        assert info.call_sid == "CA1"
        assert info.status == "in-progress"
        assert info.duration == 30

    @pytest.mark.asyncio
    async def test_get_call_no_duration(self):
        mock_client = MagicMock()
        mock_client.get_call = AsyncMock(
            return_value={"Call": {"Sid": "CA1", "From": "+91111", "Status": "ringing"}}
        )
        provider = self._make_provider(client=mock_client)

        info = await provider.get_call("CA1")
        assert info.duration is None

    def test_parse_incoming_webhook(self):
        provider = self._make_provider()
        payload = _make_incoming_payload("CA7")
        info = provider.parse_incoming_webhook(payload)
        assert info.call_sid == "CA7"

    def test_parse_status_webhook(self):
        provider = self._make_provider()
        payload = _make_status_payload("CA7", "completed", "45")
        info = provider.parse_status_webhook(payload)
        assert info.call_sid == "CA7"
        assert info.duration == 45

    def test_verify_signature_valid(self):
        secret = "test-webhook-secret-key-1234567890ab"
        payload = _make_incoming_payload()
        sig = _hmac_sign(payload, secret)
        raw = json.dumps(payload, separators=(",", ":")).encode()

        with patch("app.telephony.exotel.provider.get_settings") as mock_settings:
            mock_settings.return_value.exotel_webhook_secret = secret
            from app.telephony.exotel.provider import ExotelProvider

            provider = ExotelProvider(client=MagicMock())
            assert provider.verify_webhook_signature(raw, sig) is True

    def test_verify_signature_invalid(self):
        secret = "test-webhook-secret-key-1234567890ab"
        payload = _make_incoming_payload()
        raw = json.dumps(payload, separators=(",", ":")).encode()

        with patch("app.telephony.exotel.provider.get_settings") as mock_settings:
            mock_settings.return_value.exotel_webhook_secret = secret
            from app.telephony.exotel.provider import ExotelProvider

            provider = ExotelProvider(client=MagicMock())
            assert provider.verify_webhook_signature(raw, "badsig") is False

    @pytest.mark.asyncio
    async def test_send_digits_raises_not_implemented(self):
        provider = self._make_provider()
        with pytest.raises(NotImplementedError):
            await provider.send_digits("CA1", "123")


# ---------------------------------------------------------------------------
# TestExotelWebhookHandler
# ---------------------------------------------------------------------------


class TestExotelWebhookHandler:
    def _make_handler(self, verify_signatures=False):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.provider import ExotelProvider
        from app.telephony.exotel.webhook_handler import ExotelWebhookHandler

        provider = ExotelProvider(client=MagicMock())
        mgr = CallManager()
        return ExotelWebhookHandler(provider, mgr, verify_signatures=verify_signatures), mgr

    @pytest.mark.asyncio
    async def test_handle_incoming_registers_call(self):
        handler, mgr = self._make_handler()
        payload = _make_incoming_payload("CA10", "+91999")

        result = await handler.handle_incoming(payload)

        assert result["status"] == "accepted"
        assert result["call_sid"] == "CA10"
        state = await mgr.get("CA10")
        assert state is not None
        assert state.caller_number == "+91999"

    @pytest.mark.asyncio
    async def test_handle_incoming_rejects_invalid_signature(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.webhook_handler import ExotelWebhookHandler

        secret = "test-secret-for-webhook-handler-123"
        payload = _make_incoming_payload("CA10")

        with patch("app.telephony.exotel.provider.get_settings") as mock_settings:
            mock_settings.return_value.exotel_webhook_secret = secret
            from app.telephony.exotel.provider import ExotelProvider

            provider = ExotelProvider(client=MagicMock())
            mgr = CallManager()
            handler = ExotelWebhookHandler(provider, mgr, verify_signatures=True)

            with pytest.raises(AuthenticationError):
                await handler.handle_incoming(payload, signature="wrong-sig")

    @pytest.mark.asyncio
    async def test_handle_incoming_rejects_missing_signature(self):
        handler, mgr = self._make_handler(verify_signatures=True)
        payload = _make_incoming_payload("CA20")

        with pytest.raises(AuthenticationError):
            await handler.handle_incoming(payload, signature=None)
        assert await mgr.get("CA20") is None

    @pytest.mark.asyncio
    async def test_handle_status_removes_on_terminal(self):
        handler, mgr = self._make_handler()
        await mgr.register("CA10", "+91999")

        payload = _make_status_payload("CA10", "completed")
        result = await handler.handle_status(payload)

        assert result["status"] == "ok"
        assert await mgr.get("CA10") is None

    @pytest.mark.asyncio
    async def test_handle_status_updates_on_active(self):
        handler, mgr = self._make_handler()
        await mgr.register("CA10", "+91999")

        payload = _make_status_payload("CA10", "in-progress")
        await handler.handle_status(payload)

        state = await mgr.get("CA10")
        assert state.status == "in-progress"

    @pytest.mark.asyncio
    async def test_handle_status_removes_on_failed(self):
        handler, mgr = self._make_handler()
        await mgr.register("CA10", "+91999")

        payload = _make_status_payload("CA10", "failed")
        await handler.handle_status(payload)

        assert await mgr.get("CA10") is None

    @pytest.mark.asyncio
    async def test_handle_status_removes_on_busy(self):
        handler, mgr = self._make_handler()
        await mgr.register("CA10", "+91999")

        payload = _make_status_payload("CA10", "busy")
        await handler.handle_status(payload)

        assert await mgr.get("CA10") is None


# ---------------------------------------------------------------------------
# TestExotelWebSocketHandler
# ---------------------------------------------------------------------------


class TestExotelWebSocketHandler:
    @pytest.mark.asyncio
    async def test_run_starts_and_exits_cleanly(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        ws = _FakeWebSocket([_agentstream_start_msg(), _agentstream_stop_msg()])
        handler = ExotelWebSocketHandler(mgr)

        await handler.run(ws)
        assert handler.call_sid == "CA123"

    @pytest.mark.asyncio
    async def test_run_invokes_on_audio(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        audio = b"\xde\xad"
        received: list[bytes] = []

        async def on_audio(chunk: bytes) -> None:
            received.append(chunk)

        ws = _FakeWebSocket(
            [_agentstream_start_msg(), _agentstream_media_msg(audio), _agentstream_stop_msg()]
        )
        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws, on_audio=on_audio)

        assert received == [audio]

    @pytest.mark.asyncio
    async def test_run_invokes_on_dtmf(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        dtmf_received: list[str] = []

        async def on_dtmf(digit: str) -> None:
            dtmf_received.append(digit)

        dtmf_msg = json.dumps({"event": "dtmf", "dtmf": {"digit": "9"}})
        ws = _FakeWebSocket([_agentstream_start_msg(), dtmf_msg, _agentstream_stop_msg()])
        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws, on_dtmf=on_dtmf)

        assert dtmf_received == ["9"]

    @pytest.mark.asyncio
    async def test_run_removes_call_from_manager_on_exit(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        await mgr.register("CA123", "+91999")

        ws = _FakeWebSocket([_agentstream_start_msg(), _agentstream_stop_msg()])
        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws)

        assert await mgr.get("CA123") is None

    @pytest.mark.asyncio
    async def test_send_audio_forwards_to_stream(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        ws = _FakeWebSocket([_agentstream_start_msg(), _agentstream_stop_msg()])
        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws)

        await handler.send_audio(b"outbound-audio")

        # Verify at least one send_text was called (stop msg + stream start ack, etc.)
        # The outbound audio goes via send_text on the websocket.
        sent_events = [json.loads(m) for m in ws.sent]
        media_events = [e for e in sent_events if e.get("event") == "media"]
        assert len(media_events) == 1
        assert base64.b64decode(media_events[0]["media"]["payload"]) == b"outbound-audio"

    @pytest.mark.asyncio
    async def test_stop_sends_stop_message(self):
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        ws = _FakeWebSocket([_agentstream_start_msg(), _agentstream_stop_msg()])
        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws)
        await handler.stop()

        stop_events = [json.loads(m) for m in ws.sent if json.loads(m).get("event") == "stop"]
        assert len(stop_events) == 1


# ---------------------------------------------------------------------------
# Integration scenarios
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_call_lifecycle_via_webhook(self):
        """Incoming call → webhook registers → status-change removes."""
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.provider import ExotelProvider
        from app.telephony.exotel.webhook_handler import ExotelWebhookHandler

        provider = ExotelProvider(client=MagicMock())
        mgr = CallManager()
        handler = ExotelWebhookHandler(provider, mgr, verify_signatures=False)

        # Step 1: Incoming call webhook
        incoming = await handler.handle_incoming(_make_incoming_payload("CA42", "+91777"))
        assert incoming["call_sid"] == "CA42"
        assert await mgr.count() == 1

        # Step 2: Call connects
        await handler.handle_status(_make_status_payload("CA42", "in-progress"))
        state = await mgr.get("CA42")
        assert state.status == "in-progress"

        # Step 3: Call ends
        await handler.handle_status(_make_status_payload("CA42", "completed", "90"))
        assert await mgr.get("CA42") is None
        assert await mgr.count() == 0

    @pytest.mark.asyncio
    async def test_websocket_session_removes_call_on_disconnect(self):
        """WebSocket stream registers call in manager and removes it when stream ends."""
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.websocket_handler import ExotelWebSocketHandler

        mgr = CallManager()
        await mgr.register("CA55", "+91888")
        assert await mgr.count() == 1

        audio_chunks: list[bytes] = []

        async def collect_audio(chunk: bytes) -> None:
            audio_chunks.append(chunk)

        sample_audio = b"\xff\xee\xdd"
        ws = _FakeWebSocket(
            [
                _agentstream_start_msg("MZ55", "CA55"),
                _agentstream_media_msg(sample_audio, "MZ55"),
                _agentstream_stop_msg("MZ55"),
            ]
        )

        handler = ExotelWebSocketHandler(mgr)
        await handler.run(ws, on_audio=collect_audio)

        # Audio was received
        assert audio_chunks == [sample_audio]
        # Call removed after stream ended
        assert await mgr.get("CA55") is None

    @pytest.mark.asyncio
    async def test_make_call_and_get_call(self):
        """Provider can initiate an outbound call and retrieve its status."""
        from app.telephony.exotel.provider import ExotelProvider

        mock_client = MagicMock()
        mock_client.make_call = AsyncMock(
            return_value={
                "Call": {
                    "Sid": "CAout1",
                    "From": "+911234567890",
                    "To": "+919876543210",
                    "Status": "queued",
                }
            }
        )
        mock_client.get_call = AsyncMock(
            return_value={
                "Call": {
                    "Sid": "CAout1",
                    "From": "+911234567890",
                    "To": "+919876543210",
                    "Status": "in-progress",
                    "Direction": "outbound",
                    "Duration": None,
                }
            }
        )

        provider = ExotelProvider(client=mock_client)
        made = await provider.make_call("+911234567890", "+919876543210", "https://cb.example.com")
        assert made.call_sid == "CAout1"
        assert made.direction == "outbound"

        fetched = await provider.get_call("CAout1")
        assert fetched.status == "in-progress"

    @pytest.mark.asyncio
    async def test_event_parser_and_call_manager_roundtrip(self):
        """Event parser's output feeds correctly into CallManager."""
        from app.telephony.exotel.call_manager import CallManager
        from app.telephony.exotel.event_parser import ExotelEventParser, StreamStartEvent

        parser = ExotelEventParser()
        mgr = CallManager()

        # Parse a stream start event
        raw_msg = _stream_start_msg("MZ77", "CA77")
        event = parser.parse_stream_message(raw_msg)
        assert isinstance(event, StreamStartEvent)

        # Register the call using parsed details
        await mgr.register(event.call_sid, caller_number="+91000")
        state = await mgr.get(event.call_sid)
        assert state is not None
        assert state.call_sid == "CA77"


class TestExotelStreamRobustness:
    """A malformed frame from the network must be dropped, never end the call."""

    @pytest.mark.asyncio
    async def test_malformed_frames_are_skipped_and_stream_continues(self):
        from app.telephony.exotel.stream import ExotelAudioStream

        received: list[bytes] = []

        async def on_audio(chunk: bytes) -> None:
            received.append(chunk)

        ws = _FakeWebSocket(
            [
                "not json",
                json.dumps([1, 2, 3]),
                json.dumps({"event": "start", "start": "not-an-object"}),
                _agentstream_start_msg(),
                json.dumps({"event": "media", "media": {"payload": "%%%not-base64%%%"}}),
                json.dumps({"event": "media", "media": "not-an-object"}),
                json.dumps({"event": "dtmf", "dtmf": None}),
                _agentstream_media_msg(b"\x01\x02"),
                _agentstream_stop_msg(),
            ]
        )
        stream = ExotelAudioStream(websocket=ws, on_audio=on_audio)
        await stream.start()

        assert received == [b"\x01\x02"]
        assert stream.call_sid == "CA123"
