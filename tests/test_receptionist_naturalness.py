"""Voice-boundary regressions; no live synthesis or paid API calls."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import strip_repeated_opener
from app.ai.language_profile import analyze_language, update_language_profile
from app.core.config import Settings, get_settings
from app.voice.speech_handler import SpeechHandler, _Response
from app.voice.turn_metrics import TurnMetrics
from app.voice.tts_text import normalize_for_tts


def test_default_brand_hint_uses_short_hindi_vowels():
    settings = get_settings()
    default_map = Settings.model_fields["tts_pronunciations"].default
    with patch.object(settings, "tts_pronunciations", default_map):
        for language in ("en", "hi", "hi-en"):
            assert normalize_for_tts("KINIKH Group", language) == "किनिख Group"


def test_configurable_ordered_whole_word_map_and_acronyms():
    with patch.object(get_settings(), "tts_pronunciations", "KINIKH=Kinikh;Kinikh=Ki-nikh"):
        assert (
            normalize_for_tts("KINIKH Group CRM HELLO Kinikhish")
            == "Ki-nikh Group CRM Hello Kinikhish"
        )
        assert normalize_for_tts("KINIKH Group", "hi") == "किनिख Group"
        assert normalize_for_tts("KINIKH-powered CRM") == "Ki-nikh-powered CRM"


@pytest.mark.parametrize(
    "first,short",
    [
        ("I want to make a booking", "haan ok"),
        ("मुझे घर बनवाना है", "website"),
        ("I need help with construction", "हाँ"),
    ],
)
def test_short_turn_never_switches(first, short):
    ctx = CallContext(call_sid="LANG", caller_number="")
    update_language_profile(ctx, analyze_language(first))
    original = ctx.language
    update_language_profile(ctx, analyze_language(short))
    assert ctx.language == ctx.reply_language == original


def test_script_ratio_counts_unknown_latin_words_against_switch():
    ctx = CallContext(call_sid="LANG", caller_number="")
    update_language_profile(ctx, analyze_language("I need help with construction"))
    update_language_profile(
        ctx, analyze_language("मुझे चाहिए bespoke landscaping carpentry plastering roofing")
    )
    assert ctx.language == "en"


@pytest.mark.parametrize(
    "utterance,expected",
    [("English please", "en"), ("हिंदी में", "hi"), ("Hindi mein baat karo", "hi")],
)
def test_explicit_request_switches_immediately(utterance, expected):
    ctx = CallContext(call_sid="LANG", caller_number="")
    update_language_profile(ctx, analyze_language(utterance))
    assert ctx.language == expected and ctx.language_locked


def test_clear_english_turn_switches_hindi_call():
    ctx = CallContext(call_sid="LANG", caller_number="")
    update_language_profile(ctx, analyze_language("मुझे घर बनवाना है"))
    update_language_profile(ctx, analyze_language("I need a website. Can you help me with this?"))
    assert ctx.language == ctx.reply_language == "en"


@pytest.mark.parametrize(
    "current,previous,expected",
    [
        ("Haanji, we can help with that.", "Haanji, tell me more.", "We can help with that."),
        ("Haan ji, we can help.", "Ji, please continue.", "We can help."),
        ("जी, हमारी team call करेगी।", "हाँ जी, बताइए।", "हमारी team call करेगी।"),
        ("Okay, we can help.", "Okay Rahul, tell me more.", "We can help."),
        ("We offer booking support.", "We offer construction services.", "Booking support."),
    ],
)
def test_repeated_openers(current, previous, expected):
    assert strip_repeated_opener(current, previous) == expected


def _response(handler, transcript="I need help with a website"):
    response = _Response(
        turn_id=2,
        kind="caller_turn",
        metrics=TurnMetrics(call_sid="FILLER", turn_id=2, kind="caller_turn"),
    )
    response.metrics.speech_end = TurnMetrics.now() - 2
    response.transcript = transcript
    response.language = "en"
    response.analyzed.set()
    handler._response = response
    handler._turn_number = 2
    handler._context = CallContext(call_sid="FILLER", caller_number="")
    return response


@pytest.mark.asyncio
async def test_fast_reply_skips_filler(mock_db_session):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _response(handler)
    response.reply_audio_ready.set()
    assert await handler._filler_skip_reason(response) == "reply_ready"


@pytest.mark.asyncio
async def test_recent_filler_never_plays_on_next_turn(mock_db_session):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _response(handler)
    handler._last_filler_turn = 1
    assert await handler._filler_skip_reason(response) == "recent_filler"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transcript,control,expected",
    [
        ("haan ok", False, "yes_no_answer"),
        ("Please connect me to your team", False, "closing_or_transfer"),
        ("That is all I needed", True, "call_ending"),
    ],
)
async def test_filler_skips_answers_and_control_replies(
    mock_db_session, transcript, control, expected
):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _response(handler, transcript)
    response.reply_generated.set()
    response.contains_control = control
    assert await asyncio.wait_for(handler._filler_skip_reason(response), 1) == expected


@pytest.mark.asyncio
async def test_filler_waits_for_1500ms_without_slowing_fast_reply(mock_db_session):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _response(handler)
    response.metrics.speech_end = TurnMetrics.now()
    task = asyncio.create_task(handler._filler_skip_reason(response))
    await asyncio.sleep(0)
    assert not task.done()
    response.reply_audio_ready.set()
    assert await asyncio.wait_for(task, 0.1) == "reply_ready"


@pytest.mark.asyncio
async def test_filler_rotation_and_spacing(mock_db_session, monkeypatch):
    from app.voice.speech_handler import _FILLER_AUDIO_CACHE

    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    handler._emit_audio = AsyncMock(return_value=True)
    response = _response(handler)
    response.reply_generated.set()
    for text in ("Okay.", "Sure.", "Right."):
        monkeypatch.setitem(_FILLER_AUDIO_CACHE, (text, "en"), [b"\x00\x00" * 160])
        assert await handler._filler_skip_reason(response) is None
        assert await handler._play_filler(response) is None
        handler._turn_number += 1
        assert await handler._filler_skip_reason(response) == "recent_filler"
        handler._turn_number += 2
    assert handler._used_fillers == {"Okay.", "Sure.", "Right."}
    assert await handler._play_filler(response) == "all_used"


@pytest.mark.asyncio
async def test_phone_filter_precedes_pronunciation_and_language_is_fixed(mock_db_session):
    voice = AsyncMock()
    voice.synthesize.return_value = b"\x00\x00" * 160
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=AsyncMock())
    response = _response(handler)
    handler._send_audio = AsyncMock()
    handler._emit_audio = AsyncMock(return_value=True)
    handler._tts_voice_profile = Mock(return_value=None)
    with patch("app.voice.speech_handler.normalize_for_tts", wraps=normalize_for_tts) as normalize:
        await handler._speak_segment("Call KINIKH on 9876543210.", response)
        assert "9876543210" not in normalize.call_args.args[0]
    assert "किनिख" in voice.synthesize.call_args.args[0]
    assert "9876543210" not in voice.synthesize.call_args.args[0]
    handler._context.voice_language = "hi"
    await handler._speak_segment("Our team can help.", response)
    assert voice.synthesize.call_args.args[1] == "en"
    handler._tts_voice_profile.assert_called_once_with("en")


def test_voice_settings_configurable(monkeypatch):
    from app.voice.elevenlabs import ElevenLabsClient

    settings = get_settings()
    for key, value in (("stability", 0.4), ("similarity", 0.75), ("style", 0.2), ("speed", 0.95)):
        monkeypatch.setattr(settings, "elevenlabs_" + key, value)
    assert ElevenLabsClient()._payload("Hello")["voice_settings"] == {
        "stability": 0.4,
        "similarity_boost": 0.75,
        "style": 0.2,
        "speed": 0.95,
        "use_speaker_boost": False,
    }


@pytest.mark.asyncio
async def test_startup_caches_use_normalized_text(monkeypatch):
    import app.voice.speech_handler as speech

    voice = Mock(last_used_fallback=False)
    voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 160)
    monkeypatch.setattr(speech, "VoiceManager", lambda: voice)
    monkeypatch.setattr(speech, "_GREETING_AUDIO_CACHE", {})
    monkeypatch.setattr(speech, "_FILLER_AUDIO_CACHE", {})
    await speech.prewarm_greeting()
    assert "किनिख Group" in voice.synthesize.call_args.args[0]
    assert speech._GREETING_AUDIO_CACHE
    voice.synthesize.reset_mock()
    await speech.prewarm_fillers()
    assert len(speech._FILLER_AUDIO_CACHE) == 9
    spoken = {call.args[0] for call in voice.synthesize.call_args_list}
    assert spoken == {"Okay.", "Sure.", "Right.", "जी, ज़रूर।", "बिल्कुल।", "जी।"}


@pytest.mark.asyncio
async def test_late_transfer_marker_blocks_filler_while_tts_waits(mock_db_session):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _response(handler, "I would like help with my project")
    audio_release = asyncio.Event()
    first_tts = asyncio.Event()
    marker_release = asyncio.Event()

    async def stream_reply(*args, **kwargs):
        yield "Let me connect you to our team."
        await marker_release.wait()
        yield "[TRANSFER:tech]"

    async def slow_speak(*args):
        first_tts.set()
        await audio_release.wait()

    handler._engine = Mock(
        stream_reply=stream_reply, last_first_token_at=None, last_history_messages=0
    )
    handler._speak_segment = slow_speak
    reply_task = asyncio.create_task(handler._speak_reply(response))
    filler_task = asyncio.create_task(handler._filler_skip_reason(response))
    try:
        await asyncio.wait_for(first_tts.wait(), 1)
        assert not filler_task.done()
        marker_release.set()
        assert await asyncio.wait_for(filler_task, 1) == "call_ending"
        audio_release.set()
        await asyncio.wait_for(reply_task, 1)
    finally:
        reply_task.cancel()
        filler_task.cancel()
        await asyncio.gather(reply_task, filler_task, return_exceptions=True)
