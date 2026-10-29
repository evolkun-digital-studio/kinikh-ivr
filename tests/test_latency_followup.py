"""Regression coverage for the live-call latency follow-up."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import ConversationEngine, split_speakable
from app.ai.openai_client import OpenAIClient
from app.core.config import get_settings
from app.voice.audio_stream import wrap_pcm16_wav
from app.voice.speech_handler import (
    SpeechHandler,
    TurnState,
    _FILLER_AUDIO_CACHE,
    _Response,
)
from app.voice.turn_metrics import TurnMetrics


@pytest.mark.asyncio
async def test_live_llm_uses_summary_rag_last_eight_and_minimal_reasoning(monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_live_model", "")
    monkeypatch.setattr(get_settings(), "openai_model", "gpt-5-test")
    captured: dict = {}

    async def stream_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        yield "Here is the answer you requested."

    client = OpenAIClient.__new__(OpenAIClient)
    client.stream_chat = stream_chat
    client.chat = AsyncMock(return_value="fallback")
    ctx = CallContext(call_sid="CA_BOUND", caller_number="+91000")
    for index in range(12):
        ctx.add_message("user" if index % 2 == 0 else "assistant", f"message {index}")
    await ctx.refresh_running_summary()

    engine = ConversationEngine([], client=client)
    result = [
        part
        async for part in engine.stream_reply(
            ctx, first_token_timeout=1, rag_context="RAG FACT"
        )
    ]

    assert result == ["Here is the answer you requested."]
    assert len(captured["messages"]) == 9  # one system prompt + eight messages
    assert [m["content"] for m in captured["messages"][1:]] == [
        f"message {index}" for index in range(4, 12)
    ]
    assert "RAG FACT" in captured["messages"][0]["content"]
    assert "EARLIER CONVERSATION SUMMARY" in captured["messages"][0]["content"]
    assert captured["reasoning_effort"] == "minimal"
    assert len(ctx.messages) == 12  # post-call transcript remains complete


def test_complete_sentence_flushes_without_splitting_at_commas():
    text = "We can help with that, and keep streaming."
    ready, rest = split_speakable(text)
    assert ready == [text]
    assert rest == ""


@pytest.mark.asyncio
async def test_stt_fallback_only_for_long_empty_speech(monkeypatch):
    client = OpenAIClient()
    calls: list[str] = []

    async def create(**kwargs):
        calls.append(str(kwargs["model"]))
        response = MagicMock()
        response.text = "" if len(calls) == 1 else "fallback transcript"
        return response

    monkeypatch.setattr(client._raw.audio.transcriptions, "create", create)
    long_wav = wrap_pcm16_wav(b"\x00\x00" * 8_000, 8_000)
    assert await client.transcribe(long_wav) == "fallback transcript"
    assert calls == [get_settings().stt_model, get_settings().stt_fallback_model]
    assert client.last_stt_model == get_settings().stt_fallback_model

    calls.clear()
    short_wav = wrap_pcm16_wav(b"\x00\x00" * 4_000, 8_000)
    assert await client.transcribe(short_wav) == ""
    assert calls == [get_settings().stt_model]


@pytest.mark.asyncio
async def test_cached_filler_after_deadline_is_not_history(mock_db_session, monkeypatch):
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    ctx = CallContext(call_sid="CA_FILL", caller_number="+91000")
    handler._context = ctx
    handler._turn_number = 2
    handler._turn_state = TurnState.PROCESSING
    handler._send_audio = AsyncMock()
    response = _Response(
        turn_id=2,
        kind="caller_turn",
        metrics=TurnMetrics(
            call_sid=ctx.call_sid, turn_id=2, speech_end=time.monotonic() - 2
        ),
    )
    handler._response = response
    response.transcript = "I need a website"
    response.language = "en"
    response.analyzed.set()
    response.reply_generated.set()
    monkeypatch.setitem(_FILLER_AUDIO_CACHE, ("Okay.", "en"), [b"\x00\x00" * 1600])

    await asyncio.wait_for(handler._maybe_play_filler(response), timeout=1)

    assert response.filler_played is True
    assert ctx.messages == []
    assert handler._used_fillers == {"Okay."}


def test_turn_log_contains_requested_latency_fields():
    metrics = TurnMetrics(
        call_sid="CA_LOG",
        turn_id=3,
        speech_end=10.0,
        first_audio_output=10.25,
        extra={
            "stt_model": "gpt-4o-mini-transcribe",
            "tts_model": "eleven_flash_v2_5",
            "history_messages": 8,
        },
    )
    with patch("app.voice.turn_metrics.log_call_event") as logged:
        metrics.emit("completed")

    fields = logged.call_args_list[1].kwargs
    assert fields["stt_model"] == "gpt-4o-mini-transcribe"
    assert fields["tts_model"] == "eleven_flash_v2_5"
    assert fields["history_messages"] == 8
    assert fields["end_of_turn_to_first_audio_ms"] == 250.0
