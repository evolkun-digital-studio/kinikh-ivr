"""Full-duplex live-call behaviour of SpeechHandler.

These drive the real SpeechHandler / AudioBuffer / VAD code with synthetic
PCM16 audio at real timing (playback windows are real, just short), with only
the providers (STT, LLM, TTS) and the database mocked.
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.ai.openai_client import OpenAIClient
from app.core.config import get_settings
from app.services.department_service import DepartmentService
from app.voice.speech_handler import SpeechHandler

SAMPLE_RATE = 8000
FRAME_SAMPLES = 160  # 20 ms


def _tone(ms: int, amplitude: int = 3000) -> bytes:
    samples = SAMPLE_RATE * ms // 1000
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude) for i in range(samples)
    )


def _silence(ms: int) -> bytes:
    return b"\x00" * (SAMPLE_RATE * ms // 1000 * 2)


def _syllabic_speech(ms: int, on_ms: int = 120, off_ms: int = 20) -> bytes:
    """Speech-like energy: syllables separated by brief quiet dips — the
    pattern the old barge-in detector could never confirm, because one
    quiet frame reset its streak."""
    out = b""
    while len(out) < SAMPLE_RATE * ms // 1000 * 2:
        out += _tone(on_ms) + _silence(off_ms)
    return out


def _utterance(speech_ms: int = 500) -> bytes:
    """Speech followed by enough silence (> END_OF_SPEECH_MS) to end the turn."""
    return _tone(speech_ms) + _silence(900)


def _assistant_audio(seconds: float) -> bytes:
    return _silence(int(seconds * 1000))


def _voice(transcripts: list[str] | str = "Hello", audio_seconds: float = 0.05) -> AsyncMock:
    voice = AsyncMock()
    if isinstance(transcripts, list):
        voice.transcribe = AsyncMock(side_effect=transcripts)
    else:
        voice.transcribe = AsyncMock(return_value=transcripts)
    voice.synthesize = AsyncMock(return_value=_assistant_audio(audio_seconds))
    return voice


def _ai(replies: list[str] | str = "Sure.") -> AsyncMock:
    client = AsyncMock()
    if isinstance(replies, list):
        client.chat = AsyncMock(side_effect=replies)
    else:
        client.chat = AsyncMock(return_value=replies)
    return client


async def _handler(voice, ai, db, *, greeting: bool = False):
    settings = get_settings()
    handler = SpeechHandler(db=db, voice=voice, ai_client=ai)
    sender = AsyncMock()
    clearer = AsyncMock()
    handler.set_audio_sender(sender)
    handler.set_audio_clearer(clearer)
    original = settings.ai_auto_greeting
    settings.ai_auto_greeting = greeting
    try:
        with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
            # A unique call per test: CallContext lives in the process-wide
            # context_manager until finalize() removes it.
            await handler._ensure_initialized(f"CA_{uuid.uuid4().hex}", "+919876543210", "MZ1")
    finally:
        settings.ai_auto_greeting = original
    # Skip LLM language detection so ai.chat counts only conversation replies.
    handler._context.language_detected = True
    return handler, sender, clearer


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)


def _messages(handler, role: str) -> list[str]:
    return [m.content for m in handler._context.messages if m.role == role]


async def _past_grace() -> None:
    """Wait out the start-of-sentence barge-in grace window, so speech sent
    next is judged as a real interruption rather than echo."""
    await asyncio.sleep(get_settings().barge_in_grace_ms / 1000 + 0.05)


@pytest.mark.asyncio
async def test_second_utterance_during_playback_gets_its_own_reply(mock_db_session):
    """Caller turn 1 → reply 1 (still audible) → caller speaks normally
    (syllabic speech, dips between syllables) → reply 1 stops, turn 2 is
    heard and answered. Exactly two replies; the caller's speech is never
    dropped because the assistant was talking. Failed before this change:
    the playback gate discarded that speech and only one reply was given."""
    voice = _voice(["I have a first question.", "I have a second question."], audio_seconds=1.0)
    ai = _ai(["First answer.", "Second answer."])
    handler, _, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()

    await handler.handle_audio(_syllabic_speech(600) + _silence(900))
    await _wait_for(lambda: ai.chat.await_count == 2 and handler._turn_state == "LISTENING")

    assert _messages(handler, "user") == ["I have a first question.", "I have a second question."]
    assert ai.chat.await_count == 2
    clearer.assert_awaited()  # reply 1 was cut off at Exotel
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_two_turns_after_playback_produce_exactly_two_replies(mock_db_session):
    voice = _voice(["I have a first question.", "I have a second question."], audio_seconds=0.1)
    ai = _ai(["First answer.", "Second answer."])
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: ai.chat.await_count == 1 and handler._turn_state == "LISTENING")
    await handler.handle_audio(_utterance())
    await _wait_for(lambda: ai.chat.await_count == 2 and handler._turn_state == "LISTENING")
    await asyncio.sleep(0.05)

    assert ai.chat.await_count == 2
    assert _messages(handler, "assistant") == ["First answer.", "Second answer."]
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_barge_in_stops_output_and_never_resumes_old_reply(mock_db_session):
    """AI: "Okay, I can connect you with our architecture team. ..."
    Caller: "Wait, I need help with an existing project."
    Playback stops at once, queued audio is cleared, the second sentence is
    never sent, the new statement is answered, the old reply never replays."""
    voice = _voice(["I want to build a house", "Wait, I need help with an existing project"])
    old_reply = (
        "Okay, I can connect you with our architecture team. "
        "They handle all new building designs and approvals."
    )
    ai = _ai([old_reply, "Got it. What kind of changes do you need?"])
    handler, sender, clearer = await _handler(voice, ai, mock_db_session)
    # Long enough that the next sentence is not yet pre-synthesized (lookahead)
    # when the caller interrupts just after the grace window.
    voice.synthesize = AsyncMock(return_value=_assistant_audio(2.0))

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()
    sent_at_barge_in = sender.await_count

    await handler.handle_audio(_tone(600))  # sustained caller speech → barge-in
    assert handler._turn_state == "CALLER_SPEAKING"
    clearer.assert_awaited_once()
    await asyncio.sleep(0.3)
    assert sender.await_count == sent_at_barge_in  # old reply: nothing more sent

    await handler.handle_audio(_silence(900))  # caller finishes the new statement
    await _wait_for(
        lambda: ai.chat.await_count == 2 and handler._turn_state == "LISTENING", timeout=5
    )

    spoken = [c.args[0] for c in voice.synthesize.await_args_list]
    assert "They handle all new building designs and approvals." not in spoken
    assert spoken[-1] == "Got it. What kind of changes do you need?"
    assert spoken.count("Okay, I can connect you with our architecture team.") == 1
    # History holds what the caller heard of the interrupted reply, not all of it.
    assistant = _messages(handler, "assistant")
    assert assistant[0] == "Okay, I can connect you with our architecture team."
    assert assistant[1] == "Got it. What kind of changes do you need?"
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_stale_llm_tokens_never_play_after_newer_turn(mock_db_session):
    """Turn 1 is still streaming from the LLM when the caller interrupts;
    its delayed tokens must never be synthesized or sent afterwards."""
    release_rest = asyncio.Event()

    async def stream_chat(messages, **kwargs):
        if len([m for m in messages if m["role"] == "user"]) == 1:
            yield "Our architecture team designs new homes. Also"
            await release_rest.wait()
            yield " a stale sentence that must never be spoken. "
        else:
            yield "Sure, tell me about the existing project."

    client = OpenAIClient.__new__(OpenAIClient)  # no network: stream_chat is replaced
    client.stream_chat = stream_chat
    voice = _voice(["I want a new house.", "It is about an existing project."], audio_seconds=1.0)
    handler, sender, _ = await _handler(voice, client, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    await _past_grace()
    await handler.handle_audio(_tone(600) + _silence(900))  # barge-in + new turn
    release_rest.set()  # turn-1 tokens arrive late
    await _wait_for(
        lambda: voice.synthesize.await_count == 2 and handler._turn_state == "LISTENING"
    )
    await asyncio.sleep(0.1)

    spoken = [c.args[0] for c in voice.synthesize.await_args_list]
    assert spoken == [
        "Our architecture team designs new homes.",
        "Sure, tell me about the existing project.",
    ]
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_short_pause_stays_one_caller_turn(mock_db_session):
    """ "I wanted help with my house..." 300 ms pause "...which is already
    under construction." is one turn: one STT request, one reply."""
    voice = _voice("I wanted help with my house which is already under construction")
    ai = _ai("Sure, what do you need?")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_tone(600) + _silence(300) + _tone(600) + _silence(900))
    await _wait_for(lambda: ai.chat.await_count == 1 and handler._turn_state == "LISTENING")
    await asyncio.sleep(0.05)

    voice.transcribe.assert_awaited_once()
    wav = voice.transcribe.await_args.args[0]
    assert len(wav) - 44 >= len(_tone(600)) * 2  # both phrases in the one request
    assert ai.chat.await_count == 1
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_caller_resuming_before_reply_is_merged_into_one_turn(mock_db_session):
    """A pause long enough to end the turn, but the caller resumes before
    any reply is audible: the in-flight turn is abandoned and both parts are
    answered together — one user message, one reply, nothing spoken twice."""
    first_stt = asyncio.Event()
    never = asyncio.Event()
    calls = 0

    async def transcribe(wav, language):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_stt.set()
            await never.wait()
        return "I wanted help with my house which is under construction"

    voice = _voice()
    voice.transcribe = AsyncMock(side_effect=transcribe)
    ai = _ai("Got it, what stage is it at?")
    handler, sender, _ = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance(400))
    await asyncio.wait_for(first_stt.wait(), 1)
    await handler.handle_audio(_utterance(400))  # caller continues
    await _wait_for(lambda: ai.chat.await_count == 1 and handler._turn_state == "LISTENING")

    first_wav, merged_wav = (c.args[0] for c in voice.transcribe.await_args_list)
    assert len(merged_wav) > len(first_wav)
    assert _messages(handler, "user") == ["I wanted help with my house which is under construction"]
    assert ai.chat.await_count == 1
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_echo_during_playback_does_not_interrupt(mock_db_session):
    """Quiet leakage of our own audio (below the barge-in energy floor) for
    the whole playback must neither interrupt nor reach STT."""
    voice = _voice("Hello", audio_seconds=0.4)
    ai = _ai("Hi there, how can I help?")
    handler, _, clearer = await _handler(voice, ai, mock_db_session)

    await handler.handle_audio(_utterance())
    await _wait_for(lambda: handler._turn_state == "TTS_PLAYING")
    for _ in range(15):
        await handler.handle_audio(_tone(20, amplitude=200))  # echo-level energy
    await _wait_for(lambda: handler._turn_state == "LISTENING")

    clearer.assert_not_awaited()
    voice.transcribe.assert_awaited_once()
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_disconnect_cancels_and_awaits_every_task(mock_db_session):
    llm_started = asyncio.Event()

    async def slow_chat(messages):
        llm_started.set()
        await asyncio.Event().wait()

    voice = _voice("Hello, I need some help.")
    ai = _ai()
    ai.chat = AsyncMock(side_effect=slow_chat)
    handler, sender, _ = await _handler(voice, ai, mock_db_session)
    await handler.handle_audio(_tone(600))  # utterance in progress (stall watchdog armed)
    await handler.handle_audio(_silence(900))
    await asyncio.wait_for(llm_started.wait(), 1)
    await handler.handle_audio(_tone(600))  # another utterance open when the call ends

    buffer = handler._buffer
    await handler.mark_stopped()
    await handler.mark_stopped()  # idempotent

    await asyncio.sleep(0)  # let done_callbacks fire
    assert not handler._tasks
    assert all(task.done() for task in buffer._flush_tasks)
    assert buffer._silence_task is None or buffer._silence_task.done()
    assert handler._turn_state == "ENDED"
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_stt_timeout_gets_a_natural_spoken_fallback(mock_db_session):
    settings = get_settings()

    async def hang(wav, language):
        await asyncio.Event().wait()

    voice = _voice()
    voice.transcribe = AsyncMock(side_effect=hang)
    ai = _ai()
    handler, sender, _ = await _handler(voice, ai, mock_db_session)

    with patch.object(settings, "stt_timeout_seconds", 0.05):
        await handler.handle_audio(_utterance())
        await _wait_for(lambda: voice.synthesize.await_count == 1 and sender.await_count > 0)

    assert voice.synthesize.await_args.args[0] == "I could not hear you clearly. Please repeat your query."
    sender.assert_awaited()
    ai.chat.assert_not_awaited()
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_greeting_does_not_block_receive_loop_and_can_be_interrupted(mock_db_session):
    """start() returns before the greeting finishes, so caller audio keeps
    flowing and a caller talking over the greeting interrupts it."""
    voice = _voice("Hello?", audio_seconds=2.0)
    ai = _ai("Hi! How can I help?")
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
    sender, clearer = AsyncMock(), AsyncMock()
    handler.set_audio_sender(sender)
    handler.set_audio_clearer(clearer)

    with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
        await asyncio.wait_for(handler.start(f"CA_{uuid.uuid4().hex}", "+919876543210", "MZ1"), 0.5)
    await _wait_for(lambda: handler._turn_state == "GREETING")
    await _past_grace()

    await handler.handle_audio(_tone(600))
    assert handler._turn_state == "CALLER_SPEAKING"
    clearer.assert_awaited_once()
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_turn_metrics_are_logged_for_each_stage(mock_db_session):
    voice = _voice("Hello")
    ai = _ai("Hi.")
    handler, _, _ = await _handler(voice, ai, mock_db_session)

    with patch("app.voice.turn_metrics.log_call_event") as log_event:
        await handler.handle_audio(_utterance())
        await _wait_for(lambda: log_event.call_count >= 1 and handler._turn_state == "LISTENING")

    fields = log_event.call_args.kwargs
    assert fields["outcome"] == "completed"
    for key in (
        "stt_ms",
        "end_of_turn_to_stt_final_ms",
        "stt_final_to_llm_first_token_ms",
        "llm_first_token_to_first_audio_ms",
        "tts_startup_ms",
        "end_of_turn_to_first_audio_ms",
    ):
        assert fields[key] is not None and fields[key] >= 0, key
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_hello_at_pickup_does_not_cancel_the_greeting(mock_db_session):
    """Live call 2026-09-30: the caller said "hello" while the greeting was
    still being synthesized; the greeting was cancelled and the caller heard
    silence. The greeting must always play."""
    tts_started = asyncio.Event()
    release_tts = asyncio.Event()

    async def slow_synthesize(text, lang):
        if "Welcome to Kinikh Group" in text:  # the greeting: slow, and audibly non-silent
            tts_started.set()
            await release_tts.wait()
            return _tone(400, amplitude=1234)
        return _assistant_audio(0.4)  # any other reply: silence

    voice = _voice("Hello")
    voice.synthesize = AsyncMock(side_effect=slow_synthesize)
    handler, sender, _ = await _handler(voice, _ai("Hi, how can I help?"), mock_db_session)
    handler._start_response("greeting", handler._run_greeting, "GREETING")

    await asyncio.wait_for(tts_started.wait(), 1)
    await handler.handle_audio(_tone(600))  # "hello" before the greeting is audible
    release_tts.set()
    await _wait_for(lambda: sender.await_count > 0)

    await asyncio.sleep(0.1)
    first_frame = sender.await_args_list[0].args[0]
    assert any(first_frame), "the greeting's audio must actually be sent to the caller"
    assert handler._context.messages[0].role == "assistant"
    assert "Welcome to Kinikh Group" in handler._context.messages[0].content
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_caller_who_keeps_talking_still_gets_a_reply(mock_db_session):
    """Live call 2026-09-30: every new "hello?" cancelled the pending reply,
    so the caller never heard anything. After one merge the reply is kept."""
    stt_gate = asyncio.Event()

    async def transcribe(wav, language):
        await stt_gate.wait()
        return "hello"

    voice = _voice()
    voice.transcribe = AsyncMock(side_effect=transcribe)
    ai = _ai("Hi! How can I help you today?")
    handler, sender, _ = await _handler(voice, ai, mock_db_session)

    for _ in range(2):  # caller keeps saying hello while STT is still running
        await handler.handle_audio(_utterance(400))
        await asyncio.sleep(0.02)
    cancelled = []
    original_cancel = handler.cancel_active_response

    def counting_cancel(reason):
        cancelled.append(reason)
        original_cancel(reason)

    handler.cancel_active_response = counting_cancel
    for _ in range(3):  # ...and keeps going
        await handler.handle_audio(_utterance(400))
        await asyncio.sleep(0.02)
    stt_gate.set()
    await _wait_for(lambda: ai.chat.await_count >= 1 and sender.await_count > 0)
    # The pending reply may be merged away once, never on every new "hello".
    assert cancelled.count("caller_resumed") == 0
    await handler.mark_stopped()
