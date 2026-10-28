"""Event-gated checks for latency before the first useful audio; no APIs."""

import asyncio
from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import ConversationEngine
from app.ai.openai_client import OpenAIClient
from app.core.config import get_settings
from app.voice.elevenlabs import ElevenLabsClient
from app.voice import VoiceManager
from app.voice.pcm import apply_fade
from app.voice.speech_handler import SpeechHandler, _Response
from app.voice.transcript_filter import classify_turn_end
from app.voice.turn_metrics import TurnMetrics


@pytest.mark.parametrize("text", ["Yes.", "No", "haan", "हाँ जी", "theek hai", "Okay", "नहीं"])
def test_complete_short_answers_do_not_add_continuation_wait(text):
    assert classify_turn_end(text)[0] == "reply"


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed,expected_wait", [(1.5, None), (0.5, 0.7)])
async def test_stt_time_counts_toward_continuation_grace(mock_db_session, elapsed, expected_wait):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    response = _Response(
        turn_id=1,
        kind="caller_turn",
        metrics=TurnMetrics(call_sid="LATENCY", turn_id=1, speech_end=10 - elapsed),
    )
    with (
        patch.object(handler._settings, "unfinished_turn_extra_ms", 1200),
        patch.object(TurnMetrics, "now", return_value=10),
        patch("app.voice.speech_handler.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        await handler._wait_if_unfinished(response, "I need a flat but")
    if expected_wait is None:
        sleep.assert_not_awaited()
    else:
        assert sleep.await_args.args[0] == pytest.approx(expected_wait)
    assert not response.awaiting_continuation


@pytest.mark.asyncio
async def test_audio_stays_streamed_with_exotel_minimum_frame_size(mock_db_session):
    release = asyncio.Event()
    consumed = asyncio.Event()
    voice = VoiceManager.__new__(VoiceManager)
    original = b"\x01\x00" * 4000

    async def stream(*args, **kwargs):
        yield original[:1024]
        consumed.set()
        await release.wait()
        yield original[1024:]

    voice.synthesize_to_stream = stream
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=AsyncMock())
    with patch.object(handler._settings, "tts_streaming_enabled", True):
        async with aclosing(handler._synthesize("Our team can help.", "en")) as frames:
            next_frame = asyncio.create_task(anext(frames))
            await asyncio.wait_for(consumed.wait(), 0.2)
            assert not next_frame.done()  # Below Exotel's 3200-byte minimum.
            release.set()
            first = await asyncio.wait_for(next_frame, 0.2)
            assert len(first) == 3200
            audio = first + b"".join([frame async for frame in frames])
    assert audio[: len(original)] == apply_fade(original, fade_in=True, fade_out=True)
    assert all(byte == 0 for byte in audio[len(original) :])


@pytest.mark.asyncio
async def test_elevenlabs_does_not_wait_to_fill_a_4096_byte_http_buffer():
    release = asyncio.Event()

    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x01\x00" * 1024  # 64ms at the provider's 16kHz rate.
            await release.wait()
            yield b"\x01\x00" * 1024

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=AudioStream()))
    ) as http:
        client = ElevenLabsClient()
        client._http = http
        async with aclosing(client.synthesize_stream("Our team can help.")) as stream:
            first = await asyncio.wait_for(anext(stream), 0.2)
            assert first
            release.set()
            assert [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_live_model_override_keeps_post_call_model_unchanged(monkeypatch):
    settings = get_settings()
    monkeypatch.setitem(settings.__dict__, "openai_live_model", "gpt-4.1-mini")
    client = OpenAIClient.__new__(OpenAIClient)
    client._model = "post-call-model"
    client._temperature = 0.2
    client._max_tokens = 1024

    async def stream():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="Our team can help."))])

    client._raw = MagicMock()
    client._raw.chat.completions.create = AsyncMock(return_value=stream())
    engine = ConversationEngine([], client=client)
    ctx = CallContext(call_sid="LATENCY", caller_number="")
    ctx.add_message("user", "I need a website")
    assert [s async for s in engine.stream_reply(ctx, first_token_timeout=1, rag_context="")]
    payload = client._raw.chat.completions.create.call_args.kwargs
    assert payload["model"] == "gpt-4.1-mini"
    assert "reasoning_effort" not in payload
    assert client._model == "post-call-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat", "stream_chat"])
async def test_rejected_reasoning_setting_is_not_retried_on_later_turns(monkeypatch, method):
    import app.ai.openai_client as module

    model = "test-no-reasoning"
    monkeypatch.setattr(module, "_reasoning_effort_supported", {})
    client = OpenAIClient.__new__(OpenAIClient)
    client._model, client._temperature, client._max_tokens = model, 0.2, 1024

    async def chunks():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello."))])

    async def create(**kwargs):
        if "reasoning_effort" in kwargs:
            raise openai.BadRequestError(
                "Unsupported parameter: reasoning_effort",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
                body={"error": {"param": "reasoning_effort"}},
            )
        if method == "stream_chat":
            return chunks()
        return MagicMock(choices=[MagicMock(message=MagicMock(content="Hello."))])

    client._raw = MagicMock()
    client._raw.chat.completions.create = AsyncMock(side_effect=create)
    for _ in range(3):
        if method == "stream_chat":
            assert [chunk async for chunk in client.stream_chat([], reasoning_effort="minimal")]
        else:
            assert await client.chat([], reasoning_effort="minimal")
    assert client._raw.chat.completions.create.await_count == 4  # Three turns + one probe.
    assert module._reasoning_effort_supported[model] is False
