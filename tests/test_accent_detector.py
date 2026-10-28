"""Optional speech-style (accent) detection: background-only, hysteresis,
neutral fallback, log-only safety, curated-voice routing, and independence
from language, RAG facts and routing.

All detectors here are ScriptedAccentDetector test doubles — these tests
prove the plumbing, NOT that any accent is recognised (no acoustic model
ships with the project).
"""

from __future__ import annotations

import asyncio
import re
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.language_profile import analyze_language, update_language_profile
from app.core.config import get_settings
from app.voice.accent_detector import (
    NEUTRAL,
    AccentResult,
    NeutralAccentDetector,
    ScriptedAccentDetector,
    SpeechStyleTracker,
    apply_accent_result,
    build_speech_style_tracker,
    register_accent_detector,
    resample_pcm16,
    voiced_pcm,
)

GUJ = "gujarati_influenced"
PUN = "punjabi_influenced"
RATE = 8000


def _speech(ms: int) -> bytes:
    """Loud caller 'speech' (PCM16 8 kHz)."""
    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(RATE * ms // 1000))


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (RATE * ms // 1000)


def _ctx(call_sid: str = "CA_ACCENT") -> CallContext:
    return CallContext(call_sid=call_sid, caller_number="+919876543210")


def _high(style: str, ms: int = 4500) -> AccentResult:
    return AccentResult(style, "high", ms)


@pytest.fixture
def accent_settings():
    """Feature on, log_only, real defaults otherwise; restored afterwards."""
    settings = get_settings()
    with (
        patch.object(settings, "accent_detection_enabled", True),
        patch.object(settings, "accent_detection_mode", "log_only"),
        patch.object(settings, "accent_detector_provider", "none"),
    ):
        yield settings


def _tracker(settings, ctx: CallContext, detector) -> tuple[SpeechStyleTracker, list]:
    tasks: list[asyncio.Task] = []
    tracker = build_speech_style_tracker(
        settings,
        get_context=lambda: ctx,
        spawn=lambda coro: tasks.append(asyncio.ensure_future(coro)),
        detector=detector,
    )
    assert tracker is not None
    return tracker, tasks


# ── Context (1–8) ───────────────────────────────────────────────────────────


def test_1_default_speech_style_is_neutral_indian(mock_db_session):
    from app.voice.speech_handler import SpeechHandler

    ctx = _ctx()
    assert ctx.speech_style == NEUTRAL
    assert ctx.speech_style_confidence == "low"
    assert ctx.speech_style_evidence_turns == 0 and not ctx.speech_style_locked
    # Feature flag off by default: no tracker at all.
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    assert handler._speech_style is None


@pytest.mark.asyncio
async def test_2_insufficient_audio_does_not_run_classification(accent_settings):
    detector = ScriptedAccentDetector([_high(GUJ)])
    ctx = _ctx()
    tracker, tasks = _tracker(accent_settings, ctx, detector)
    for _ in range(20):  # "haan" / "ji" / "hello": short utterances never count
        tracker.add_caller_audio(_speech(500), RATE)
    for _ in range(3):  # 3.6 s of real speech: still under 4 s
        tracker.add_caller_audio(_speech(1200), RATE)
    tracker.add_caller_audio(_silence(5000), RATE)  # silence is never speech
    assert tasks == [] and detector.calls == []


@pytest.mark.asyncio
async def test_3_detector_runs_once_after_enough_meaningful_speech(accent_settings):
    detector = ScriptedAccentDetector([AccentResult(NEUTRAL, "high", 4200)])
    ctx = _ctx()
    tracker, tasks = _tracker(accent_settings, ctx, detector)
    # 2 s speech padded with 3 s silence each: silence is trimmed away.
    tracker.add_caller_audio(_silence(1500) + _speech(2000) + _silence(1500), RATE)
    assert tasks == []
    tracker.add_caller_audio(_speech(2200), RATE)
    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert len(detector.calls) == 1
    analysed_bytes, rate = detector.calls[0]
    assert rate == RATE and analysed_bytes == 2 * RATE * 4200 // 1000  # voiced audio only


@pytest.mark.asyncio
async def test_4_detection_does_not_block_response_generation(accent_settings, mock_db_session):
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    detector = ScriptedAccentDetector([_high(GUJ)], delay=2.0)
    register_accent_detector("test-slow", lambda: detector)
    with patch.object(accent_settings, "accent_detector_provider", "test-slow"):
        voice = AsyncMock()
        voice.transcribe = AsyncMock(return_value="I want to know about your construction services")
        voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 1600)
        ai = AsyncMock()
        ai.chat = AsyncMock(return_value="Sure, what is your name?")
        handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
        handler.set_audio_sender(AsyncMock())
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_NB", "+91000")
            voice.synthesize.reset_mock()
            started = time.perf_counter()
            await handler._on_audio_flushed(_speech(4500))
            elapsed = time.perf_counter() - started
    ai.chat.assert_called()
    voice.synthesize.assert_called()
    assert elapsed < 1.5  # the 2 s detector did not hold up the reply
    assert len(detector.calls) == 1  # ...but it was started
    await handler.mark_stopped()


def test_5_low_confidence_result_does_not_change_profile():
    ctx = _ctx()
    for _ in range(5):
        update = apply_accent_result(ctx, AccentResult(GUJ, "medium", 4500))
        assert not update.updated and update.reason == "low_confidence"
    assert ctx.speech_style == NEUTRAL and not ctx.speech_style_locked


def test_6_repeated_high_confidence_consistent_results_update_profile():
    ctx = _ctx()
    first = apply_accent_result(ctx, _high(GUJ))
    assert not first.updated and first.reason == "awaiting_evidence"
    assert ctx.speech_style == NEUTRAL  # one result never changes the voice
    second = apply_accent_result(ctx, _high(GUJ))
    assert second.updated
    assert ctx.speech_style == GUJ and ctx.speech_style_locked
    assert ctx.speech_style_confidence == "high" and ctx.speech_style_evidence_turns == 2


def test_7_conflicting_results_do_not_cause_flapping():
    ctx = _ctx()
    apply_accent_result(ctx, _high(GUJ))
    assert apply_accent_result(ctx, _high(PUN)).reason == "conflicting_result"
    assert apply_accent_result(ctx, _high(GUJ)).reason == "conflicting_result"
    assert ctx.speech_style == NEUTRAL  # alternating evidence never settles
    apply_accent_result(ctx, _high(GUJ))
    assert ctx.speech_style == GUJ
    # Once set, a later result (any confidence) never moves it.
    assert apply_accent_result(ctx, AccentResult(PUN, "low", 4500)).reason == "conflicting_result"
    assert apply_accent_result(ctx, _high(PUN)).reason == "conflicting_result"
    assert ctx.speech_style == GUJ


def test_not_enough_audio_and_unknown_style_are_ignored():
    ctx = _ctx()
    assert apply_accent_result(ctx, _high(GUJ, ms=1500)).reason == "not_enough_audio"
    assert apply_accent_result(ctx, _high("unknown")).reason == "unknown_style"
    assert apply_accent_result(ctx, _high("klingon_influenced")).reason == "unknown_style"
    assert ctx.speech_style_evidence_turns == 0


@pytest.mark.asyncio
async def test_8_call_end_prevents_background_task_changing_context(accent_settings):
    detector = ScriptedAccentDetector([_high(GUJ)], delay=0.05)
    ctx = _ctx()
    with patch.object(accent_settings, "accent_min_evidence", 1):
        tracker, tasks = _tracker(accent_settings, ctx, detector)
        tracker.add_caller_audio(_speech(4500), RATE)
        tracker.close()  # Exotel 'stop' arrives while analysing
        await asyncio.gather(*tasks)
    assert ctx.speech_style == NEUTRAL and not ctx.speech_style_locked
    tracker.add_caller_audio(_speech(4500), RATE)
    assert len(tasks) == 1  # nothing new starts after the call ended


@pytest.mark.asyncio
async def test_8b_mark_stopped_cancels_pending_analysis(accent_settings, mock_db_session):
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    detector = ScriptedAccentDetector([_high(GUJ)], delay=30.0)
    register_accent_detector("test-hang", lambda: detector)
    with patch.object(accent_settings, "accent_detector_provider", "test-hang"):
        handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
        handler.set_audio_sender(AsyncMock())
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized("CA_STOP", "+91000")
        ctx = handler._context
        handler._offer_speech_style_audio(_speech(4500))
        background = [t for t in handler._tasks if t.get_name().startswith("background:")]
        assert len(background) == 1
        started = time.perf_counter()
        await handler.mark_stopped()
    assert time.perf_counter() - started < 1.0  # analysis never delays call end
    assert background[0].cancelled()
    assert ctx.speech_style == NEUTRAL


@pytest.mark.asyncio
async def test_barge_in_audio_is_never_used_for_speech_style(accent_settings, mock_db_session):
    from app.voice.speech_handler import SpeechHandler

    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    handler._context = _ctx()
    handler._speech_style.add_caller_audio = MagicMock()
    handler._utterance_overlapped_playback = True  # set by handle_barge_in
    handler._offer_speech_style_audio(_speech(4500))
    handler._speech_style.add_caller_audio.assert_not_called()
    handler._offer_speech_style_audio(_speech(4500))  # next clean utterance
    handler._speech_style.add_caller_audio.assert_called_once()


@pytest.mark.asyncio
async def test_resampled_only_when_detector_requires_it(accent_settings):
    detector = ScriptedAccentDetector([AccentResult(NEUTRAL, "high", 4500)], sample_rate=16000)
    tracker, tasks = _tracker(accent_settings, _ctx(), detector)
    tracker.add_caller_audio(_speech(4500), RATE)
    await asyncio.gather(*tasks)
    assert detector.calls == [(2 * 16000 * 4500 // 1000, 16000)]
    assert len(resample_pcm16(_speech(1000), RATE, RATE)) == 2 * RATE


def test_voiced_pcm_never_modifies_original():
    original = _silence(500) + _speech(1000)
    copy = bytes(original)
    trimmed = voiced_pcm(original, RATE)
    assert original == copy and len(trimmed) == 2 * RATE


@pytest.mark.asyncio
async def test_neutral_detector_is_the_default_and_always_neutral(accent_settings):
    ctx = _ctx()
    with patch.object(accent_settings, "accent_min_evidence", 1):
        tracker, tasks = _tracker(accent_settings, ctx, None)
        assert isinstance(tracker._detector, NeutralAccentDetector)
        tracker.add_caller_audio(_speech(4500), RATE)
        await asyncio.gather(*tasks)
    assert ctx.speech_style == NEUTRAL


# ── TTS routing (9–16) ──────────────────────────────────────────────────────


def _el_client(**voices):
    from app.voice.elevenlabs import ElevenLabsClient

    settings = MagicMock()
    settings.elevenlabs_api_key = "fake-key"
    settings.elevenlabs_model_id = "eleven_multilingual_v2"
    settings.elevenlabs_voice_id_en = "voice-en"
    settings.elevenlabs_voice_id_hi = "voice-hi"
    for key, value in voices.items():
        setattr(settings, key, value)
    with patch("app.voice.elevenlabs.get_settings", return_value=settings):
        return ElevenLabsClient()


def test_9_neutral_english_selects_neutral_english_voice():
    assert _el_client()._voice_for("en") == "voice-en"
    assert _el_client(elevenlabs_voice_en_neutral="en-neutral")._voice_for("en", NEUTRAL) == "en-neutral"


def test_10_neutral_hindi_selects_neutral_hindi_voice():
    assert _el_client()._voice_for("hi", NEUTRAL) == "voice-hi"
    assert _el_client(elevenlabs_voice_hi_neutral="hi-neutral")._voice_for("hi") == "hi-neutral"


def test_11_neutral_hinglish_selects_neutral_hinglish_voice():
    assert _el_client()._voice_for("hi-en") == "voice-hi"  # existing behaviour
    client = _el_client(elevenlabs_voice_hinglish_neutral="hinglish-neutral")
    assert client._voice_for("hi-en", NEUTRAL) == "hinglish-neutral"


def test_12_configured_gujarati_voice_selected_for_exact_pair():
    client = _el_client(
        elevenlabs_voice_en_gujarati="en-guj", elevenlabs_voice_hinglish_gujarati="hinglish-guj"
    )
    assert client.has_style_voice("en", GUJ)
    assert client._voice_for("en", GUJ) == "en-guj"
    assert client._voice_for("hi-en", GUJ) == "hinglish-guj"


def test_13_missing_specialised_voice_falls_back_to_neutral():
    client = _el_client(elevenlabs_voice_en_gujarati="en-guj")
    assert not client.has_style_voice("hi", GUJ)
    assert client._voice_for("hi", GUJ) == "voice-hi"
    assert client._voice_for("en", PUN) == "voice-en"
    assert client._voice_for("ta", GUJ) == "voice-en"  # unknown language: default


def test_voice_ids_are_not_logged():
    client = _el_client(elevenlabs_voice_en_gujarati="secret-voice-id")
    with patch("app.voice.elevenlabs.logger") as log:
        client._voice_for("en", GUJ)
    assert "secret-voice-id" not in str(log.info.call_args)


class _FakeElevenLabs:
    """ElevenLabsClient stand-in recording which speech style was requested."""

    def __init__(self, styled: set[tuple[str, str]]):
        self._styled = styled
        self.requests: list[tuple[str, str | None]] = []

    def has_style_voice(self, language, speech_style):
        return (language, speech_style) in self._styled

    async def synthesize(self, text, language="en", *, speech_style=None):
        self.requests.append((language, speech_style))
        return b"\x00\x00" * 1600

    async def synthesize_stream(self, text, language="en", *, speech_style=None):
        self.requests.append((language, speech_style))
        yield b"\x00\x00" * 1600


def _adaptive_handler(mock_db_session, styled: set[tuple[str, str]]):
    from app.voice.elevenlabs_provider import ElevenLabsProvider
    from app.voice.speech_handler import SpeechHandler
    from app.voice.tts import TextToSpeech
    from app.voice.voice_manager import VoiceManager

    fake = _FakeElevenLabs(styled)
    voice = VoiceManager(stt=MagicMock(), tts=TextToSpeech(ElevenLabsProvider(client=fake)))
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=AsyncMock())
    handler._context = _ctx()
    return handler, fake


async def _drain(handler, lang: str) -> None:
    async for _ in handler._synthesize("Ji, bilkul.", lang, handler._tts_voice_profile(lang)):
        pass


@pytest.mark.asyncio
async def test_12b_adaptive_mode_routes_locked_style_to_curated_voice(accent_settings, mock_db_session):
    with patch.object(accent_settings, "accent_detection_mode", "adaptive_tts"):
        handler, fake = _adaptive_handler(mock_db_session, {("en", GUJ)})
        ctx = handler._context
        apply_accent_result(ctx, _high(GUJ))
        await _drain(handler, "en")
        assert fake.requests[-1] == ("en", None)  # one detection: still neutral
        apply_accent_result(ctx, _high(GUJ))
        await _drain(handler, "en")
        assert fake.requests[-1] == ("en", GUJ)


@pytest.mark.asyncio
async def test_13b_adaptive_mode_without_curated_voice_uses_neutral(accent_settings, mock_db_session):
    with (
        patch.object(accent_settings, "accent_detection_mode", "adaptive_tts"),
        patch("app.voice.speech_handler.log_call_event") as log_event,
    ):
        handler, fake = _adaptive_handler(mock_db_session, {("en", GUJ)})
        ctx = handler._context
        ctx.speech_style, ctx.speech_style_locked = GUJ, True
        await _drain(handler, "hi")  # no Hindi+Gujarati-influenced voice
    assert fake.requests[-1] == ("hi", None)
    reasons = [c.kwargs.get("reason") for c in log_event.call_args_list]
    assert "voice_not_configured" in reasons


@pytest.mark.asyncio
async def test_14_detector_failure_falls_back_to_neutral_voice(accent_settings, mock_db_session):
    for failure in (RuntimeError("model crashed"), None):
        detector = (
            ScriptedAccentDetector([failure])
            if failure
            else ScriptedAccentDetector([_high(GUJ)], delay=5.0)  # times out
        )
        with (
            patch.object(accent_settings, "accent_detection_mode", "adaptive_tts"),
            patch.object(accent_settings, "accent_timeout_ms", 50),
            patch.object(accent_settings, "accent_min_evidence", 1),
            patch("app.voice.accent_detector.log_call_event") as log_event,
        ):
            handler, fake = _adaptive_handler(mock_db_session, {("en", GUJ)})
            tracker, tasks = _tracker(accent_settings, handler._context, detector)
            tracker.add_caller_audio(_speech(4500), RATE)
            await asyncio.gather(*tasks)
            await _drain(handler, "en")
        assert handler._context.speech_style == NEUTRAL
        assert fake.requests[-1] == ("en", None)
        fallback = [c for c in log_event.call_args_list if c.args[0] == "voice.accent.fallback"]
        assert fallback[0].kwargs["reason"] == ("detector_error" if failure else "timeout")
        assert "crashed" not in str(fallback)  # error messages are never logged


@pytest.mark.asyncio
async def test_15_log_only_mode_never_changes_tts_voice(accent_settings, mock_db_session):
    detector = ScriptedAccentDetector([_high(GUJ)])
    with patch("app.voice.accent_detector.log_call_event") as log_event:
        handler, fake = _adaptive_handler(mock_db_session, {("en", GUJ)})
        tracker, tasks = _tracker(accent_settings, handler._context, detector)
        for _ in range(2):
            tracker.add_caller_audio(_speech(4500), RATE)
            await asyncio.gather(*tasks)
        assert handler._context.speech_style == GUJ  # detected and logged...
        await _drain(handler, "en")
    assert fake.requests[-1] == ("en", None)  # ...but the voice never changes
    detected = [c.kwargs for c in log_event.call_args_list if c.args[0] == "voice.accent.detected"]
    assert detected and all(d["applied_to_tts"] is False for d in detected)
    assert set(detected[0]) >= {"style", "confidence", "usable_audio_ms", "accent_detection_ms"}
    assert "+91" not in str(log_event.call_args_list)  # no caller identity


@pytest.mark.asyncio
async def test_16_disabled_mode_never_invokes_classifier(mock_db_session):
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    detector = ScriptedAccentDetector([_high(GUJ)])
    register_accent_detector("test-disabled", lambda: detector)
    settings = get_settings()
    for enabled, mode in ((False, "adaptive_tts"), (True, "disabled")):
        with (
            patch.object(settings, "accent_detection_enabled", enabled),
            patch.object(settings, "accent_detection_mode", mode),
            patch.object(settings, "accent_detector_provider", "test-disabled"),
        ):
            voice = AsyncMock()
            voice.transcribe = AsyncMock(return_value="I want to know about construction")
            voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 1600)
            handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=AsyncMock())
            handler.set_audio_sender(AsyncMock())
            assert handler._speech_style is None
            with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
                await handler._ensure_initialized(f"CA_OFF_{mode}", "+91000")
                for _ in range(3):
                    await handler._on_audio_flushed(_speech(4500))
            assert handler._tts_voice_profile("en") is None
            await handler.mark_stopped()
    assert detector.calls == []


# ── Language, facts and routing independence (17–22) ───────────────────────


def _locked(style: str) -> CallContext:
    ctx = _ctx()
    ctx.speech_style, ctx.speech_style_confidence, ctx.speech_style_locked = style, "high", True
    return ctx


def _turn(ctx: CallContext, text: str) -> str:
    return update_language_profile(ctx, analyze_language(text)).response_language


def test_17_gujarati_influenced_english_stays_english():
    ctx = _locked(GUJ)
    assert _turn(ctx, "I want to know about your architecture services.") == "en"
    assert ctx.language == "en" and ctx.speech_style == GUJ


def test_18_punjabi_influenced_hinglish_stays_hinglish():
    ctx = _locked(PUN)
    assert _turn(ctx, "Mujhe interior design service ke baare mein information chahiye.") == "hi-en"
    assert ctx.language == "hi-en"


_RAG_FACT = "Kinikh provides architecture, planning and construction services."


async def _prompt(ctx: CallContext) -> str:
    from app.ai.conversation import ConversationEngine

    retriever = MagicMock()
    retriever.retrieve_context = AsyncMock(return_value=_RAG_FACT)
    engine = ConversationEngine([], client=AsyncMock())
    with patch("app.rag.retriever.get_retriever", return_value=retriever):
        prompt = await engine._build_system_prompt(ctx, text="construction services?")
    return re.sub(r"Current date and time: .*", "", prompt)


@pytest.mark.asyncio
async def test_19_speech_style_never_changes_prompt_or_rag_facts():
    neutral, styled = _ctx(), _locked(GUJ)
    for ctx in (neutral, styled):
        _turn(ctx, "Construction services kya provide karte ho?")
    neutral_prompt, styled_prompt = await _prompt(neutral), await _prompt(styled)
    assert _RAG_FACT in styled_prompt
    assert styled_prompt == neutral_prompt  # style never reaches the LLM
    assert "influenced" not in styled_prompt and "speech_style" not in styled_prompt


@pytest.mark.asyncio
async def test_20_speech_style_never_changes_department_routing(mock_db_session):
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    routed = {}
    for style in (NEUTRAL, GUJ):
        voice = AsyncMock()
        voice.transcribe = AsyncMock(return_value="Mujhe construction team se baat karni hai")
        voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 1600)
        ai = AsyncMock()
        ai.chat = AsyncMock(return_value="Please hold while I connect you. [TRANSFER:infra]")
        handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
        handler.set_audio_sender(AsyncMock())
        handler._close_call = AsyncMock()
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized(f"CA_ROUTE_{style}", "+91000")
            handler._context.speech_style = style
            handler._context.speech_style_locked = style != NEUTRAL
            await handler._on_audio_flushed(_speech(2000))
        routed[style] = handler._context.transfer_department
        await handler.mark_stopped()
    assert routed[NEUTRAL] == routed[GUJ] == "infra"


def test_21_speech_style_does_not_override_explicit_speak_english():
    ctx = _locked(PUN)
    _turn(ctx, "Mujhe ghar banwana hai aur interior bhi chahiye")
    assert _turn(ctx, "Please speak in English.") == "en"
    assert _turn(ctx, "haan") == "en"
    assert ctx.speech_style == PUN  # style untouched, language untouched by style


def test_22_explicit_hindi_request_still_wins():
    ctx = _locked(GUJ)
    _turn(ctx, "I want to know about your architecture services.")
    assert _turn(ctx, "Hindi mein baat karo.") == "hi"
    assert ctx.language == "hi"


def test_gujarati_language_request_is_not_a_speech_style():
    ctx = _ctx()
    _turn(ctx, "Gujarati mein baat karo please")
    assert ctx.speech_style == NEUTRAL and not ctx.speech_style_locked


# ── Performance (mocked pipeline — not a live-provider measurement) ─────────


async def _first_audio_latency(mock_db_session, accent: bool) -> float:
    """Wall time from end of caller speech to first audio frame sent, with
    STT/LLM/TTS mocked (instant). Accent detector (if on) takes 500 ms."""
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    settings = get_settings()
    detector = ScriptedAccentDetector([_high(GUJ)], delay=0.5)
    register_accent_detector("test-perf", lambda: detector)
    with (
        patch.object(settings, "accent_detection_enabled", accent),
        patch.object(settings, "accent_detection_mode", "log_only"),
        patch.object(settings, "accent_detector_provider", "test-perf"),
    ):
        voice = AsyncMock()
        voice.transcribe = AsyncMock(return_value="I want to know about construction services")
        voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 1600)
        ai = AsyncMock()
        ai.chat = AsyncMock(return_value="Sure, what is your name?")
        handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
        sent: list[float] = []

        async def _send(_frame):
            sent.append(time.perf_counter())

        handler.set_audio_sender(_send)
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            await handler._ensure_initialized(f"CA_PERF_{accent}", "+91000")
            await handler._outbound_queue.join()
            sent.clear()
            started = time.perf_counter()
            await handler._on_audio_flushed(_speech(4500))
            await handler._outbound_queue.join()
        await handler.mark_stopped()
    assert sent
    assert (len(detector.calls) == 1) == accent
    return sent[0] - started


@pytest.mark.asyncio
async def test_28_log_only_detection_leaves_first_audio_latency_unchanged(mock_db_session):
    without = min([await _first_audio_latency(mock_db_session, False) for _ in range(3)])
    with_accent = min([await _first_audio_latency(mock_db_session, True) for _ in range(3)])
    # A 500 ms detector adds no measurable delay before the first audio.
    assert with_accent < without + 0.05, (without, with_accent)


def test_accent_audio_copy_cost_is_negligible(accent_settings):
    tracker = SpeechStyleTracker(
        NeutralAccentDetector(),
        accent_settings,
        get_context=lambda: _ctx(),
        spawn=lambda coro: coro.close(),
    )
    utterance = _silence(300) + _speech(3000) + _silence(900)
    started = time.perf_counter()
    for _ in range(100):
        tracker._window, tracker._window_ms, tracker._analyses = bytearray(), 0, 0
        tracker.add_caller_audio(utterance, RATE)
    per_utterance_ms = (time.perf_counter() - started) * 1000 / 100
    assert per_utterance_ms < 5.0, per_utterance_ms
