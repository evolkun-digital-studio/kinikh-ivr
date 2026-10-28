"""Regression tests for the call-quality fixes: noise gate, STT hallucination
filter, audio conversion, language tracking, silence handling, fixed URL."""

from __future__ import annotations

import asyncio
import math
import random
import struct
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings


def _tone(ms: int, amplitude: int, freq: float = 300.0, rate: int = 8000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(
        f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))
    )


def _noise(ms: int, amplitude: int, rate: int = 8000, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(rng.randint(-amplitude, amplitude) for _ in range(n)))


# ── Adaptive noise gate ─────────────────────────────────────────────────────


def _segmenter(noise_floor):
    from app.voice.vad import UtteranceSegmenter

    return UtteranceSegmenter(
        frame_bytes=320,
        min_rms=150,
        speech_start_ms=200,
        end_of_speech_ms=700,
        pre_roll_ms=250,
        max_utterance_bytes=320_000,
        noise_floor=noise_floor,
    )


def test_steady_background_noise_is_not_treated_as_speech():
    """Noise loud enough to cross the fixed threshold (RMS ~300) used to be
    detected as the caller talking; with the adaptive gate it is not."""
    from app.voice.vad import NoiseFloor

    floor = NoiseFloor(multiplier=3.0)
    # The floor is learned during the greeting (barge-in monitor frames).
    for i in range(50):
        floor.update(300.0 + (i % 7) * 10)
    seg = _segmenter(floor)
    for i in range(30):  # 6 s of continuous noise (RMS ~300), 200 ms chunks
        event = seg.process(_noise(200, 520, seed=i))
        assert not event.speech_started
        assert not event.utterances
    # Without the adaptive gate the same noise IS detected as speech.
    fixed = _segmenter(None)
    assert any(fixed.process(_noise(200, 520, seed=i)).speech_started for i in range(5))


def test_real_speech_is_still_detected_over_background_noise():
    from app.voice.vad import NoiseFloor

    seg = _segmenter(NoiseFloor(multiplier=3.0))
    for i in range(10):  # 2 s of background noise to learn the floor
        seg.process(_noise(200, 520, seed=i))
    event = seg.process(_tone(400, 6000))
    assert event.speech_started


def test_quiet_line_keeps_fixed_threshold():
    from app.voice.vad import NoiseFloor

    floor = NoiseFloor(multiplier=3.0)
    for _ in range(100):
        floor.update(20.0)
    assert floor.threshold(150) == 150  # never MORE strict than before on a quiet line


def test_speech_does_not_raise_the_noise_floor():
    from app.voice.vad import NoiseFloor

    floor = NoiseFloor(multiplier=3.0)
    for i in range(200):  # continuous talking with natural short gaps
        floor.update(3000.0 if i % 10 < 8 else 30.0)
    assert floor.value < 100


# ── STT hallucination filter ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["you", "...", "Thank you for watching!", "Subtitles by the Amara.org community", "सब्सक्राइब"],
)
def test_known_hallucinations_are_rejected(text):
    from app.voice.transcript_filter import is_hallucination

    assert is_hallucination(text)


@pytest.mark.parametrize(
    "text", ["Thank you.", "haan", "हाँ", "ok", "bye", "I need interior design", "नमस्ते"]
)
def test_real_short_answers_are_kept(text):
    from app.voice.transcript_filter import is_hallucination

    assert not is_hallucination(text)


# ── Audio conversion ───────────────────────────────────────────────────────


def test_downsampler_streaming_matches_one_shot():
    """Chunk boundaries (including odd byte splits) must not change output."""
    from app.voice.pcm import Downsampler, downsample

    rate = 16000
    n = 3200
    pcm = struct.pack(
        f"<{n}h", *(int(9000 * math.sin(2 * math.pi * 700 * i / rate)) for i in range(n))
    )
    one_shot = downsample(pcm, 16000)
    ds = Downsampler(16000)
    out = b""
    for cut in range(0, len(pcm), 777):  # odd chunk size splits samples
        out += ds.process(pcm[cut : cut + 777])
    out += ds.flush()
    assert out == one_shot


def test_sanitize_rejects_compressed_audio():
    from app.voice.pcm import sanitize_pcm16

    assert sanitize_pcm16(b"ID3\x04" + b"\x00" * 100) == b""
    assert sanitize_pcm16(b"\xff\xfb\x90\x44" + b"\x00" * 100) == b""
    assert sanitize_pcm16(b"RIFF" + b"\x00" * 100) == b""
    assert sanitize_pcm16(b"\x01\x00\x02") == b"\x01\x00"


# ── Language tracking ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I want to build a house", "en"),
        ("Mujhe ghar banana hai", "hi"),  # all-Hindi words, Roman script
        ("Can you send it to me", "en"),  # "to"/"me" are not Hindi markers
        ("मुझे घर बनाना है", "hi"),
        ("मुझे interior design चाहिए", "hi-en"),
        ("Aap log renovation karte ho?", "hi-en"),
    ],
)
def test_quick_language_detection(text, expected):
    from app.ai.language_detector import quick_detect

    assert quick_detect(text) == expected


def test_short_english_reply_does_not_flip_hindi_call(mock_db_session):
    from app.ai.context_manager import CallContext
    from app.voice.speech_handler import SpeechHandler

    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    handler._context = CallContext(call_sid="LANG", caller_number="")
    handler._update_language("मुझे घर बनाना है")
    assert handler._context.language == "hi"
    handler._update_language("ok")
    assert handler._context.language == "hi"
    handler._update_language("Actually I want to talk about an office renovation project")
    assert handler._context.language == "en"  # Clear English turn exceeds 70%.
    handler._update_language("And how much would that cost?")
    assert handler._context.language == "en" # Remains English.


# ── Silence handling ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_silent_caller_is_reprompted_then_call_ends(mock_db_session):
    from app.services.department_service import DepartmentService
    from app.voice.speech_handler import SpeechHandler

    settings = get_settings()
    voice = AsyncMock()
    voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 400)
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=AsyncMock())
    handler.set_audio_sender(AsyncMock())
    hangup = AsyncMock()
    handler.set_hangup(hangup)

    with (
        patch.object(settings, "silence_reprompt_seconds", 0.05),
        patch.object(settings, "max_silence_reprompts", 1),
        patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])),
    ):
        await handler._ensure_initialized("SILENT", "+919876543210")
        for _ in range(200):
            if hangup.await_count:
                break
            await asyncio.sleep(0.02)

    spoken = [c.args[0] for c in voice.synthesize.call_args_list]
    assert any("able to hear me" in t.lower() for t in spoken)
    assert any("thank you for contacting किनिख group" in t.lower() for t in spoken)
    hangup.assert_awaited_once()
    await handler.mark_stopped()


# ── Fixed public URL ───────────────────────────────────────────────────────


def test_stream_url_uses_public_base_url():
    from starlette.requests import Request

    from app.api.v1.telephony import _build_stream_url

    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
        "path": "/api/v1/telephony/incoming",
        "headers": [(b"host", b"127.0.0.1:8000")],
        "query_string": b"",
    }
    settings = get_settings()
    with patch.object(settings, "public_base_url", "https://ivr.example.com/"):
        url = _build_stream_url(Request(scope), "SID123")
    assert url == "wss://ivr.example.com/api/v1/telephony/stream/SID123"
