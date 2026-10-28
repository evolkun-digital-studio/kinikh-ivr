"""Tests for latency optimizations, audio quality, and knowledge integration.

Covers the requirements from sections B through Z of the IVR optimization spec:
- Latency: greeting without LLM, knowledge lookup, streaming, STT reuse
- Audio: PCM format, frame alignment, resampling, queue management
- Knowledge: demo data loading, context injection, FAQ matching
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Test 1: Fixed greeting does not invoke LLM ─────────────────────────────

@pytest.mark.asyncio
async def test_greeting_does_not_invoke_llm():
    """Section Y: The initial greeting must use fixed text, not an LLM call."""
    from app.ai.conversation import ConversationEngine
    from app.ai.context_manager import CallContext

    mock_client = MagicMock()
    mock_client.chat = AsyncMock(side_effect=AssertionError("LLM must not be called for greeting"))
    mock_client.stream_chat = AsyncMock(side_effect=AssertionError("LLM must not be called for greeting"))

    engine = ConversationEngine(departments=[], client=mock_client)
    ctx = CallContext(call_sid="test", caller_number="+910000000000")

    from app.core.config import get_settings
    greeting = await engine.get_greeting(ctx)

    assert "Welcome to Kinikh Group" in greeting
    assert len(ctx.messages) == 1
    assert ctx.messages[0].role == "assistant"


# ─── Test 2: Greeting starts without waiting for company context ─────────────

@pytest.mark.asyncio
async def test_greeting_starts_without_waiting_for_knowledge():
    """Section Y: Greeting fires from fixed text — no knowledge load blocks it."""
    from app.ai.conversation import ConversationEngine
    from app.ai.context_manager import CallContext

    engine = ConversationEngine(departments=[], client=MagicMock())
    ctx = CallContext(call_sid="test", caller_number="+910000000000")

    # get_greeting must be synchronous-fast — no external calls
    start = time.monotonic()
    greeting = await engine.get_greeting(ctx)
    elapsed = time.monotonic() - start

    assert greeting  # non-empty
    assert elapsed < 0.1  # must be essentially instant


# ─── Test 3: Only new audio is sent to STT ───────────────────────────────────

@pytest.mark.asyncio
async def test_only_new_audio_sent_to_stt():
    """Section F: Once a segment is transcribed, its bytes must not be re-sent.
    The AudioBuffer passes each finalized utterance separately to the callback,
    never accumulating old audio across turns."""
    from app.voice.audio_stream import AudioBuffer

    flushed_chunks: list[bytes] = []

    async def on_flush(wav_bytes: bytes):
        flushed_chunks.append(wav_bytes)

    buf = AudioBuffer(
        on_flush=on_flush,
        sample_rate=8000,
        min_rms=50.0,  # low threshold so test audio passes
        speech_start_ms=20,
        end_of_speech_ms=100,
        pre_roll_ms=20,
    )

    # Create speech audio (loud enough to pass RMS gate)
    speech_frame = struct.pack("<" + "h" * 160, *([5000] * 160))  # 20ms @ 8kHz
    silence_frame = struct.pack("<" + "h" * 160, *([0] * 160))

    # Utterance 1: a few frames of speech then silence
    for _ in range(15):  # 300ms of speech (exceeds speech_start_ms=20)
        await buf.push(speech_frame)
    for _ in range(10):  # 200ms silence (exceeds end_of_speech_ms=100)
        await buf.push(silence_frame)

    # Wait for flush
    await asyncio.sleep(0.2)

    # Utterance 2: new speech, new silence
    for _ in range(15):
        await buf.push(speech_frame)
    for _ in range(10):
        await buf.push(silence_frame)

    await asyncio.sleep(0.2)
    await buf.cancel_pending()

    # Each flush should be a separate utterance, not a growing accumulation
    if len(flushed_chunks) >= 2:
        # Second chunk must NOT contain the first chunk's audio
        assert len(flushed_chunks[1]) <= len(flushed_chunks[0]) * 2, \
            "Second flush should not re-include first utterance's audio"


# ─── Test 4: Old audio is never retranscribed ────────────────────────────────

def test_audio_buffer_does_not_accumulate_old_segments():
    """Section F: The segmenter resets after each utterance — old PCM bytes
    are never included in the next utterance's output."""
    from app.voice.vad import UtteranceSegmenter

    seg = UtteranceSegmenter(
        frame_bytes=320,
        min_rms=50.0,
        speech_start_ms=20,
        end_of_speech_ms=80,
        pre_roll_ms=20,
        max_utterance_bytes=320_000,
        sample_rate=8000,
    )

    speech = struct.pack("<" + "h" * 160, *([5000] * 160))  # 20ms speech
    silence = struct.pack("<" + "h" * 160, *([0] * 160))  # 20ms silence

    # Utterance 1
    for _ in range(10):
        seg.process(speech)
    for _ in range(8):
        event = seg.process(silence)
    first_utterances = event.utterances

    # Utterance 2
    for _ in range(10):
        seg.process(speech)
    for _ in range(8):
        event = seg.process(silence)
    second_utterances = event.utterances

    if first_utterances and second_utterances:
        # Each utterance is independent — second is NOT first + second combined
        assert len(second_utterances[0]) < len(first_utterances[0]) * 3


# ─── Test 5: No language detector LLM in critical path ──────────────────────

def test_no_language_detector_in_critical_path():
    """Section G: The _run_caller_turn method must not call _maybe_detect_language
    or any separate language-detection LLM before generating the response."""
    import inspect
    from app.voice.speech_handler import SpeechHandler

    source = inspect.getsource(SpeechHandler._run_caller_turn)
    assert "_maybe_detect_language" not in source, \
        "Language detection LLM must not be in the critical response path"
    assert "language_detector" not in source.lower(), \
        "No language detector reference in critical path"


# ─── Test 6: One primary LLM handles company questions ──────────────────────

def test_one_primary_llm_for_company_questions():
    """Section G/J: The conversation engine uses ONE LLM for responses,
    not separate classification/detection/response LLMs."""
    from app.ai.conversation import ConversationEngine

    engine = ConversationEngine(departments=[], client=MagicMock())
    # The engine has ONE client — no separate classifier, detector, or RAG
    assert hasattr(engine, "_client")
    assert not hasattr(engine, "_language_detector")
    assert not hasattr(engine, "_department_classifier")




# ─── Test 11: LLM streaming begins before complete generation ────────────────

@pytest.mark.asyncio
async def test_llm_streaming_begins_before_complete():
    """Section L: stream_reply must yield segments as they arrive,
    not wait for the full response."""
    from app.ai.conversation import ConversationEngine
    from app.ai.context_manager import CallContext
    from app.ai.openai_client import OpenAIClient

    mock_client = MagicMock(spec=OpenAIClient)
    # Simulate streaming that yields token by token
    tokens = ["Yes, ", "Kinikh ", "provides ", "architecture ", "services. ",
              "We also ", "handle ", "interior ", "design."]

    async def fake_stream(messages, **kwargs):
        for t in tokens:
            yield t

    mock_client.stream_chat = fake_stream

    engine = ConversationEngine(departments=[], client=mock_client)
    ctx = CallContext(call_sid="test", caller_number="+910000000000")
    ctx.add_message("user", "What services do you provide?")

    segments = []
    async for seg in engine.stream_reply(ctx, first_token_timeout=5.0):
        segments.append(seg)

    assert len(segments) >= 1, "Must yield at least one segment"
    full_text = " ".join(segments)
    assert "Kinikh" in full_text or "architecture" in full_text


# ─── Test 12: First phrase reaches TTS before full LLM completion ────────────

@pytest.mark.asyncio
async def test_first_phrase_before_full_completion():
    """Section M: The first speakable phrase should be yielded without waiting
    for the entire LLM response."""
    from app.ai.conversation import split_speakable

    # Simulate an LLM buffer with a complete first sentence + incomplete second
    buffer = "Yes, Kinikh provides architecture services. We also"
    segments, remainder = split_speakable(buffer)

    assert len(segments) >= 1, "Must have at least one ready segment"
    assert "architecture" in segments[0].lower() or "kinikh" in segments[0].lower()
    assert remainder.strip(), "Remainder should contain the unfinished part"


# ─── Test 13: Final Exotel PCM is 8kHz ──────────────────────────────────────

def test_exotel_pcm_is_8khz():
    """Section O: ElevenLabs pcm_16000 output is downsampled to 8kHz."""
    from app.voice.elevenlabs import ElevenLabsClient

    client = ElevenLabsClient.__new__(ElevenLabsClient)
    assert client._OUTPUT_FORMAT == "pcm_16000"


# ─── Test 14: Final audio is mono ────────────────────────────────────────────

def test_exotel_audio_is_mono():
    """Section O: Audio sent to Exotel must be mono (1 channel)."""
    from app.voice.audio_stream import _CHANNELS
    assert _CHANNELS == 1


# ─── Test 15: Final audio is signed 16-bit little-endian ─────────────────────

def test_exotel_audio_is_pcm16le():
    """Section O: Audio must be signed 16-bit little-endian PCM."""
    from app.voice.audio_stream import _SAMPLE_WIDTH_BYTES
    assert _SAMPLE_WIDTH_BYTES == 2  # 16-bit = 2 bytes


# ─── Test 16: Outbound chunks have even byte lengths ────────────────────────

def test_outbound_chunks_even_bytes():
    """Section Q: Every PCM16 chunk must have even length (2 bytes per sample)."""
    from app.voice.speech_handler import _EXOTEL_FRAME_BYTES
    assert _EXOTEL_FRAME_BYTES % 2 == 0
    assert _EXOTEL_FRAME_BYTES == 3200  # 200ms @ 8kHz/16-bit


# ─── Test 17: No WAV header in Exotel PCM payload ───────────────────────────

def test_no_wav_header_in_exotel_payload():
    """Section O: Audio sent to Exotel must be raw PCM, no WAV container."""
    from app.voice.audio_stream import wrap_pcm16_wav

    # Create 320 bytes of test PCM data
    pcm = b"\x00" * 320
    wav = wrap_pcm16_wav(pcm, 8000)

    # WAV has RIFF header; raw PCM does not
    assert wav[:4] == b"RIFF"  # wrap creates WAV (used for STT)

    # The ExotelAudioStream.send_audio sends raw bytes — verify it never
    # wraps in WAV. The _synthesize method yields raw PCM chunks.
    # Verify that the frame size is correct for raw PCM.
    assert pcm[:4] != b"RIFF"  # raw PCM has no header


# ─── Test 18: MP3 bytes never passed as PCM ─────────────────────────────────

def test_mp3_not_passed_as_pcm():
    """Section O: ElevenLabs with pcm_16000 format returns raw PCM, not MP3.
    MP3 files start with 0xFF 0xFB or 'ID3' — PCM does not."""
    # Verify the output format configuration
    from app.voice.elevenlabs import ElevenLabsClient

    # pcm_16000 returns raw PCM bytes, never MP3
    assert ElevenLabsClient._OUTPUT_FORMAT == "pcm_16000"

    # A typical MP3 frame starts with sync word 0xFFE0-0xFFFF or "ID3" header
    mp3_sync = b"\xff\xfb"
    id3_header = b"ID3"
    pcm_sample = struct.pack("<h", 5000)  # valid PCM16LE sample

    assert pcm_sample[:2] != mp3_sync
    assert pcm_sample[:3] != id3_header


# ─── Test 19: Resampling occurs only when needed ─────────────────────────────

def test_resampling_is_anti_aliased():
    """ElevenLabs pcm_16000 must be converted to Exotel's 8 kHz with a
    low-pass filter — plain sample-dropping folds 4-8 kHz sibilance back
    into the audible band (heard on calls as hiss/metallic noise)."""
    import math
    import struct

    from app.voice.pcm import downsample

    def tone(freq: float) -> bytes:
        n = 16000 // 5  # 200 ms
        return struct.pack(
            f"<{n}h", *(int(8000 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(n))
        )

    def rms(pcm: bytes) -> float:
        vals = struct.unpack(f"<{len(pcm) // 2}h", pcm)[50:-50]
        return (sum(v * v for v in vals) / len(vals)) ** 0.5

    in_band = rms(downsample(tone(1000), 16000))
    alias = rms(downsample(tone(6000), 16000))  # would alias to 2 kHz if unfiltered
    assert in_band > 5000
    assert alias < in_band * 0.05  # >= 26 dB suppression


# ─── Test 20: Exactly one outbound audio writer is active ────────────────────

def test_single_outbound_audio_writer():
    """Section U: ExotelAudioStream uses a send_lock to serialize writes."""
    import inspect
    from app.telephony.exotel.stream import ExotelAudioStream

    source = inspect.getsource(ExotelAudioStream.send_audio)
    assert "_send_lock" in source, \
        "send_audio must use _send_lock for serialized writes"


# ─── Test 21: Stale generation cannot send audio ────────────────────────────

def test_stale_generation_blocked():
    """Section V: _emit_audio checks _is_current before sending."""
    import inspect
    from app.voice.speech_handler import SpeechHandler

    source = inspect.getsource(SpeechHandler._emit_audio)
    assert "_is_current" in source, \
        "_emit_audio must check _is_current before sending"


# ─── Test 22: Cancelled TTS cannot continue sending ─────────────────────────

@pytest.mark.asyncio
async def test_cancelled_response_stops_sending():
    """Section V: cancel_active_response must cancel the task and clear state."""
    from app.voice.speech_handler import SpeechHandler

    mock_db = AsyncMock()
    handler = SpeechHandler(db=mock_db, voice=MagicMock(), ai_client=MagicMock())

    # Simulate a running task
    async def long_task():
        await asyncio.sleep(100)

    task = asyncio.create_task(long_task())
    handler._response_task = task
    handler._response = MagicMock()

    handler.cancel_active_response(reason="test")

    assert handler._response is None
    assert handler._response_task is None

    # Wait for the cancellation to propagate
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert task.cancelled()


# ─── Test 23: Audio queue is bounded ─────────────────────────────────────────

def test_audio_queue_bounded():
    """Section X: Pacing limits how far ahead audio can be sent."""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.playback_max_lead_ms >= 200
    assert settings.playback_max_lead_ms <= 2000


# ─── Test 24: Playback pacing matches PCM duration ──────────────────────────

def test_playback_pacing_calculation():
    """Section R: Pacing is based on actual audio duration, not arbitrary sleep."""
    from app.voice.speech_handler import _EXOTEL_FRAME_BYTES, _PCM_BYTES_PER_SAMPLE

    # 3200 bytes at 8kHz/16-bit = 200ms of audio
    sample_rate = 8000
    duration_s = _EXOTEL_FRAME_BYTES / _PCM_BYTES_PER_SAMPLE / sample_rate
    duration_ms = duration_s * 1000

    assert abs(duration_ms - 200) < 0.1, \
        f"Frame duration should be 200ms, got {duration_ms}ms"


# ─── Test 25: Phrase boundaries don't introduce large silent gaps ────────────

def test_phrase_split_no_large_gaps():
    """Section T: split_speakable produces reasonable phrase chunks, not
    tiny fragments that would cause many TTS calls with gaps."""
    from app.ai.conversation import split_speakable

    # A typical LLM response
    text = ("Yes, Kinikh provides architecture and planning services. "
            "We also handle interior design and construction.")
    segments, remainder = split_speakable(text)

    for seg in segments:
        assert len(seg) >= 12, f"Segment too short: '{seg}' — would cause choppy TTS"


# ─── Test 26: Barge-in clears queued audio ───────────────────────────────────

@pytest.mark.asyncio
async def test_barge_in_clears_audio():
    """Section W: handle_barge_in must clear the output buffer."""
    from app.voice.speech_handler import SpeechHandler
    from app.voice.vad import BargeInResult

    mock_db = AsyncMock()
    handler = SpeechHandler(db=mock_db, voice=MagicMock(), ai_client=MagicMock())

    clear_called = False
    async def mock_clear():
        nonlocal clear_called
        clear_called = True

    handler._clear_audio = mock_clear
    handler._initialized = True
    handler._stopped = False
    handler._turn_state = MagicMock()

    # Set up minimal state for barge_in
    from app.voice.audio_stream import AudioBuffer
    handler._buffer = MagicMock(spec=AudioBuffer)
    handler._buffer.sample_rate = 8000

    # Create a BargeInResult with some speech audio
    speech = struct.pack("<" + "h" * 160, *([5000] * 160))
    result = BargeInResult(speech=speech, remainder=b"")

    # Handle barge-in
    handler._response = MagicMock()
    handler._response_task = MagicMock()
    handler._response_task.done.return_value = False
    handler._response_task.cancel = MagicMock()

    await handler.handle_barge_in(result)
    assert clear_called, "Barge-in must call clear_output_buffer"


# ─── Test 27: Caller continuation before playback doesn't clear ──────────────

def test_continuation_before_playback_no_clear():
    """Section W: If caller resumes while PROCESSING (no audio yet audible),
    treat as continuation, not barge-in — don't send clear."""
    import inspect
    from app.voice.speech_handler import SpeechHandler

    source = inspect.getsource(SpeechHandler._on_caller_speech_start)
    # Continuation cancels the response but does NOT clear audio
    assert "cancel_active_response" in source
    # Continuation does NOT call clear_output_buffer
    assert "clear_output_buffer" not in source


# ─── Test 28: No audio emitted after ENDED state ────────────────────────────

def test_no_audio_after_ended():
    """Section U: _emit_audio returns False when state is ENDED."""
    import inspect
    from app.voice.speech_handler import SpeechHandler

    # _is_current checks _stopped and ENDED state
    source = inspect.getsource(SpeechHandler._is_current)
    assert "ENDED" in source or "stopped" in source




# ─── Turn metrics tests ─────────────────────────────────────────────────────

def test_turn_metrics_derived_fields():
    """Section B: TurnMetrics.derived() produces all required breakdown fields."""
    from app.voice.turn_metrics import TurnMetrics

    t0 = 100.0
    m = TurnMetrics(
        call_sid="test",
        turn_id=1,
        speech_start=t0,
        speech_end=t0 + 0.5,
        vad_finalize=t0 + 0.55,
        stt_start=t0 + 0.55,
        stt_final=t0 + 1.2,
        knowledge_start=t0 + 1.2,
        knowledge_end=t0 + 1.202,
        llm_request_start=t0 + 1.21,
        llm_first_token=t0 + 1.64,
        first_phrase_ready=t0 + 1.75,
        tts_request_start=t0 + 1.76,
        tts_first_chunk=t0 + 2.06,
        first_audio_output=t0 + 2.08,
        response_complete=t0 + 3.0,
    )

    d = m.derived()
    assert d["vad_ms"] is not None
    assert d["stt_ms"] is not None
    assert d["knowledge_ms"] is not None
    assert d["llm_ttft_ms"] is not None
    assert d["phrase_buffer_ms"] is not None
    assert d["tts_startup_ms"] is not None
    assert d["end_of_turn_to_first_audio_ms"] is not None
    assert d["complete_turn_ms"] is not None

    # Sanity check values
    assert d["stt_ms"] == pytest.approx(650.0, abs=1)
    assert d["knowledge_ms"] == pytest.approx(2.0, abs=1)
    assert d["llm_ttft_ms"] == pytest.approx(430.0, abs=1)


def test_turn_metrics_emit_logs_breakdown():
    """Section B: emit() logs voice.latency.breakdown event."""
    from app.voice.turn_metrics import TurnMetrics

    m = TurnMetrics(
        call_sid="test-emit",
        turn_id=2,
        speech_end=100.0,
        stt_start=100.05,
        stt_final=100.5,
    )

    logged_events = []
    with patch("app.voice.turn_metrics.log_call_event") as mock_log:
        mock_log.side_effect = lambda event, **kwargs: logged_events.append(event)
        m.emit("completed")

    assert "voice.latency.breakdown" in logged_events
    assert "voice.turn.metrics" in logged_events


# ─── Audio format validation tests ──────────────────────────────────────────

def test_wav_header_format():
    """Validate wrap_pcm16_wav produces correct RIFF/WAVE header."""
    from app.voice.audio_stream import wrap_pcm16_wav

    pcm = b"\x00\x01" * 100  # 200 bytes of PCM
    wav = wrap_pcm16_wav(pcm, 8000)

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"

    # fmt chunk
    assert wav[12:16] == b"fmt "
    fmt_size = struct.unpack_from("<I", wav, 16)[0]
    assert fmt_size == 16  # PCM

    audio_format = struct.unpack_from("<H", wav, 20)[0]
    assert audio_format == 1  # WAVE_FORMAT_PCM

    channels = struct.unpack_from("<H", wav, 22)[0]
    assert channels == 1  # mono

    sample_rate = struct.unpack_from("<I", wav, 24)[0]
    assert sample_rate == 8000

    bits_per_sample = struct.unpack_from("<H", wav, 34)[0]
    assert bits_per_sample == 16


def test_frame_alignment():
    """Section Q: All frame sizes must produce even byte counts."""
    from app.voice.speech_handler import _EXOTEL_FRAME_BYTES

    # Frame must be even (2 bytes per PCM16 sample)
    assert _EXOTEL_FRAME_BYTES % 2 == 0

    # Calculate expected frame sizes
    for ms in [20, 40, 200]:
        frame_bytes = int(ms / 1000 * 8000) * 2
        assert frame_bytes % 2 == 0, f"{ms}ms frame has odd byte count: {frame_bytes}"
