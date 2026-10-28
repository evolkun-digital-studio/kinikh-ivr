"""Natural turn taking and everyday Hindi.

- An unfinished-sounding transcript waits for the caller to go on and is
  merged with what follows; a finished one is answered at once.
- A short backchannel ("haan") during playback never interrupts; sustained
  speech does; echo in the first part of a sentence is ignored; a barge-in
  that proves to be noise repeats the sentence it cut off.
- Fixed Hindi phrases use everyday spoken Hindi, not formal Hindi.

Uses the same real-timing harness as test_voice_duplex.py.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from loguru import logger
import pytest

from app.core.config import Settings, get_settings
from app.voice.transcript_filter import classify_turn_end
from app.voice.vad import BargeInDetector
from tests.test_voice_duplex import (
    _ai,
    _handler,
    _messages,
    _past_grace,
    _silence,
    _tone,
    _utterance,
    _voice,
    _wait_for,
)


@pytest.fixture
def turn_logs():
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(m.record["message"]), level="INFO")
    yield messages
    logger.remove(sink)


# ── End of turn ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("transcript", "decision"),
    [
        ("mujhe interior design chahiye aur", "wait_unfinished"),
        ("Mujhe interior design chahiye aur.", "wait_unfinished"),
        ("I need a flat in Noida but", "wait_unfinished"),
        ("मुझे घर बनवाना है और।", "wait_unfinished"),
        ("Haan, toh,", "wait_unfinished"),
        ("I was thinking that, um", "wait_unfinished"),
        ("Yes.", "wait_unfinished"),
        ("Okay so...", "wait_unfinished"),
        ("Hello?", "reply"),
        ("Matlab?", "reply"),
        ("I need interior design for my flat.", "reply"),
        ("मुझे 2BHK flat के लिए interior चाहिए।", "reply"),
    ],
)
def test_classify_turn_end(transcript, decision):
    assert classify_turn_end(transcript)[0] == decision


@pytest.mark.asyncio
async def test_unfinished_transcript_waits_and_merges_with_next_utterance(
    mock_db_session, turn_logs
):
    voice = _voice(["mujhe interior design chahiye aur", "ek 2BHK flat ke liye."])
    ai = _ai("Ji bilkul, flat kahan hai?")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: voice.transcribe.await_count == 1)
    await asyncio.sleep(0.2)  # well inside the 1.2 s wait
    ai.chat.assert_not_awaited()

    await handler.handle_audio(_utterance())  # the caller goes on
    await _wait_for(lambda: ai.chat.await_count == 1 and handler._turn_state == "LISTENING")
    await asyncio.sleep(0.05)

    assert _messages(handler, "user") == ["mujhe interior design chahiye aur ek 2BHK flat ke liye."]
    assert ai.chat.await_count == 1
    assert any(
        "[TURN_END] decision=wait_unfinished reason=trailing_connector:aur words=5" in m
        for m in turn_logs
    )
    assert any("[TURN_END] decision=reply reason=complete words=10" in m for m in turn_logs)
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_unfinished_transcript_then_silence_is_answered_after_the_wait(mock_db_session):
    settings = get_settings()
    voice = _voice("mujhe interior design chahiye aur")
    ai = _ai("Ji, aur kya chahiye?")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    with patch.object(settings, "unfinished_turn_extra_ms", 300):
        await handler.handle_audio(_utterance())
        await _wait_for(lambda: voice.transcribe.await_count == 1)
        stt_done = time.monotonic()
        await _wait_for(lambda: ai.chat.await_count == 1)

    assert time.monotonic() - stt_done >= 0.25
    assert _messages(handler, "user") == ["mujhe interior design chahiye aur"]
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_finished_transcript_is_answered_immediately(mock_db_session, turn_logs):
    voice = _voice("I need interior design for my flat.")
    ai = _ai("Sure, where is the flat?")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: voice.transcribe.await_count == 1)
    # Far sooner than unfinished_turn_extra_ms (1.2 s).
    await _wait_for(lambda: ai.chat.await_count == 1, timeout=0.5)

    assert any("[TURN_END] decision=reply reason=complete words=7" in m for m in turn_logs)
    assert not any("wait_unfinished" in m for m in turn_logs)
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_caller_repeating_short_words_still_gets_a_reply(mock_db_session):
    """ "hello" ... "hello" ... "hello": each sounds unfinished, but the turn
    is merged at most _MAX_CONTINUATION_MERGES times, then answered."""
    voice = _voice("Hello,")  # trailing comma: always sounds unfinished
    ai = _ai("Hi, how can I help?")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    for _ in range(3):
        await handler.handle_audio(_utterance(400))
        await asyncio.sleep(0.3)  # inside each 1.2 s wait
    await _wait_for(lambda: ai.chat.await_count >= 1, timeout=2)

    assert _messages(handler, "user") == ["Hello, Hello, Hello,"]
    await handler.mark_stopped()


# ── Barge-in ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_haan_during_playback_does_not_interrupt(mock_db_session):
    voice = _voice("I need interior design for my flat.", audio_seconds=1.5)
    ai = _ai("Sure, our interior team can help with that.")
    handler, _, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()

    await handler.handle_audio(_tone(300) + _silence(300))  # "haan"
    assert handler._turn_state == "TTS_PLAYING"
    clearer.assert_not_awaited()

    # ...nor does it become a turn of its own once playback ends.
    await _wait_for(lambda: handler._turn_state == "LISTENING")
    await asyncio.sleep(0.1)
    assert voice.transcribe.await_count == 1
    assert ai.chat.await_count == 1
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_500ms_of_speech_during_playback_interrupts(mock_db_session):
    voice = _voice("I need interior design for my flat.", audio_seconds=2.0)
    ai = _ai("Sure, our interior team can help with that.")
    handler, sender, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()

    started = time.monotonic()
    await handler.handle_audio(_tone(500))
    assert handler._turn_state == "CALLER_SPEAKING"
    clearer.assert_awaited_once()
    assert handler._outbound_queue.empty()
    assert time.monotonic() - started < 0.4
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_speech_inside_the_grace_window_does_not_interrupt(mock_db_session):
    """Echo of our own voice right at the start of a sentence is ignored."""
    voice = _voice("I need interior design for my flat.", audio_seconds=2.0)
    ai = _ai("Sure, our interior team can help with that.")
    handler, _, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    # The grace window starts when audio is sent, after TTS is committed.
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING" and bool(handler._sentence_starts))
    await handler.handle_audio(_tone(400) + _silence(200))  # all inside the 500 ms grace

    assert handler._turn_state == "TTS_PLAYING"
    clearer.assert_not_awaited()
    await handler.mark_stopped()


def test_barge_in_detector_keeps_grace_audio_but_needs_speech_past_it():
    frame = 320  # 20 ms at 8 kHz
    detector = BargeInDetector(
        frame_bytes=frame, min_rms=600, min_active_ms=350, gap_tolerance_ms=120
    )
    # Speech starting inside the grace window cannot confirm on its own...
    assert detector.detect(_tone(400), suppressed=True) is None
    # ...but once it continues for 350 ms past it, it does, with all its audio.
    result = detector.detect(_tone(400))
    assert result is not None
    assert len(result.speech) >= len(_tone(400)) + len(_tone(340))

    # Grace-window energy followed by silence is dropped (echo).
    detector.reset()
    assert detector.detect(_tone(400), suppressed=True) is None
    assert detector.detect(_silence(200) + _tone(300)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("noise_transcript", ["", "Thank you for watching!"])
async def test_noise_barge_in_repeats_the_cut_off_sentence(mock_db_session, noise_transcript):
    sentence = "Okay, I can connect you with our architecture team."
    voice = _voice(["I want to build a house.", noise_transcript], audio_seconds=1.5)
    ai = _ai(sentence)
    handler, _, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()
    await handler.handle_audio(_tone(500) + _silence(900))  # "interruption" that is noise
    clearer.assert_awaited_once()

    await _wait_for(
        lambda: voice.synthesize.await_count == 2 and handler._turn_state == "LISTENING",
        timeout=5,
    )
    spoken = [c.args[0] for c in voice.synthesize.await_args_list]
    assert spoken == [sentence, sentence]
    assert ai.chat.await_count == 1  # repeated, not a new LLM reply
    assert _messages(handler, "assistant") == [sentence]  # recorded once
    await handler.mark_stopped()


def test_barge_in_and_turn_defaults():
    fields = Settings.model_fields
    assert fields["end_of_speech_ms"].default == 900
    assert fields["unfinished_turn_extra_ms"].default == 1200
    assert fields["barge_in_min_rms_energy"].default == 600
    assert fields["barge_in_min_active_ms"].default == 350
    assert fields["barge_in_gap_tolerance_ms"].default == 120
    assert fields["barge_in_grace_ms"].default == 500


# ── Everyday Hindi ───────────────────────────────────────────────────────────

_FORMAL_HINDI = ("कृपया", "आवश्यकता", "सम्पर्क", "संपर्क", "विभाग", "सूचना", "उपलब्ध", "धन्यवाद", "प्रतीक्षा")


def _fixed_hindi_phrases() -> dict[str, str]:
    from app.ai import conversation
    from app.prompts import system

    phrases = {
        name: getattr(system, name)
        for name in (
            "MISSED_THAT_HI",
            "STILL_THERE_HI",
            "SILENCE_GOODBYE_HI",
            "FAREWELL_HI",
            "CONFIRMATION_PROMPT_HI",
        )
    }
    phrases["_FALLBACK_HI"] = conversation._FALLBACK_HI
    for q in system.CONVERSATION_QUESTIONS:
        phrases[f"question:{q['field']}"] = q["hi"]
    return phrases


@pytest.mark.parametrize("name", sorted(_fixed_hindi_phrases()))
def test_fixed_hindi_phrases_avoid_formal_words(name):
    text = _fixed_hindi_phrases()[name]
    assert not [w for w in _FORMAL_HINDI if w in text], name


def test_hindi_phrases_keep_company_name_placeholder():
    from app.ai.prompt_manager import PromptManager

    pm = PromptManager()
    assert "Kinikh Group" in pm.farewell("hi", "Kinikh Group")
    assert "Kinikh Group" in pm.silence_goodbye("hi", "Kinikh Group")
    assert "{" not in pm.missed_that("hi") + pm.still_there("hi")


def test_greeting_prompt_asks_for_everyday_hindi_and_short_replies():
    from app.ai.prompt_manager import PromptManager

    prompt = PromptManager().greeting(
        "Kinikh Group", "INFO", language="hi", language_locked=True
    )
    assert "everyday spoken Hindi" in prompt
    assert "हमारी team आपको जल्दी call back करेगी" in prompt
    assert "Reply in one or two short sentences, about 25 words at most" in prompt
