"""ConversationEngine.stream_reply — sentence streaming for the live call path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import ConversationEngine, split_speakable
from app.ai.openai_client import OpenAIClient


def _streaming_client(stream_chat, chat=None) -> OpenAIClient:
    client = OpenAIClient.__new__(OpenAIClient)  # no network: methods replaced
    client.stream_chat = stream_chat
    client.chat = chat or AsyncMock(return_value="Fallback chat reply.")
    return client


def _context() -> CallContext:
    ctx = CallContext(call_sid="CA_STREAM", caller_number="+919876543210")
    ctx.add_message("user", "I need help with a renovation")
    return ctx


async def _collect(engine, ctx, timeout: float = 1.0) -> list[str]:
    return [s async for s in engine.stream_reply(ctx, first_token_timeout=timeout)]


@pytest.mark.parametrize(
    ("text", "ready", "rest"),
    [
        ("Sure. I can help with that. The", ["Sure. I can help with that."], "The"),
        ("It costs Rs. 5.5 lakh. Also", ["It costs Rs. 5.5 lakh."], "Also"),
        ("Mr. Sharma will call you. Th", ["Mr. Sharma will call you."], "Th"),
        ("Still speaking without a boundary", [], "Still speaking without a boundary"),
    ],
)
def test_split_speakable(text, ready, rest):
    assert split_speakable(text) == (ready, rest)


@pytest.mark.asyncio
async def test_first_sentence_is_yielded_before_the_reply_finishes():
    second_part = asyncio.Event()

    async def stream_chat(messages, **kwargs):
        for token in ["Sure, ", "I can help with that. ", "What "]:
            yield token
        await second_part.wait()
        yield "is the address?"

    engine = ConversationEngine([], client=_streaming_client(stream_chat))
    segments = engine.stream_reply(_context(), first_token_timeout=1.0)
    first = await asyncio.wait_for(segments.__anext__(), 0.5)  # before the stream ends
    assert first == "Sure, I can help with that."
    second_part.set()
    assert [s async for s in segments] == ["What is the address?"]
    assert engine.last_first_token_at is not None


@pytest.mark.asyncio
async def test_stream_reply_does_not_modify_history():
    async def stream_chat(messages, **kwargs):
        yield "Okay. "

    ctx = _context()
    engine = ConversationEngine([], client=_streaming_client(stream_chat))
    await _collect(engine, ctx)
    assert [m.role for m in ctx.messages] == ["user"]


@pytest.mark.asyncio
async def test_no_first_token_in_time_falls_back_to_chat_then_spoken_fallback():
    async def silent_stream(messages, **kwargs):
        await asyncio.Event().wait()
        yield ""

    async def slow_chat(messages, **kwargs):
        await asyncio.Event().wait()

    engine = ConversationEngine(
        [], client=_streaming_client(silent_stream, chat=AsyncMock(side_effect=slow_chat))
    )
    segments = await asyncio.wait_for(_collect(engine, _context(), timeout=0.05), 1.0)
    assert segments == ["I'm sorry, I'm having trouble right now. Please call back in a moment."]


@pytest.mark.asyncio
async def test_stream_error_before_output_retries_with_chat():
    async def failing_stream(messages, **kwargs):
        raise ConnectionError("reset")
        yield ""  # pragma: no cover

    engine = ConversationEngine([], client=_streaming_client(failing_stream))
    assert await _collect(engine, _context()) == ["Fallback chat reply."]


@pytest.mark.asyncio
async def test_mid_reply_failure_keeps_what_was_already_said():
    async def breaking_stream(messages, **kwargs):
        yield "Your appointment is confirmed. "
        yield "Anything"
        raise ConnectionError("reset")

    engine = ConversationEngine([], client=_streaming_client(breaking_stream))
    assert await _collect(engine, _context()) == ["Your appointment is confirmed."]
