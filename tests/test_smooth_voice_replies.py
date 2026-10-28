import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import ConversationEngine, split_speakable
from app.voice.speech_handler import _FILLER_AUDIO_CACHE, _FILLER_TEXTS, SpeechHandler, _Response
from app.voice.turn_metrics import TurnMetrics


def _handler(db):
    handler = SpeechHandler(db=db, voice=AsyncMock(), ai_client=AsyncMock())
    response = _Response(
        turn_id=1, kind="caller_turn", metrics=TurnMetrics(call_sid="SMOOTH", turn_id=1)
    )
    handler._response = response
    handler._context = CallContext(call_sid="SMOOTH", caller_number="")
    return handler, response


def test_commas_do_not_split_a_spoken_sentence_into_separate_tts_requests():
    text = "For your website request, our team can help with that."
    assert split_speakable(text) == ([text], "")
    assert split_speakable("For your website request, ") == ([], "For your website request, ")


def test_hindi_fillers_are_warm_and_do_not_use_acha():
    for language in ("hi", "hi-en"):
        assert _FILLER_TEXTS[language][0] == "जी, ज़रूर।"
        assert all("अच्छा" not in text and "एक पल" not in text for text in _FILLER_TEXTS[language])


@pytest.mark.asyncio
async def test_filler_finishes_whole_when_reply_audio_becomes_ready(mock_db_session, monkeypatch):
    handler, response = _handler(mock_db_session)
    response.language = "hi"
    text = _FILLER_TEXTS["hi"][0]
    frames = [b"\x01\x00" * 1600, b"\x02\x00" * 1600]
    monkeypatch.setitem(_FILLER_AUDIO_CACHE, (text, "hi"), frames)

    async def emit(*args):
        response.reply_audio_ready.set()
        return True

    handler._emit_audio = AsyncMock(side_effect=emit)
    assert await handler._play_filler(response) is None
    assert handler._emit_audio.await_count == 2


@pytest.mark.asyncio
async def test_filler_and_reply_audio_do_not_interleave(mock_db_session, monkeypatch):
    handler, response = _handler(mock_db_session)
    response.language = "hi"
    handler._send_audio = AsyncMock()
    handler._voice.synthesize.return_value = b"\x03\x00" * 1600
    text = _FILLER_TEXTS["hi"][0]
    monkeypatch.setitem(_FILLER_AUDIO_CACHE, (text, "hi"), [b"\x01\x00" * 1600, b"\x02\x00" * 1600])
    started = asyncio.Event()
    order = []

    async def emit(audio, response, segment_text=None):
        order.append("reply" if segment_text else "filler")
        if len(order) == 1:
            started.set()
            await response.reply_audio_ready.wait()
        return True

    handler._emit_audio = emit
    filler = asyncio.create_task(handler._play_filler(response))
    reply = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        reply = asyncio.create_task(handler._speak_segment("हमारी टीम आपकी मदद करेगी।", response))
        await asyncio.wait_for(asyncio.gather(filler, reply), 1)
        assert order == ["filler", "filler", "reply"]
    finally:
        filler.cancel()
        if reply:
            reply.cancel()
        await asyncio.gather(filler, *([reply] if reply else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_next_sentence_can_start_tts_while_a_small_audio_queue_remains(mock_db_session):
    handler, response = _handler(mock_db_session)
    handler._playback_until = TurnMetrics.now() + 0.2
    handler._pending_audio_bytes = 3200
    handler._outbound_queue.put_nowait((b"\x00" * 3200, response, None))
    with patch(
        "app.voice.speech_handler.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=AssertionError("unnecessary wait"),
    ):
        await handler._wait_for_playback_headroom(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "hi"])
async def test_language_rule_is_the_last_instruction_even_after_rag(language):
    ctx = CallContext(
        call_sid="SMOOTH",
        caller_number="",
        language=language,
        language_locked=True,
        response_language=language,
    )
    engine = ConversationEngine([], client=AsyncMock())
    prompt = await engine._build_system_prompt(ctx, rag_context="FACTS IN ENGLISH")
    final = prompt.split("FINAL SPOKEN LANGUAGE:")[-1]
    assert "FINAL SPOKEN LANGUAGE:" in prompt
    assert "English" in final if language == "en" else "Hindi" in final
    assert "acknowledgement" in final


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language,reply,expected",
    [
        (
            "hi",
            "अच्छा, आपकी website की जानकारी बता दीजिए।",
            "जी, ज़रूर। आपकी website की जानकारी बता दीजिए।",
        ),
        ("en", "Achha, our team can help with that.", "Our team can help with that."),
        ("hi", "अच्छा काम करने के लिए हमारी team है।", "अच्छा काम करने के लिए हमारी team है।"),
    ],
)
async def test_acknowledgement_is_gentle_and_in_reply_language(language, reply, expected):
    ctx = CallContext(
        call_sid="SMOOTH",
        caller_number="",
        language=language,
        language_locked=True,
        response_language=language,
    )
    ctx.add_message("user", "A question")
    client = AsyncMock()
    client.chat.return_value = reply
    engine = ConversationEngine([], client=client)
    result = [
        part async for part in engine.stream_reply(ctx, first_token_timeout=1, rag_context="")
    ]
    assert " ".join(result) == expected


def test_ek_pal_is_replaced_before_tts():
    from app.voice.tts_text import normalize_for_tts

    assert normalize_for_tts("एक पल, मैं check करती हूँ।", "hi") == "One second, मैं check करती हूँ।"
    assert normalize_for_tts("Ek pal, let me check.", "hi-en") == "One second, let me check."
    assert normalize_for_tts("पलक एक पलटन", "hi") == "पलक एक पलटन"
