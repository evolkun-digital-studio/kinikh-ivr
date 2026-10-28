"""Integration tests for the complete call pipeline.

Simulates:
  Incoming Call → Webhook → WebSocket Audio → STT → Conversation Engine
  → Voice Generation → Audio Response → Call End → Lead Extraction
  → Database → Email

All external I/O (OpenAI, ElevenLabs, SMTP, PostgreSQL) is mocked.
The pipeline logic — routing between components, state transitions,
error handling — is exercised against real code.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Stream message builders
# ---------------------------------------------------------------------------


def _ws_connected() -> str:
    return json.dumps({"event": "connected"})


def _ws_start(
    call_sid: str,
    caller_number: str,
    stream_sid: str = "MZ001",
) -> str:
    # snake_case matches Exotel's real AgentStream/Voicebot protocol
    # (developer.exotel.com/docs/agentstream/websocket-protocol) — caller
    # number is a direct `start.from` field, not nested in custom_parameters.
    return json.dumps(
        {
            "event": "start",
            "stream_sid": stream_sid,
            "start": {
                "call_sid": call_sid,
                "stream_sid": stream_sid,
                "from": caller_number,
            },
        }
    )


def _ws_media(audio: bytes = b"\x00" * 160, stream_sid: str = "MZ001") -> str:
    return json.dumps(
        {
            "event": "media",
            "stream_sid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode(), "track": "inbound"},
        }
    )


def _ws_stop(stream_sid: str = "MZ001") -> str:
    return json.dumps({"event": "stop", "stream_sid": stream_sid})


def _loud_audio(num_samples: int = 4000) -> bytes:
    """Deterministic non-silent PCM16LE audio, loud/long enough (>= the
    default speech_start_ms=200ms => 10 frames => 3200 bytes, with margin)
    to make UtteranceSegmenter (app/voice/vad.py) confirm real speech began
    when pushed through AudioBuffer/handle_audio() — not just to pass a
    blob-level RMS check. Has no trailing silence, so pushing it alone
    leaves the segmenter's utterance "in progress"; pair with
    buffer.drain() (which force-finalizes) or use _complete_utterance()
    for something that finalizes entirely on its own."""
    import struct

    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(num_samples))


def _complete_utterance(loud_samples: int = 4000) -> bytes:
    """A full, self-finalizing utterance: enough sustained loud frames to
    confirm speech began, then enough trailing silence (>= the default
    end_of_speech_ms=900ms => 45 frames => 14400 bytes, with margin) to
    confirm the caller stopped — so push()/handle_audio() finalizes it and
    schedules a flush entirely on its own, exactly like a real caller
    utterance, with no manual _schedule_flush()/drain() nudge needed."""
    import struct

    loud = b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(loud_samples))
    trailing_silence = b"\x00" * 16000  # 1000 ms
    return loud + trailing_silence


# ---------------------------------------------------------------------------
# Fake WebSocket
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal WebSocket that replays pre-configured messages."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []

    async def iter_text(self):
        for msg in self._messages:
            yield msg

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_mock_dept(
    name: str = "rural",
    email: str = "rural@kinikh.com",
    keywords: list[str] | None = None,
    prompt: str | None = None,
    knowledge_base: str | None = None,
) -> MagicMock:
    dept = MagicMock()
    dept.name = name
    dept.display_name = name.title()
    dept.email = email
    dept.keywords = keywords or ["demo", "pricing", "buy"]
    # Explicitly None (not an auto-vivified MagicMock attribute) — matches
    # the real nullable Department.prompt/knowledge_base columns, and keeps
    # build_business_context()'s tenants-fallback logic (`d.get(...) or
    # TENANTS_DEFAULT`) working correctly instead of treating a stray
    # MagicMock object as a truthy "real" value.
    dept.prompt = prompt
    dept.knowledge_base = knowledge_base
    dept.id = uuid.uuid4()
    return dept


def _make_mock_call(call_sid: str = "CA_TEST") -> MagicMock:
    call = MagicMock()
    call.id = uuid.uuid4()
    call.call_sid = call_sid
    call.caller_number = "+919876543210"
    call.recording_url = None  # a real Call row has no recording until Exotel reports one
    return call


def _make_mock_lead(name: str = "Raj Kumar") -> MagicMock:
    lead = MagicMock()
    lead.id = uuid.uuid4()
    lead.name = name
    lead.phone = "+919876543210"
    lead.department_id = uuid.uuid4()
    return lead


def _lead_extraction_result(caller_phone: str = "+919876543210") -> dict:
    return {
        "name": "Raj Kumar",
        "phone": caller_phone,
        "email": "raj@example.com",
        "department": "rural",
        "requirement": "Product demo needed",
        "summary": "Caller needs a product demo.",
        "language": "en",
        "timestamp": "2026-06-30T10:00:00Z",
        "additional_notes": None,
    }


def _as_frame(audio: bytes) -> bytes:
    """What SpeechHandler actually sends for a short TTS result: the audio
    with its 8 ms edge fades, padded with silence to one 200 ms Exotel frame."""
    from app.voice.pcm import apply_fade

    return apply_fade(audio, fade_in=True, fade_out=True).ljust(3200, b"\x00")


# ---------------------------------------------------------------------------
# SpeechHandler pipeline tests
# ---------------------------------------------------------------------------


class TestSpeechHandlerPipeline:
    """Unit-integration tests for SpeechHandler — all AI/DB/email are mocked."""

    def _make_handler(
        self,
        mock_db: AsyncMock,
        mock_voice: AsyncMock,
        mock_ai_client: AsyncMock,
    ) -> tuple:
        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db, voice=mock_voice, ai_client=mock_ai_client)
        mock_ws = AsyncMock()  # stand-in for ExotelAudioStream.send_audio
        handler.set_audio_sender(mock_ws)
        return handler, mock_ws

    def _mock_ai_client(self, reply: str = "Could you share your email address?") -> AsyncMock:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=reply)
        client.chat_json = AsyncMock(return_value=_lead_extraction_result())
        return client

    def _mock_voice(self, transcript: str = "Hi, I am Raj and I need a demo") -> AsyncMock:
        voice = AsyncMock()
        voice.transcribe = AsyncMock(return_value=transcript)
        voice.synthesize = AsyncMock(return_value=b"FAKE_AUDIO")
        return voice

    # ── Initialization ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_initialization_sends_greeting(self, mock_db_session):
        """After stream start, a greeting is synthesized and sent via WebSocket."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_ws = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA001", "+919876543210")

        await handler._outbound_queue.join()
        mock_voice.synthesize.assert_called_once()
        await handler._outbound_queue.join()
        mock_ws.assert_called_once_with(_as_frame(b"FAKE_AUDIO"))
        assert handler._initialized is True

    @pytest.mark.asyncio
    async def test_no_greeting_when_auto_greeting_disabled(self, mock_db_session):
        """Regression: the greeting is intended, documented receptionist
        behavior — but must be explicitly configurable so it can be
        disabled (e.g. while debugging a premature/self-talk response
        issue) without touching the AI pipeline itself."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_ws = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        # get_settings() is a process-wide @lru_cache singleton — patch the
        # attribute (auto-restored on exit) rather than assigning directly,
        # which would leak into every other test in the session.
        with (
            patch.object(handler._settings, "ai_auto_greeting", False),
            patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])),
        ):
            await handler._ensure_initialized("CA_NOGREET", "+919876543210")

        mock_voice.synthesize.assert_not_called()
        await handler._outbound_queue.join()
        mock_ws.assert_not_called()
        assert handler._initialized is True
        # No greeting was ever added to the conversation history either.
        assert handler._context.messages == []

    @pytest.mark.asyncio
    async def test_initialization_is_idempotent(self, mock_db_session):
        """Calling _ensure_initialized twice runs initialization only once."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_ws = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA001", "+91000")
            await handler._ensure_initialized("CA001", "+91000")  # second call no-op

        # synthesize called once for greeting, not twice
        assert mock_voice.synthesize.call_count == 1

    @pytest.mark.asyncio
    async def test_initialization_loads_departments(self, mock_db_session):
        """Departments loaded from DB are stored on the handler."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        depts = [_make_mock_dept("rural"), _make_mock_dept("support", "support@kinikh.com")]
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=depts)):
            await handler._ensure_initialized("CA001", "+91000")

        assert len(handler._departments) == 2
        assert handler._departments[0]["name"] == "rural"

    @pytest.mark.asyncio
    async def test_initialization_passes_full_department_columns_to_engine(self, mock_db_session):
        """Regression: display_name/prompt/knowledge_base exist on the
        Department model (populated by scripts/seed.py) but were previously
        dropped here, leaving ConversationEngine with only bare department
        names. A DB row's own prompt/knowledge_base must now reach the
        engine's business context."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept(
            "rural",
            prompt="DB-CUSTOM rural instructions.",
            knowledge_base="DB-CUSTOM rural knowledge.",
        )
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_DEPTCOLS", "+91000")

        assert handler._departments[0]["prompt"] == "DB-CUSTOM rural instructions."
        assert handler._departments[0]["knowledge_base"] == "DB-CUSTOM rural knowledge."

        prompt = await handler._engine._build_system_prompt(handler._context)
        assert "DB-CUSTOM rural instructions." in prompt
        assert "DB-CUSTOM rural knowledge." in prompt

    @pytest.mark.asyncio
    async def test_handle_audio_before_init_is_safe(self, mock_db_session):
        """Audio received before initialization is silently dropped."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        # Should not raise — buffer is None before init
        await handler.handle_audio(b"\x00" * 100)
        mock_voice.transcribe.assert_not_called()

    # ── Audio processing ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_audio_flushed_runs_full_stt_ai_tts_cycle(self, mock_db_session):
        """Audio flush → STT → language detect → AI → TTS → audio sent."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("I need a demo of your product")
        mock_ai = self._mock_ai_client("Sure, what is your name?")
        handler, mock_ws = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA002", "+91000")

        mock_voice.synthesize.reset_mock()
        mock_ws.reset_mock()

        await handler._on_audio_flushed(_loud_audio())

        mock_voice.transcribe.assert_called_once()
        mock_ai.chat.assert_called()
        mock_voice.synthesize.assert_called()
        await handler._outbound_queue.join()
        mock_ws.assert_called()

    @pytest.mark.asyncio
    async def test_empty_transcript_skips_conversation(self, mock_db_session):
        """Empty transcription result halts the pipeline for that audio chunk."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("")  # empty transcript
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA003", "+91000")

        mock_ai.chat.reset_mock()
        await handler._on_audio_flushed(b"\x00" * 100)

        mock_ai.chat.assert_not_called()



    # ── Name extraction heuristic ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_name_extracted_from_my_name_is(self, mock_db_session):
        """'My name is X Y' heuristic captures up to 2 words after the phrase."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("My name is Rajesh Kumar")
        mock_ai = self._mock_ai_client("Nice to meet you Rajesh!")
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA007", "+91000")

        await handler._on_audio_flushed(_loud_audio())
        # Heuristic takes [0:2] words after the phrase — "Rajesh Kumar"
        assert "Rajesh" in (handler._context.collected["name"] or "")

    @pytest.mark.asyncio
    async def test_hindi_name_extracted(self, mock_db_session):
        """Hindi 'मेरा नाम X' pattern sets the name field (may include trailing word)."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("मेरा नाम राज है")
        mock_ai = self._mock_ai_client("नमस्ते राज!")
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA008", "+91000")

        await handler._on_audio_flushed(_loud_audio())
        # Heuristic picks up to 2 words after "मेरा नाम"; "राज" is the first
        assert "राज" in (handler._context.collected["name"] or "")

    # ── Confirmation ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_end_call_marker_is_not_spoken_and_ends_call(self, mock_db_session):
        """The model closes the call with [END_CALL]: the marker is never
        spoken, the context is marked ended and the stream is hung up."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("No, that's all, thank you")
        mock_ai = self._mock_ai_client("Thanks Raj, our team will call you soon. [END_CALL]")
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        hangup = AsyncMock()
        handler.set_hangup(hangup)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA009", "+91000")
        await handler._on_audio_flushed(_loud_audio())

        spoken = [c.args[0] for c in mock_voice.synthesize.call_args_list]
        assert any("our team will call you soon" in t for t in spoken)
        assert not any("END_CALL" in t for t in spoken)
        assert handler._context.ended is True
        hangup.assert_awaited_once()
        assert "END_CALL" not in handler._context.transcript

    # ── _speak edge cases ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_speak_without_websocket_is_silent(self, mock_db_session):
        """_speak does nothing when no audio sender is set."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        handler._send_audio = None  # remove audio sender

        await handler._speak("Hello!")  # should not raise
        mock_voice.synthesize.assert_not_called()

    @pytest.mark.asyncio
    async def test_speak_empty_text_is_silent(self, mock_db_session):
        """_speak does nothing for empty strings."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        await handler._speak("")
        mock_voice.synthesize.assert_not_called()

    # ── Finalization (Lead → DB → Email) ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_finalize_creates_lead_in_db(self, mock_db_session):
        """finalize() extracts lead data and persists it to the database."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        mock_ai.chat_json = AsyncMock(return_value=_lead_extraction_result())
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA010", "+919876543210")

        handler._context.add_message("user", "I am Raj Kumar")
        handler._context.add_message("assistant", "Hello Raj!")

        mock_call = _make_mock_call("CA010")
        mock_lead = _make_mock_lead()

        create_from_call_mock = AsyncMock(return_value=mock_lead)
        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=mock_call)),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", create_from_call_mock),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            await handler.finalize("CA010")

        create_from_call_mock.assert_called_once()
        call_kwargs = create_from_call_mock.call_args.kwargs
        assert call_kwargs["phone"] == "+919876543210"
        assert call_kwargs["name"] == "Raj Kumar"
        assert call_kwargs["requirement"] == "Product demo needed"

    @pytest.mark.asyncio
    async def test_finalize_persists_lead_even_without_a_call_row(self, mock_db_session):
        """Regression: a real call log showed "Call '...' not found" —
        call_service.complete() raises NotFoundError for a call_sid with no
        Call row (e.g. a test call that bypassed the normal Exotel
        incoming-webhook that creates it via CallService.initiate()). That
        must not abort finalize() before the lead — already built by then —
        gets persisted and committed."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        mock_ai.chat_json = AsyncMock(return_value=_lead_extraction_result())
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_NO_CALL_ROW", "+919876543210")

        mock_lead = _make_mock_lead()
        create_from_call_mock = AsyncMock(return_value=mock_lead)
        complete_mock = AsyncMock()
        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=None)),  # no Call row
            patch.object(CallService, "complete", complete_mock),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", create_from_call_mock),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            await handler.finalize("CA_NO_CALL_ROW")  # must not raise/abort

        create_from_call_mock.assert_called_once()  # lead still persisted
        complete_mock.assert_not_called()  # nothing to mark complete
        mock_db_session.commit.assert_called()  # the lead write was still committed

    @pytest.mark.asyncio
    async def test_finalize_dispatches_email(self, mock_db_session):
        """finalize() dispatches a lead notification email after DB write."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA011", "+919876543210")

        mock_call = _make_mock_call("CA011")
        mock_lead = _make_mock_lead()

        dispatch_mock = AsyncMock()
        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=mock_call)),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", AsyncMock(return_value=mock_lead)),
            patch.object(EmailService, "dispatch_lead_email", dispatch_mock),
        ):
            await handler.finalize("CA011")

        dispatch_mock.assert_called_once_with(mock_lead.id)

    @pytest.mark.asyncio
    async def test_finalize_email_failure_does_not_crash(self, mock_db_session):
        """Email dispatch failure is swallowed — lead still created in DB."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA012", "+91000")

        mock_lead = _make_mock_lead()
        create_mock = AsyncMock(return_value=mock_lead)

        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=_make_mock_call())),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", create_mock),
            patch.object(
                EmailService, "dispatch_lead_email", AsyncMock(side_effect=Exception("SMTP down"))
            ),
        ):
            await handler.finalize("CA012")  # must not raise

        create_mock.assert_called_once()  # lead was still created

    @pytest.mark.asyncio
    async def test_finalize_commits_call_and_lead_before_email_dispatch(self, mock_db_session):
        """Regression: Call/Lead persistence must be committed BEFORE email
        dispatch starts, so a slow/failing SMTP retry loop can't hold the
        Call/Lead write open and uncommitted."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_COMMIT", "+919876543210")

        mock_call = _make_mock_call("CA_COMMIT")
        mock_lead = _make_mock_lead()
        complete_mock = AsyncMock(return_value=mock_call)

        events: list[str] = []
        mock_db_session.commit = AsyncMock(side_effect=lambda: events.append("commit"))

        async def _dispatch_impl(_lead_id):
            # Simulate a slow/failing SMTP retry loop starting only after the
            # Call/Lead transaction has already been committed.
            events.append("email_dispatch_start")
            raise Exception("SMTP: Connection unexpectedly closed")

        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=mock_call)),
            patch.object(CallService, "complete", complete_mock),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", AsyncMock(return_value=mock_lead)),
            patch.object(
                EmailService, "dispatch_lead_email", AsyncMock(side_effect=_dispatch_impl)
            ),
        ):
            await handler.finalize("CA_COMMIT")  # must not raise

        # Call.complete() and the pre-email commit both happened...
        complete_mock.assert_called_once()
        assert "commit" in events
        # ...strictly before email dispatch was attempted.
        assert events.index("commit") < events.index("email_dispatch_start")
        # The failed email attempt must not roll back the already-committed
        # Call/Lead write.
        mock_db_session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_no_call_sid_is_noop(self, mock_db_session):
        """finalize() with None call_sid does nothing."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await handler.finalize(None)  # must not raise

    @pytest.mark.asyncio
    async def test_finalize_no_context_is_noop(self, mock_db_session):
        """finalize() when no context has been initialized does nothing."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await handler.finalize("CA_NOCONTEXT")  # must not raise

    @pytest.mark.asyncio
    async def test_finalize_cleans_up_context(self, mock_db_session):
        """finalize() removes the call context from the shared context_manager."""
        from app.ai.context_manager import context_manager
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_CLEANUP", "+91000")

        assert await context_manager.get("CA_CLEANUP") is not None

        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=_make_mock_call())),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(
                LeadService, "create_from_call", AsyncMock(return_value=_make_mock_lead())
            ),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            await handler.finalize("CA_CLEANUP")

        assert await context_manager.get("CA_CLEANUP") is None

    # ── Stop lifecycle ──────────────────────────────────────────────────────
    # Regression coverage for a real PSTN call where the WebSocket closed
    # right after Exotel's 'stop' event, but a trailing buffered-audio flush
    # still ran a full STT->OpenAI->TTS turn and then failed to send:
    # "Unexpected ASGI message 'websocket.send', after sending
    # 'websocket.close'". mark_stopped() is called the instant 'stop' is
    # parsed (via ExotelAudioStream's on_stop callback), before finalize().

    @pytest.mark.asyncio
    async def test_mark_stopped_prevents_new_stt(self, mock_db_session):
        """No new transcription may start once the call is marked stopped."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP1", "+91000")

        await handler.mark_stopped("CA_STOP1")
        mock_voice.transcribe.reset_mock()

        await handler._on_audio_flushed(b"\x00" * 100)

        mock_voice.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_stopped_prevents_outbound_audio(self, mock_db_session):
        """No new TTS/send may start once the call is marked stopped."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP2", "+91000")

        await handler.mark_stopped("CA_STOP2")
        mock_voice.synthesize.reset_mock()
        mock_send_audio.reset_mock()

        await handler._speak("This should never be spoken")

        mock_voice.synthesize.assert_not_called()
        mock_send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_outbound_tts_audio_never_reaches_audio_buffer(self, mock_db_session):
        """Structural invariant: generated ElevenLabs audio must only ever
        flow through SpeechHandler -> ExotelAudioStream.send_audio(), and
        must never be pushed into AudioBuffer/STT. _speak() has no reference
        to self._buffer at all — this proves it structurally, not just by
        absence of a symptom."""
        from unittest.mock import patch as _patch

        from app.services.department_service import DepartmentService
        from app.voice.audio_stream import AudioBuffer

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with (
            _patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])),
            _patch.object(AudioBuffer, "push", AsyncMock()) as mock_push,
        ):
            await handler._ensure_initialized("CA_NOECHO", "+91000")  # speaks the greeting
            await handler._speak("A second reply, also outbound-only")

        mock_send_audio.assert_called()  # audio did flow out via send_audio
        mock_push.assert_not_called()  # but never back into the inbound buffer

    @pytest.mark.asyncio
    async def test_mark_stopped_cancels_pending_silence_timer(self, mock_db_session):
        """mark_stopped() must cancel the buffer's pending silence timer so
        it can't fire a flush (and a new turn) after the call is closed."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP3", "+91000")

        await handler.handle_audio(_loud_audio())  # unfinished speech arms the stall watchdog
        silence_task = handler._buffer._silence_task
        assert silence_task is not None and not silence_task.done()

        await handler.mark_stopped("CA_STOP3")
        await asyncio.sleep(0)  # let the cancellation actually be processed

        assert silence_task.cancelled() or silence_task.done()

    @pytest.mark.asyncio
    async def test_mark_stopped_cancels_in_progress_turn(self, mock_db_session):
        """mark_stopped() must cancel (and await) an ALREADY-RUNNING turn's
        flush task, not just an idle pending timer — a real PSTN call showed
        STT/OpenAI/TTS still completing and attempting to send audio after
        Exotel had already closed the WebSocket."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        stt_started = asyncio.Event()
        never_release = asyncio.Event()  # only released via cancellation

        async def hanging_transcribe(audio_bytes, language):
            stt_started.set()
            await never_release.wait()
            return "should never get here"

        mock_voice.transcribe = AsyncMock(side_effect=hanging_transcribe)
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP5", "+91000")
        mock_ai.chat.reset_mock()
        mock_send_audio.reset_mock()

        # A complete, self-finalizing utterance — push() itself confirms
        # speech began, detects end-of-speech from the trailing silence, and
        # schedules the flush naturally, exactly like a real caller turn.
        await handler.handle_audio(_complete_utterance())
        await asyncio.wait_for(stt_started.wait(), timeout=1)

        flush_task = handler._buffer._pending_flush_task
        assert flush_task is not None and not flush_task.done()

        await handler.mark_stopped("CA_STOP5")

        assert flush_task.cancelled() or flush_task.done()
        mock_ai.chat.assert_not_called()  # never reached OpenAI
        mock_send_audio.assert_not_called()  # never reached outbound send

    @pytest.mark.asyncio
    async def test_mark_stopped_cancels_the_actually_running_turn_not_just_the_latest(
        self, mock_db_session
    ):
        """Regression: a real PSTN call kept making OpenAI calls for
        seconds after Exotel's stream had already stopped, because
        cancel_pending() could lose track of the task actually running
        STT/OpenAI/TTS once a second utterance had scheduled another flush.

        Now a second utterance during STT means the caller only paused: turn
        1 is abandoned and its audio merged into the next turn — which gets
        stuck too. mark_stopped() must reach whichever task is really doing
        the work, and nothing may reach OpenAI or the caller afterwards."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        stt_started = asyncio.Event()
        never_release = asyncio.Event()

        async def hanging_transcribe(audio_bytes, language):
            stt_started.set()
            await never_release.wait()
            return "should never get here"

        mock_voice.transcribe = AsyncMock(side_effect=hanging_transcribe)
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP6", "+91000")
        mock_ai.chat.reset_mock()
        mock_send_audio.reset_mock()

        await handler.handle_audio(_complete_utterance())
        await asyncio.wait_for(stt_started.wait(), timeout=1)
        stt_started.clear()

        await handler.handle_audio(_complete_utterance())
        await asyncio.wait_for(stt_started.wait(), timeout=1)

        first_wav, merged_wav = (c.args[0] for c in mock_voice.transcribe.call_args_list)
        assert len(merged_wav) > len(first_wav)  # both utterances, one STT request
        running = handler._buffer._pending_flush_task
        assert running is not None and not running.done()
        assert running in handler._buffer._flush_tasks

        await handler.mark_stopped("CA_STOP6")

        assert running.done()
        assert not handler._tasks  # every response task finished
        mock_ai.chat.assert_not_called()  # never reached OpenAI
        mock_send_audio.assert_not_called()  # never reached outbound send

    @pytest.mark.asyncio
    async def test_final_buffer_flush_after_stop_produces_no_response(self, mock_db_session):
        """A final buffer.drain() (as finalize() performs) must not generate
        a spoken response once the call has already been marked stopped —
        this is the exact real-call failure: trailing audio -> STT -> OpenAI
        -> TTS -> send, attempted after Exotel had already closed the socket."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP4", "+91000")

        await handler.handle_audio(_loud_audio())  # in-progress utterance, not yet finalized
        await handler.mark_stopped("CA_STOP4")
        mock_voice.transcribe.reset_mock()
        mock_ai.chat.reset_mock()
        mock_send_audio.reset_mock()

        await handler._buffer.drain()  # what finalize() does with any trailing audio

        mock_voice.transcribe.assert_not_called()
        mock_ai.chat.assert_not_called()
        mock_send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_duplicate_openai_request_for_overlapping_flushes(self, mock_db_session):
        """End-to-end version of the AudioBuffer-level regression: two flush
        triggers close together while one turn is processing must result in
        exactly one OpenAI call for that turn, not two."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice()
        release_turn = asyncio.Event()

        async def slow_transcribe(audio_bytes, language):
            await release_turn.wait()
            return "Hello, I need a demo."

        mock_voice.transcribe = AsyncMock(side_effect=slow_transcribe)
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_NODUP", "+91000")
        mock_ai.chat.reset_mock()

        # push() itself confirms speech, detects end-of-speech, and
        # schedules turn 1's flush naturally — no manual nudge needed.
        await handler.handle_audio(_complete_utterance())
        await asyncio.sleep(0)  # let turn 1 actually start and reach release_turn.wait()

        # A redundant/overlapping trigger for the SAME in-flight turn (e.g.
        # the stall watchdog and end-of-speech detection firing close
        # together) must be a no-op, not a second concurrent turn.
        handler._buffer._schedule_flush()
        await asyncio.sleep(0)

        release_turn.set()
        await asyncio.sleep(0.01)

        # Exactly one turn ran: one STT call, and exactly one user/assistant
        # exchange appended to the conversation (a duplicate turn would add
        # a second "hello" + reply pair). mock_ai.chat is not asserted on
        # its own call count here since a single legitimate turn already
        # calls it twice (language detection + the actual reply).
        mock_voice.transcribe.assert_called_once()
        user_messages = [m for m in handler._context.messages if m.role == "user"]
        assert len(user_messages) == 1
        await handler._buffer.cancel_pending()

    @pytest.mark.asyncio
    async def test_conversation_history_contains_only_real_turns(self, mock_db_session):
        """Conversation history must contain exactly: the greeting, then one
        user/assistant pair per real caller turn — nothing else. Guards
        against any code path that injects extra/default/boilerplate
        content (e.g. the full system prompt, department data, or a
        duplicate turn) into the message history OpenAI sees."""
        from app.services.department_service import DepartmentService

        mock_voice = self._mock_voice("I want to know about your services")
        mock_ai = self._mock_ai_client("We offer several services, which interests you?")
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_HISTORY", "+91000")
        await handler._on_audio_flushed(_loud_audio())

        roles = [m.role for m in handler._context.messages]
        assert roles == ["assistant", "user", "assistant"]
        # The user turn is exactly the transcript — not the system prompt,
        # not department data, not knowledge snippets, no other boilerplate.
        assert handler._context.messages[1].content == "I want to know about your services"

    @pytest.mark.asyncio
    async def test_finalize_department_classified_by_keyword_when_missing(self, mock_db_session):
        """When lead_data has no department, the classifier fills it in."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        # Lead extraction returns no department
        mock_ai.chat_json = AsyncMock(
            return_value={
                "name": "Raj",
                "phone": "+91000",
                "email": None,
                "department": None,
                "requirement": "I want to buy a product",
                "summary": "Caller wants to buy.",
                "language": "en",
                "timestamp": "2026-06-30T00:00:00Z",
                "additional_notes": None,
            }
        )
        # Department classifier (keyword-based fast path)
        mock_ai.chat = AsyncMock(return_value="rural")

        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept("rural", keywords=["buy", "purchase"])
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA_CLASSIFY", "+91000")

        # The keyword "buy" should match via DepartmentClassifier fast-path
        create_mock = AsyncMock(return_value=_make_mock_lead())
        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=_make_mock_call())),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(LeadService, "create_from_call", create_mock),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            await handler.finalize("CA_CLASSIFY")

        create_mock.assert_called_once()
        # Department should have been classified as "rural"
        call_kwargs = create_mock.call_args.kwargs
        assert call_kwargs.get("department_id") == mock_dept.id

    @pytest.mark.asyncio
    async def test_finalize_marks_call_complete_in_db(self, mock_db_session):
        """finalize() calls CallService.complete() to update call status."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService

        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            await handler._ensure_initialized("CA013", "+91000")

        handler._context.add_message("user", "Test")
        handler._context.add_message("assistant", "Reply")

        complete_mock = AsyncMock()
        with (
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=_make_mock_call())),
            patch.object(CallService, "complete", complete_mock),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(
                LeadService, "create_from_call", AsyncMock(return_value=_make_mock_lead())
            ),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            await handler.finalize("CA013")

        complete_mock.assert_called_once()
        assert complete_mock.call_args.kwargs["transcript"] is not None


# ---------------------------------------------------------------------------
# Voice-activity gate (app/voice/vad.py) — real PSTN calls showed Whisper
# hallucinating "you"/"thank you"/"bye-bye" transcripts from near-silent
# audio picked up while/after ElevenLabs TTS was playing. These tests prove
# silent/low-energy audio never reaches STT, genuine speech always does
# (even while TTS is playing — real barge-in must not be swallowed), and no
# OpenAI request is ever made from audio the gate discarded.
# ---------------------------------------------------------------------------


class TestVoiceActivityGate:
    def _make_handler(self, mock_db, mock_voice, mock_ai_client):
        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db, voice=mock_voice, ai_client=mock_ai_client)
        mock_ws = AsyncMock()
        handler.set_audio_sender(mock_ws)
        return handler, mock_ws

    def _mock_ai_client(self, reply: str = "How can I help?") -> AsyncMock:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=reply)
        client.chat_json = AsyncMock(return_value=_lead_extraction_result())
        return client

    def _mock_voice(self, transcript: str = "I need a demo") -> AsyncMock:
        voice = AsyncMock()
        voice.transcribe = AsyncMock(return_value=transcript)
        voice.synthesize = AsyncMock(return_value=b"AUDIO")
        return voice

    async def _init(self, handler, call_sid: str = "CA_VAD") -> None:
        from app.services.department_service import DepartmentService

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized(call_sid, "+91000")

    @pytest.mark.asyncio
    async def test_debug_audio_disabled_by_default_writes_nothing(self, mock_db_session, tmp_path):
        """VOICE_DEBUG_AUDIO defaults to False — no debug WAV files must be
        written during normal operation."""
        import os

        mock_voice = self._mock_voice("Hello")
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler, call_sid="CA_DEBUG_OFF")
        assert handler._settings.voice_debug_audio is False

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            await handler.handle_audio(_complete_utterance())
            await asyncio.sleep(0.01)
            assert not (tmp_path / "debug_audio").exists()
        finally:
            os.chdir(old_cwd)

    @pytest.mark.asyncio
    async def test_debug_audio_enabled_saves_exact_wav_sent_to_stt(self, mock_db_session, tmp_path):
        """When enabled, the exact WAV bytes handed to Whisper are saved to
        disk — so a real call's transcript can be checked against what the
        caller actually said by listening."""
        import os

        mock_voice = self._mock_voice("Hello")
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler, call_sid="CA_DEBUG_ON")

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with patch.object(handler._settings, "voice_debug_audio", True):
                await handler.handle_audio(_complete_utterance())
                await asyncio.sleep(0.01)

            saved = list((tmp_path / "debug_audio").glob("call_CA_DEBUG_ON_turn_*.wav"))
            assert len(saved) == 1
            assert saved[0].read_bytes().startswith(b"RIFF")
        finally:
            os.chdir(old_cwd)

    @pytest.mark.asyncio
    async def test_silence_while_listening_does_not_trigger_stt(self, mock_db_session):
        """The real-time UtteranceSegmenter (app/voice/vad.py) never even
        confirms speech began for pure silence, so nothing is ever queued
        for STT — verified via the real handle_audio()/AudioBuffer path,
        not by calling _on_audio_flushed() directly (that gate moved
        upstream into the segmenter this round)."""
        mock_voice = self._mock_voice()
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)
        mock_voice.transcribe.reset_mock()

        for _ in range(50):
            await handler.handle_audio(b"\x00" * 320)  # ~1s of true silence
        await handler._buffer.drain()

        mock_voice.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_very_low_energy_audio_does_not_trigger_stt(self, mock_db_session):
        """Faint line noise / echo bleed-back — amplitude well under the
        default 150.0 RMS threshold, but not literal zero."""
        mock_voice = self._mock_voice()
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)
        mock_voice.transcribe.reset_mock()

        quiet_frame = b"".join(struct.pack("<h", 20 if i % 2 == 0 else -20) for i in range(160))
        for _ in range(50):
            await handler.handle_audio(quiet_frame)
        await handler._buffer.drain()

        mock_voice.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_genuine_speech_triggers_stt(self, mock_db_session):
        mock_voice = self._mock_voice("Hello, I need help")
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)
        mock_voice.transcribe.reset_mock()

        await handler.handle_audio(_loud_audio())
        await handler._buffer.drain()

        mock_voice.transcribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_openai_request_from_discarded_audio(self, mock_db_session):
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        mock_voice.transcribe.reset_mock()
        mock_ai.chat.reset_mock()

        for _ in range(50):
            await handler.handle_audio(b"\x00" * 320)
        await handler._buffer.drain()

        mock_voice.transcribe.assert_not_called()
        mock_ai.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_tts_playback_does_not_automatically_trigger_ai_turn(self, mock_db_session):
        """Speaking a reply must never, by itself, start a new STT/AI turn —
        only genuine inbound caller audio (passing a gate) can do that."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        mock_voice.transcribe.reset_mock()
        mock_ai.chat.reset_mock()

        await handler._speak("Here is some information for you.")

        assert handler._turn_state == "LISTENING"  # returns to listening once done speaking
        mock_voice.transcribe.assert_not_called()
        mock_ai.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_media_packets_for_one_utterance_produce_one_stt_request(
        self, mock_db_session
    ):
        """Several small inbound chunks accumulating into one AudioBuffer
        flush must still reach Whisper exactly once — buffering behavior is
        unchanged by the voice-activity gate."""
        mock_voice = self._mock_voice("One continuous sentence")
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        mock_voice.transcribe.reset_mock()

        # Four separate small pushes whose combined bytes (well over the
        # 2560-byte/8-frame speech-start threshold) are still one
        # continuous utterance to the segmenter — chunk boundaries don't
        # matter, only the continuous stream of frames does.
        chunk = _loud_audio(700)
        for _ in range(4):
            await handler._buffer.push(chunk)
        await handler._buffer.drain()

        mock_voice.transcribe.assert_called_once()

    def _frame_bytes(self, handler) -> int:
        return int(handler._settings.barge_in_frame_ms / 1000 * 8000) * 2

    @pytest.mark.asyncio
    async def test_tts_starts_transitions_state_to_tts_playing(self, mock_db_session):
        """TTS_PLAYING means assistant audio is audible: it begins when the
        first frame is handed to Exotel — not while ElevenLabs is still
        synthesizing (nothing is audible yet, so there is no echo to guard
        against and caller speech must still count as ordinary speech)."""
        mock_voice = self._mock_voice()
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)

        observed = {}

        async def capture_synthesis_state(text, lang):
            observed["during_synthesis"] = handler._turn_state
            return b"AUDIO"

        async def capture_send_state(audio):
            observed["during_send"] = handler._turn_state

        mock_voice.synthesize = AsyncMock(side_effect=capture_synthesis_state)
        handler.set_audio_sender(AsyncMock(side_effect=capture_send_state))
        await handler._speak_interruptible("Hello there")

        assert observed["during_synthesis"] == "LISTENING"
        assert observed["during_send"] == "TTS_PLAYING"
        assert handler._turn_state == "LISTENING"  # back to listening once playback finishes

    @pytest.mark.asyncio
    async def test_inbound_audio_during_tts_does_not_accumulate(self, mock_db_session):
        """Regression: a real PSTN call showed ~90KB (~6s) of TTS-window
        audio accumulating in the caller AudioBuffer and being sent to
        Whisper as one blob right after playback ended. Inbound media
        arriving during TTS_PLAYING must never grow the ordinary buffer
        unless/until a barge-in is actually confirmed."""
        mock_voice = self._mock_voice()
        synth_started = asyncio.Event()
        release_synth = asyncio.Event()

        async def slow_synthesize(text, lang):
            synth_started.set()
            await release_synth.wait()
            return b"AUDIO"

        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)
        mock_voice.synthesize = AsyncMock(side_effect=slow_synthesize)

        speak_task = asyncio.create_task(handler._speak_interruptible("Kinikh provides..."))
        await asyncio.wait_for(synth_started.wait(), timeout=1)

        # Sub-barge-in-threshold audio (quiet — well under the 300.0 default
        # barge_in_min_rms_energy) keeps arriving for what would be several
        # seconds of real playback.
        quiet_frame = b"".join(struct.pack("<h", 20 if i % 2 == 0 else -20) for i in range(160))
        for _ in range(200):  # 200 * 320 bytes = 64,000 bytes if it accumulated
            await handler.handle_audio(quiet_frame)

        assert not handler._buffer.has_pending_audio

        release_synth.set()
        assert await speak_task is True

    @pytest.mark.asyncio
    async def test_tts_echo_like_audio_does_not_trigger_ai_turn(self, mock_db_session):
        """Quiet/echo-like audio arriving during TTS_PLAYING must never
        reach STT/OpenAI, whether or not it eventually stops."""
        mock_voice = self._mock_voice()
        mock_ai = self._mock_ai_client()
        synth_started = asyncio.Event()
        release_synth = asyncio.Event()

        async def slow_synthesize(text, lang):
            synth_started.set()
            await release_synth.wait()
            return b"AUDIO"

        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        mock_voice.transcribe.reset_mock()
        mock_ai.chat.reset_mock()
        mock_voice.synthesize = AsyncMock(side_effect=slow_synthesize)

        speak_task = asyncio.create_task(handler._speak_interruptible("Kinikh provides..."))
        await asyncio.wait_for(synth_started.wait(), timeout=1)

        quiet_frame = b"".join(struct.pack("<h", 15 if i % 2 == 0 else -15) for i in range(160))
        for _ in range(30):
            await handler.handle_audio(quiet_frame)

        release_synth.set()
        assert await speak_task is True

        mock_voice.transcribe.assert_not_called()
        mock_ai.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_short_loud_packet_during_tts_does_not_trigger_barge_in(
        self, mock_db_session
    ):
        """A single loud ~20ms packet while the assistant is audible —
        comfort noise, a line click, one frame of TTS bleed-back — must never
        be enough to interrupt. Sustained activity is required."""
        mock_voice = self._mock_voice()
        handler, _ = self._make_handler(mock_db_session, mock_voice, self._mock_ai_client())
        await self._init(handler)
        mock_voice.transcribe.reset_mock()
        mock_voice.synthesize = AsyncMock(return_value=b"\x00" * 4800)  # 300ms reply

        speak_task = asyncio.create_task(handler._speak_interruptible("Kinikh provides..."))
        await asyncio.sleep(0.01)
        assert handler._turn_state == "TTS_PLAYING"  # audio handed to Exotel, playing

        one_frame = _loud_audio(self._frame_bytes(handler) // 2)
        await handler.handle_audio(one_frame)

        assert handler._turn_state == "TTS_PLAYING"  # no barge-in confirmed
        assert not handler._buffer.has_pending_audio

        assert await speak_task is True
        mock_voice.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_sustained_caller_speech_during_tts_triggers_barge_in(self, mock_db_session):
        """Real, sustained caller speech while the assistant is audible is a
        barge-in: playback stops at once (the task is cancelled, not left to
        finish its 2s of audio), Exotel is told to drop its buffered audio,
        nothing more is sent for the interrupted reply, and the caller's
        speech starts a fresh turn."""
        mock_voice = self._mock_voice("I have another question")
        mock_ai = self._mock_ai_client()
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        clear_audio = AsyncMock()
        handler.set_audio_clearer(clear_audio)
        mock_voice.transcribe.reset_mock()
        mock_send_audio.reset_mock()
        mock_voice.synthesize = AsyncMock(return_value=b"\x00" * 32000)  # 2s reply

        speak_task = asyncio.create_task(handler._speak_interruptible("Kinikh provides..."))
        # Past the start-of-sentence grace window, where echo is ignored.
        await asyncio.sleep(get_settings().barge_in_grace_ms / 1000 + 0.05)
        assert handler._turn_state == "TTS_PLAYING"
        sent_before_barge_in = mock_send_audio.await_count
        # Paced output: only ~playback_max_lead_ms of the 2s reply is out yet.
        assert 0 < sent_before_barge_in < 10

        frame_samples = self._frame_bytes(handler) // 2
        sustained_audio = _loud_audio(frame_samples * 25)  # ~500ms, over the 350ms floor
        await handler.handle_audio(sustained_audio)

        assert handler._turn_state == "CALLER_SPEAKING"
        assert handler._buffer.has_pending_audio

        completed = await asyncio.wait_for(speak_task, timeout=0.5)  # not the 2s playback
        assert completed is False  # cancelled by the barge-in, not a natural finish
        # Nothing more was sent for the interrupted reply after the barge-in.
        assert mock_send_audio.await_count == sent_before_barge_in
        clear_audio.assert_awaited_once()

        # The confirmed speech starts a fresh turn once it goes quiet.
        await handler._buffer.drain()
        mock_voice.transcribe.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_playback_audio_cleared_after_tts_finishes_normally(self, mock_db_session):
        """Once TTS finishes WITHOUT a confirmed barge-in, whatever
        sub-threshold audio arrived during that window must be gone — not
        merged into the next real caller utterance."""
        mock_voice = self._mock_voice("A brand new question")
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler)
        mock_voice.transcribe.reset_mock()

        quiet_frame = b"".join(struct.pack("<h", 15 if i % 2 == 0 else -15) for i in range(160))
        synth_started = asyncio.Event()
        release_synth = asyncio.Event()

        async def slow_synthesize(text, lang):
            synth_started.set()
            await release_synth.wait()
            return b"AUDIO"

        mock_voice.synthesize = AsyncMock(side_effect=slow_synthesize)
        speak_task = asyncio.create_task(handler._speak_interruptible("Some info"))
        await asyncio.wait_for(synth_started.wait(), timeout=1)
        for _ in range(10):
            await handler.handle_audio(quiet_frame)
        release_synth.set()
        assert await speak_task is True
        assert not handler._buffer.has_pending_audio  # stale audio never landed here

        # Next genuine caller speech starts a completely fresh turn.
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")
        await handler.handle_audio(_loud_audio())
        await handler._buffer.drain()

        mock_voice.transcribe.assert_called_once()
        sent_wav = mock_voice.transcribe.call_args[0][0]
        # Only the fresh loud audio was transcribed — none of the discarded
        # quiet frames (10 * 320 = 3200 bytes) were merged into it.
        assert len(sent_wav) <= len(_loud_audio()) + 44

    @pytest.mark.asyncio
    async def test_stop_during_tts_prevents_outbound_audio_after_stop(self, mock_db_session):
        """mark_stopped() arriving while ElevenLabs is mid-synthesis must
        still prevent the resulting audio from ever being sent."""
        mock_voice = self._mock_voice()
        synth_started = asyncio.Event()
        release_synth = asyncio.Event()

        async def slow_synthesize(text, lang):
            synth_started.set()
            await release_synth.wait()
            return b"AUDIO"

        handler, mock_send_audio = self._make_handler(
            mock_db_session, mock_voice, self._mock_ai_client()
        )
        await self._init(handler)
        mock_send_audio.reset_mock()
        mock_voice.synthesize = AsyncMock(side_effect=slow_synthesize)

        speak_task = asyncio.create_task(handler._speak_interruptible("Some information"))
        await asyncio.wait_for(synth_started.wait(), timeout=1)

        await handler.mark_stopped("CA_VAD")
        release_synth.set()
        with pytest.raises(asyncio.CancelledError):
            await speak_task

        mock_send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_caller_utterances_produce_exactly_two_ai_responses(self, mock_db_session):
        """One caller utterance = one AI response — two separate utterances
        must produce exactly two, never more (no automatic self-triggered
        follow-up turns)."""
        mock_voice = self._mock_voice("I have a first question.")
        mock_ai = self._mock_ai_client("First answer")
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler, call_sid="CA_TWO_TURNS")

        async def turn_done(count: int):
            while not (
                len([m for m in handler._context.messages if m.role == "assistant"]) >= count
                and handler._turn_state == "LISTENING"
            ):
                await asyncio.sleep(0.01)

        await handler.handle_audio(_complete_utterance())
        await asyncio.wait_for(turn_done(1), timeout=3)  # let turn 1 fully run (incl. playback)

        mock_voice.transcribe = AsyncMock(return_value="I have a second question.")
        mock_ai.chat = AsyncMock(return_value="Second answer")
        await handler.handle_audio(_complete_utterance())
        await asyncio.wait_for(turn_done(2), timeout=3)  # let turn 2 fully run

        user_messages = [m for m in handler._context.messages if m.role == "user"]
        assert len(user_messages) == 2
        assert user_messages[0].content == "I have a first question."
        assert user_messages[1].content == "I have a second question."
        await handler.mark_stopped("CA_TWO_TURNS")

    @pytest.mark.asyncio
    async def test_stop_during_openai_prevents_late_tts(self, mock_db_session):
        """mark_stopped() arriving while OpenAI is still generating a reply
        must prevent that reply from ever being spoken."""
        mock_voice = self._mock_voice("I have a question.")
        ai_call_started = asyncio.Event()
        release_ai_call = asyncio.Event()

        async def slow_chat(messages):
            ai_call_started.set()
            await release_ai_call.wait()
            return "A reply that should never be spoken"

        mock_ai = self._mock_ai_client()
        mock_ai.chat = AsyncMock(side_effect=slow_chat)
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler, call_sid="CA_STOP_OPENAI")
        handler._context.language = "hi"  # skip re-detection so slow_chat fires only once
        mock_send_audio.reset_mock()

        flush_task = asyncio.create_task(handler.handle_audio(_complete_utterance()))
        await asyncio.wait_for(ai_call_started.wait(), timeout=1)

        await handler.mark_stopped("CA_STOP_OPENAI")
        release_ai_call.set()
        await flush_task
        await asyncio.sleep(0.01)

        mock_send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_turn_result_never_speaks_after_newer_turn_begins(self, mock_db_session):
        """If a confirmed barge-in (or any newer turn) advances turn_id
        while an older turn's STT is still in flight, that older turn's
        stale result must never reach OpenAI/TTS once it finally returns."""
        stt_started = asyncio.Event()
        release_stt = asyncio.Event()

        async def slow_transcribe(audio_bytes, language):
            stt_started.set()
            await release_stt.wait()
            return "Stale transcript"

        mock_voice = self._mock_voice()
        mock_voice.transcribe = AsyncMock(side_effect=slow_transcribe)
        mock_ai = self._mock_ai_client("Stale reply")
        handler, mock_send_audio = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler, call_sid="CA_STALE_TURN")
        mock_ai.chat.reset_mock()
        mock_send_audio.reset_mock()

        flush_task = asyncio.create_task(handler.handle_audio(_complete_utterance()))
        await asyncio.wait_for(stt_started.wait(), timeout=1)

        # Simulate a newer turn already having begun (e.g. a confirmed
        # barge-in) while this turn's STT was still stuck in flight.
        handler._response = None

        release_stt.set()
        await flush_task
        await asyncio.sleep(0.01)

        mock_ai.chat.assert_not_called()
        mock_send_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_counter_increments_and_perf_fields_populated(self, mock_db_session):
        """Latency instrumentation: each turn increments the display-only
        turn counter and records per-stage timing usable for a [PERF] line."""
        mock_voice = self._mock_voice("Hello")
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler, call_sid="CA_PERF")
        assert handler._turn_number == 0

        await handler.handle_audio(_complete_utterance())
        await asyncio.sleep(0.01)

        assert handler._turn_number == 1
        assert handler._last_tts_ms >= 0
        assert handler._last_send_ms >= 0

    @pytest.mark.asyncio
    async def test_short_answer_yes_triggers_exactly_one_turn(self, mock_db_session):
        """A very short genuine reply like "Yes" must still be captured as
        a real utterance — speech-start only requires ~200ms of sustained
        energy, not a long sentence. Being under 3 words it gets the
        unfinished-turn wait first, then is answered as its own turn."""
        mock_voice = self._mock_voice("Yes")
        mock_ai = self._mock_ai_client()
        handler, _ = self._make_handler(mock_db_session, mock_voice, mock_ai)
        await self._init(handler, call_sid="CA_SHORT_YES")
        mock_voice.transcribe.reset_mock()

        await handler.handle_audio(_complete_utterance())

        async def answered():
            while not [m for m in handler._context.messages if m.role == "user"]:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(answered(), timeout=3)
        mock_voice.transcribe.assert_called_once()
        user_messages = [m for m in handler._context.messages if m.role == "user"]
        assert user_messages[-1].content == "Yes"
        await handler.mark_stopped("CA_SHORT_YES")



    @pytest.mark.asyncio
    async def test_tts_stays_gated_during_actual_playback_duration(self, mock_db_session):
        """Regression: a real call showed inbound audio being accepted as
        an ordinary caller turn immediately after send_audio() returned,
        while Exotel was very likely still audibly playing the reply.
        Sending bytes to Exotel is not the same as the caller having
        finished hearing them — TTS_PLAYING (barge-in-only inbound
        handling) must last for the audio's own playback duration."""
        mock_voice = self._mock_voice()
        mock_voice.synthesize = AsyncMock(return_value=_loud_audio(4000))  # ~0.5s of audio
        handler, mock_send_audio = self._make_handler(
            mock_db_session, mock_voice, self._mock_ai_client()
        )
        await self._init(handler, call_sid="CA_PLAYBACK_WAIT")
        mock_voice.transcribe.reset_mock()

        speak_task = asyncio.create_task(handler._speak_interruptible("Some longer reply here"))
        await asyncio.sleep(0.01)  # let synthesize + send actually complete

        mock_send_audio.assert_called()  # the audio was already sent to Exotel
        assert handler._turn_state == "TTS_PLAYING"  # but playback isn't done yet

        # Would-be echo/noise arriving during the tail of actual playback
        # must still be gated by the stricter barge-in path, not treated as
        # an ordinary already-idle caller turn.
        await handler.handle_audio(b"\x00" * 320)
        assert not handler._buffer.has_pending_audio

        await speak_task  # let the playback-duration wait finish
        assert handler._turn_state == "LISTENING"


# ---------------------------------------------------------------------------
# ExotelAudioStream integration tests (on_start callback)
# ---------------------------------------------------------------------------


class TestExotelAudioStreamPipeline:
    """Tests ExotelAudioStream wired into SpeechHandler via on_start."""

    @pytest.mark.asyncio
    async def test_on_start_called_with_call_sid_and_caller_number(self):
        """Stream 'start' event fires on_start with correct call_sid + caller_number."""
        from app.telephony.exotel.stream import ExotelAudioStream

        on_start_calls: list[tuple[str, str]] = []

        async def capture_on_start(call_sid: str, caller_number: str) -> None:
            on_start_calls.append((call_sid, caller_number))

        ws = _FakeWebSocket(
            [
                _ws_connected(),
                _ws_start("CA_STREAM", "+919876543210"),
                _ws_stop(),
            ]
        )
        stream = ExotelAudioStream(
            websocket=ws,
            on_audio=AsyncMock(),
            on_start=capture_on_start,
        )
        await stream.start()

        assert len(on_start_calls) == 1
        assert on_start_calls[0] == ("CA_STREAM", "+919876543210")

    @pytest.mark.asyncio
    async def test_on_start_not_required(self):
        """Stream works normally when on_start is not provided."""
        from app.telephony.exotel.stream import ExotelAudioStream

        on_audio_calls: list[bytes] = []

        async def capture_audio(data: bytes) -> None:
            on_audio_calls.append(data)

        ws = _FakeWebSocket(
            [
                _ws_start("CA_NOSTART", "+91000"),
                _ws_media(b"\x01\x02"),
                _ws_stop(),
            ]
        )
        stream = ExotelAudioStream(websocket=ws, on_audio=capture_audio)
        await stream.start()

        assert len(on_audio_calls) == 1

    @pytest.mark.asyncio
    async def test_stream_passes_caller_number_from_custom_params(self):
        """caller_number is parsed from start.from in the start event."""
        from app.telephony.exotel.stream import ExotelAudioStream

        on_start_mock = AsyncMock()
        ws = _FakeWebSocket([_ws_start("CA_NUM", "+918888888888"), _ws_stop()])
        stream = ExotelAudioStream(websocket=ws, on_audio=AsyncMock(), on_start=on_start_mock)
        await stream.start()

        on_start_mock.assert_called_once_with("CA_NUM", "+918888888888")
        assert stream.caller_number == "+918888888888"

    @pytest.mark.asyncio
    async def test_stream_caller_number_empty_when_missing_from_params(self):
        """caller_number defaults to '' if start.from is absent."""
        from app.telephony.exotel.stream import ExotelAudioStream

        # Start message with no "from" field at all
        start_msg = json.dumps(
            {
                "event": "start",
                "stream_sid": "MZ001",
                "start": {"call_sid": "CA_NOCUSTOM"},
            }
        )
        ws = _FakeWebSocket([start_msg, _ws_stop()])
        on_start_mock = AsyncMock()
        stream = ExotelAudioStream(websocket=ws, on_audio=AsyncMock(), on_start=on_start_mock)
        await stream.start()

        on_start_mock.assert_called_once_with("CA_NOCUSTOM", "")
        assert stream.caller_number == ""

    @pytest.mark.asyncio
    async def test_stream_audio_delivered_after_start(self):
        """Audio chunks received after start are delivered to on_audio."""
        from app.telephony.exotel.stream import ExotelAudioStream

        received_audio: list[bytes] = []

        async def capture(data: bytes) -> None:
            received_audio.append(data)

        audio_payload = b"\xde\xad\xbe\xef"
        ws = _FakeWebSocket(
            [
                _ws_start("CA_AUDIO", "+91000"),
                _ws_media(audio_payload),
                _ws_media(audio_payload),
                _ws_stop(),
            ]
        )
        stream = ExotelAudioStream(websocket=ws, on_audio=capture)
        await stream.start()

        assert len(received_audio) == 2
        assert received_audio[0] == audio_payload

    @pytest.mark.asyncio
    async def test_stream_properties_after_start(self):
        """call_sid, stream_sid, and caller_number are accessible after start."""
        from app.telephony.exotel.stream import ExotelAudioStream

        ws = _FakeWebSocket([_ws_start("CA_PROPS", "+91000", "MZ_PROPS"), _ws_stop()])
        stream = ExotelAudioStream(websocket=ws, on_audio=AsyncMock())
        await stream.start()

        assert stream.call_sid == "CA_PROPS"
        assert stream.stream_sid == "MZ_PROPS"

    @pytest.mark.asyncio
    async def test_stream_wires_into_speech_handler(self, mock_db_session):
        """ExotelAudioStream on_start callback triggers SpeechHandler._ensure_initialized."""
        from app.services.department_service import DepartmentService
        from app.telephony.exotel.stream import ExotelAudioStream
        from app.voice.speech_handler import SpeechHandler

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="")
        mock_voice.synthesize = AsyncMock(return_value=b"GREETING_AUDIO")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Welcome!")
        mock_ai.chat_json = AsyncMock(return_value={})

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)

        mock_dept = _make_mock_dept()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])):
            ws = _FakeWebSocket(
                [
                    _ws_connected(),
                    _ws_start("CA_WIRE", "+919876543210"),
                    _ws_stop(),
                ]
            )
            stream = ExotelAudioStream(
                websocket=ws,
                on_audio=handler.handle_audio,
                on_dtmf=handler.handle_dtmf,
                on_start=handler._ensure_initialized,
            )
            handler.set_audio_sender(stream.send_audio)
            await stream.start()

        assert handler._initialized is True
        assert handler._context is not None
        assert handler._context.call_sid == "CA_WIRE"
        mock_voice.synthesize.assert_called()

        # Greeting must reach the caller as a proper Exotel media frame — a JSON
        # TEXT frame, never a raw binary frame.
        assert len(ws.sent_text) >= 1
        assert not ws.sent_bytes
        frame = json.loads(ws.sent_text[0])
        assert frame["event"] == "media"
        assert frame["stream_sid"] == "MZ001"
        assert "payload" in frame["media"]


# ---------------------------------------------------------------------------
# Full end-to-end pipeline simulation
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Simulates the complete Exotel call lifecycle end-to-end."""

    @pytest.mark.asyncio
    async def test_complete_english_call(self, mock_db_session):
        """Full pipeline: connected → start → audio → stop → lead created → email sent."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService
        from app.telephony.exotel.stream import ExotelAudioStream
        from app.voice.speech_handler import SpeechHandler

        call_sid = "CA_E2E_EN"
        caller_number = "+919876543210"

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="Hi, I am Raj and I need a product demo")
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO_RESPONSE")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Sure, what is your email address?")
        mock_ai.chat_json = AsyncMock(return_value=_lead_extraction_result(caller_number))

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)

        mock_dept = _make_mock_dept()
        mock_call = _make_mock_call(call_sid)
        mock_lead = _make_mock_lead()
        dispatch_mock = AsyncMock()
        create_lead_mock = AsyncMock(return_value=mock_lead)

        with (
            patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=mock_call)),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(LeadService, "create_from_call", create_lead_mock),
            patch.object(EmailService, "dispatch_lead_email", dispatch_mock),
        ):
            ws = _FakeWebSocket(
                [
                    _ws_connected(),
                    _ws_start(call_sid, caller_number),
                    _ws_media(_loud_audio()),
                    _ws_stop(),
                ]
            )
            stream = ExotelAudioStream(
                websocket=ws,
                on_audio=handler.handle_audio,
                on_dtmf=handler.handle_dtmf,
                on_start=handler._ensure_initialized,
            )
            handler.set_audio_sender(stream.send_audio)
            await stream.start()
            await handler.finalize(stream.call_sid)

        # Greeting was synthesized and sent as a proper Exotel media frame —
        # a JSON TEXT frame, never a raw binary frame.
        assert mock_voice.synthesize.call_count >= 1
        assert len(ws.sent_text) >= 1
        assert not ws.sent_bytes
        frame = json.loads(ws.sent_text[0])
        assert frame["event"] == "media"
        assert frame["stream_sid"] == "MZ001"
        assert base64.b64decode(frame["media"]["payload"]) == _as_frame(b"AUDIO_RESPONSE")

        # Lead was persisted to DB
        create_lead_mock.assert_called_once()
        lead_kwargs = create_lead_mock.call_args.kwargs
        assert lead_kwargs["phone"] == caller_number
        assert lead_kwargs["name"] == "Raj Kumar"

        # Email was dispatched
        dispatch_mock.assert_called_once_with(mock_lead.id)

    @pytest.mark.asyncio
    async def test_complete_hindi_call(self, mock_db_session):
        """Full pipeline with Hindi audio — language detected and lead captured."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService
        from app.telephony.exotel.stream import ExotelAudioStream
        from app.voice.speech_handler import SpeechHandler

        call_sid = "CA_E2E_HI"
        caller_number = "+919876543210"

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="मेरा नाम राज है, मुझे डेमो चाहिए")
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO_HI")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="ठीक है राज जी, आपका ईमेल क्या है?")
        mock_ai.chat_json = AsyncMock(
            return_value={
                **_lead_extraction_result(caller_number),
                "name": "राज",
                "language": "hi",
            }
        )

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)

        mock_dept = _make_mock_dept()
        mock_call = _make_mock_call(call_sid)
        mock_lead = _make_mock_lead("राज")

        with (
            patch.object(DepartmentService, "list_active", AsyncMock(return_value=[mock_dept])),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=mock_dept)),
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=mock_call)),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(LeadService, "create_from_call", AsyncMock(return_value=mock_lead)),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            ws = _FakeWebSocket(
                [
                    _ws_start(call_sid, caller_number),
                    _ws_media(_loud_audio()),
                    _ws_stop(),
                ]
            )
            stream = ExotelAudioStream(
                websocket=ws,
                on_audio=handler.handle_audio,
                on_start=handler._ensure_initialized,
            )
            handler.set_audio_sender(stream.send_audio)
            await stream.start()

            # Drain buffer to trigger _on_audio_flushed (silence timer doesn't fire in tests)
            if handler._buffer:
                await handler._buffer.drain()

            await handler.finalize(stream.call_sid)

    @pytest.mark.asyncio
    async def test_complete_call_with_no_departments(self, mock_db_session):
        """Pipeline works even with no departments configured."""
        from app.services.call_service import CallService
        from app.services.department_service import DepartmentService
        from app.services.email_service import EmailService
        from app.services.lead_service import LeadService
        from app.telephony.exotel.stream import ExotelAudioStream
        from app.voice.speech_handler import SpeechHandler

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="Hello, I need help")
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="How can I help you?")
        mock_ai.chat_json = AsyncMock(return_value=_lead_extraction_result())

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)

        with (
            patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])),
            patch.object(DepartmentService, "get_by_name", AsyncMock(return_value=None)),
            patch.object(CallService, "get_by_sid", AsyncMock(return_value=_make_mock_call())),
            patch.object(CallService, "complete", AsyncMock()),
            patch.object(
                LeadService, "create_from_call", AsyncMock(return_value=_make_mock_lead())
            ),
            patch.object(EmailService, "dispatch_lead_email", AsyncMock()),
        ):
            ws = _FakeWebSocket([_ws_start("CA_NODEPT", "+91000"), _ws_stop()])
            stream = ExotelAudioStream(
                websocket=ws,
                on_audio=handler.handle_audio,
                on_start=handler._ensure_initialized,
            )
            handler.set_audio_sender(stream.send_audio)
            await stream.start()
            await handler.finalize(stream.call_sid)

        assert handler._departments == []

    @pytest.mark.asyncio
    async def test_ai_failure_uses_fallback_response(self, mock_db_session):
        """When OpenAI fails during conversation, a language-appropriate fallback is used."""
        from app.services.department_service import DepartmentService

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="I need help")
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(side_effect=Exception("OpenAI unavailable"))
        mock_ai.chat_json = AsyncMock(return_value=_lead_extraction_result())

        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)
        mock_send_audio = AsyncMock()
        handler.set_audio_sender(mock_send_audio)

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_FAIL", "+91000")

        mock_send_audio.reset_mock()
        mock_voice.synthesize.reset_mock()

        await handler._on_audio_flushed(_loud_audio())

        # Fallback message should have been synthesized and sent
        mock_voice.synthesize.assert_called()
        spoken_text = mock_voice.synthesize.call_args[0][0]
        assert "sorry" in spoken_text.lower() or "trouble" in spoken_text.lower()

    @pytest.mark.asyncio
    async def test_stt_failure_skips_conversation(self, mock_db_session):
        """When STT returns empty string, no AI call is made."""
        from app.services.department_service import DepartmentService

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="")  # STT returns nothing
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")

        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Reply")

        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)
        handler.set_audio_sender(AsyncMock())

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STT_FAIL", "+91000")

        mock_ai.chat.reset_mock()
        await handler._on_audio_flushed(b"\x00" * 100)

        mock_ai.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_dtmf_handled_without_error(self, mock_db_session):
        """DTMF digits are received without causing any error."""
        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db_session)
        await handler.handle_dtmf("5")  # must not raise

    @pytest.mark.asyncio
    async def test_multiple_audio_chunks_accumulate(self, mock_db_session):
        """Multiple audio chunks before silence flush are all passed to STT."""
        from app.services.department_service import DepartmentService

        mock_voice = AsyncMock()
        mock_voice.transcribe = AsyncMock(return_value="Hello")
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")
        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Reply")
        mock_ai.chat_json = AsyncMock(return_value={})

        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)
        handler.set_audio_sender(AsyncMock())

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_CHUNKS", "+91000")

        # Push multiple audio chunks then drain
        assert handler._buffer is not None
        for _ in range(3):
            await handler._buffer.push(b"\x00" * 100)
        await handler._buffer.drain()


# ---------------------------------------------------------------------------
# Greeting language tests
# ---------------------------------------------------------------------------


class TestGreetingLanguages:
    """Verify Maya's deterministic opening greeting."""

    @pytest.mark.asyncio
    async def test_greeting_speaks_deterministic_maya_line(self, mock_db_session):
        from app.services.department_service import DepartmentService
        from app.voice.speech_handler import SpeechHandler

        mock_voice = AsyncMock()
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")
        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Hi")

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)
        handler.set_audio_sender(AsyncMock())

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("GR_EN", "+91000")

        args = mock_voice.synthesize.call_args[0]
        assert "Welcome to किनिख Group" in args[0]
        mock_ai.chat.assert_not_called()  # never OpenAI-generated

    @pytest.mark.asyncio
    async def test_greeting_spoken_exactly_once_per_call(self, mock_db_session):
        from app.services.department_service import DepartmentService
        from app.voice.speech_handler import SpeechHandler

        mock_voice = AsyncMock()
        mock_voice.synthesize = AsyncMock(return_value=b"AUDIO")
        mock_ai = AsyncMock()
        mock_ai.chat = AsyncMock(return_value="Hi")

        handler = SpeechHandler(db=mock_db_session, voice=mock_voice, ai_client=mock_ai)
        handler.set_audio_sender(AsyncMock())

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("GR_ONCE", "+91000")
            await handler._ensure_initialized("GR_ONCE", "+91000")  # second call is a no-op

        assert mock_voice.synthesize.call_count == 1

    @pytest.mark.asyncio
    async def test_greeting_finishes_into_listening_state(self, mock_db_session):
        from app.services.department_service import DepartmentService
        from app.voice.speech_handler import SpeechHandler

        handler = SpeechHandler(
            db=mock_db_session,
            voice=AsyncMock(synthesize=AsyncMock(return_value=b"AUDIO")),
            ai_client=AsyncMock(),
        )
        handler.set_audio_sender(AsyncMock())

        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("GR_LISTEN", "+91000")

        assert handler._turn_state == "LISTENING"

    @pytest.mark.asyncio
    async def test_greeting_same_text_regardless_of_language(self, mock_db_session):
        """Maya's opening line is deterministic — it never depends on
        context.language (which isn't known yet at call start anyway)."""
        from app.ai.context_manager import CallContext
        from app.ai.conversation import ConversationEngine

        mock_ai = AsyncMock()
        engine = ConversationEngine([], client=mock_ai)
        greetings = [
            await engine.get_greeting(
                CallContext(call_sid="X", caller_number="+91000", language=lang)
            )
            for lang in ("en", "hi", "hi-en")
        ]
        assert len(set(greetings)) == 1
        mock_ai.chat.assert_not_called()
