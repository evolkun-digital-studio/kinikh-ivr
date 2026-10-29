"""Unit tests for app/ai/openai_client.py and app/ai/realtime_client.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_chat_response(content: str, total_tokens: int = 20) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(total_tokens=total_tokens)
    return resp


def _mock_transcription_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _make_timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=MagicMock())


def _make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=MagicMock())


def _make_rate_limit_error(retry_after: str | None = None) -> openai.RateLimitError:
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError("rate limited", response=mock_resp, body={"error": "rate limited"})


def _make_status_error(status_code: int) -> openai.APIStatusError:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    return openai.BadRequestError("bad request", response=mock_resp, body={"error": "bad"})


# ---------------------------------------------------------------------------
# OpenAIClient — basic construction
# ---------------------------------------------------------------------------


class TestOpenAIClientInit:
    def test_instantiates_without_error(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        assert client is not None
        assert client._model is not None

    def test_singleton_returns_same_instance(self) -> None:
        from app.ai.openai_client import get_openai_client

        a = get_openai_client()
        b = get_openai_client()
        assert a is b


# ---------------------------------------------------------------------------
# OpenAIClient — chat
# ---------------------------------------------------------------------------


class TestOpenAIClientChat:
    @pytest.mark.asyncio
    async def test_chat_returns_content(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("Hello world!")

        with patch.object(
            client._raw.chat.completions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.chat([{"role": "user", "content": "Hi"}])

        assert result == "Hello world!"

    @pytest.mark.asyncio
    async def test_chat_strips_whitespace(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("  trimmed  ")

        with patch.object(
            client._raw.chat.completions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.chat([{"role": "user", "content": "Hi"}])

        assert result == "trimmed"

    @pytest.mark.asyncio
    async def test_chat_json_mode_passes_response_format(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response('{"key": "value"}')
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            await client.chat([{"role": "user", "content": "json please"}], json_mode=True)

        assert captured.get("response_format") == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_chat_json_parses_json(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        payload = {"name": "Raj", "phone": "+91999"}
        mock_resp = _mock_chat_response(json.dumps(payload))

        with patch.object(
            client._raw.chat.completions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.chat_json([{"role": "user", "content": "extract"}])

        assert result == payload

    @pytest.mark.asyncio
    async def test_chat_json_returns_empty_on_invalid_json(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("not-json-at-all")

        with patch.object(
            client._raw.chat.completions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.chat_json([{"role": "user", "content": "x"}])

        assert result == {}

    @pytest.mark.asyncio
    async def test_chat_forwards_model_and_max_completion_tokens(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            await client.chat(
                [{"role": "user", "content": "x"}],
                model="gpt-4o-mini",
                temperature=1,
                max_tokens=256,
            )

        assert captured["model"] == "gpt-4o-mini"
        assert captured["max_completion_tokens"] == 256
        assert "max_tokens" not in captured

    @pytest.mark.asyncio
    async def test_chat_omits_non_default_temperature(self) -> None:
        """Regression: gpt-5.6-luna rejects any non-default temperature with
        HTTP 400 ('Only the default (1) value is supported') — a non-1
        temperature must not be sent at all, letting the API apply its own
        default instead of guessing which models accept an override."""
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            await client.chat(
                [{"role": "user", "content": "x"}], model="gpt-5.6-luna", temperature=0.3
            )

        assert "temperature" not in captured

    @pytest.mark.asyncio
    async def test_chat_sends_custom_temperature_to_gpt4_models(self) -> None:
        """The live-call model (gpt-4.1-mini) accepts a custom temperature;
        dropping it left replies at the default of 1."""
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            await client.chat(
                [{"role": "user", "content": "x"}], model="gpt-4.1-mini", temperature=0.2
            )

        assert captured["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_chat_sends_temperature_when_already_default(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            await client.chat([{"role": "user", "content": "x"}], temperature=1)

        assert captured["temperature"] == 1

    @pytest.mark.asyncio
    async def test_chat_json_uses_temperature_zero_which_gets_omitted(self) -> None:
        """chat_json() internally requests temperature=0 for determinism —
        on a model that rejects it, this must be silently omitted rather
        than raising, matching chat()'s own fallback behavior."""
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response('{"a": 1}')
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.chat.completions, "create", _capture):
            result = await client.chat_json([{"role": "user", "content": "x"}])

        assert result == {"a": 1}
        assert "temperature" not in captured


# ---------------------------------------------------------------------------
# OpenAIClient — retry logic
# ---------------------------------------------------------------------------


class TestOpenAIClientRetry:
    @pytest.mark.asyncio
    async def test_retries_on_timeout(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        call_count = 0

        async def _flaky(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_timeout_error()
            return mock_resp

        with (
            patch.object(client._raw.chat.completions, "create", _flaky),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await client.chat([{"role": "user", "content": "hi"}])

        assert call_count == 2
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        call_count = 0

        async def _flaky(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _make_connection_error()
            return mock_resp

        with (
            patch.object(client._raw.chat.completions, "create", _flaky),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await client.chat([{"role": "user", "content": "hi"}])

        assert call_count == 3
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()

        async def _always_fail(**kwargs: object) -> object:
            raise _make_timeout_error()

        with (
            patch.object(client._raw.chat.completions, "create", _always_fail),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(openai.APITimeoutError),
        ):
            await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("ok")
        call_count = 0

        async def _rate_limited(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_rate_limit_error()
            return mock_resp

        with (
            patch.object(client._raw.chat.completions, "create", _rate_limited),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await client.chat([{"role": "user", "content": "hi"}])

        assert call_count == 2
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_uses_retry_after_header(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        sleep_calls: list[float] = []
        mock_resp = _mock_chat_response("ok")
        call_count = 0

        async def _rate_limited(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_rate_limit_error(retry_after="7")
            return mock_resp

        async def _record_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch.object(client._raw.chat.completions, "create", _rate_limited),
            patch("asyncio.sleep", _record_sleep),
        ):
            await client.chat([{"role": "user", "content": "hi"}])

        assert sleep_calls[0] == 7.0

    @pytest.mark.asyncio
    async def test_non_retryable_4xx_raises_immediately(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        call_count = 0

        async def _bad_request(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            raise _make_status_error(400)

        with (
            patch.object(client._raw.chat.completions, "create", _bad_request),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(openai.BadRequestError),
        ):
            await client.chat([{"role": "user", "content": "bad"}])

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_increases(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        sleep_calls: list[float] = []

        async def _always_timeout(**kwargs: object) -> object:
            raise _make_timeout_error()

        async def _record_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch.object(client._raw.chat.completions, "create", _always_timeout),
            patch("asyncio.sleep", _record_sleep),
            pytest.raises(openai.APITimeoutError),
        ):
            await client.chat([{"role": "user", "content": "hi"}])

        assert len(sleep_calls) == 3
        assert sleep_calls[1] > sleep_calls[0]
        assert sleep_calls[2] > sleep_calls[1]


# ---------------------------------------------------------------------------
# OpenAIClient — streaming
# ---------------------------------------------------------------------------


class TestOpenAIClientStreaming:
    @pytest.mark.asyncio
    async def test_stream_chat_yields_tokens(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()

        def _make_chunk(content: str | None) -> MagicMock:
            chunk = MagicMock()
            chunk.choices = [MagicMock(delta=MagicMock(content=content))]
            return chunk

        async def _mock_stream(**kwargs: object) -> object:
            class _FakeStream:
                def __aiter__(self) -> object:
                    return self

                _items = [_make_chunk("Hello"), _make_chunk(None), _make_chunk(" world")]
                _idx = 0

                async def __anext__(self) -> object:
                    if self._idx >= len(self._items):
                        raise StopAsyncIteration
                    item = self._items[self._idx]
                    self._idx += 1
                    return item

            return _FakeStream()

        with patch.object(client._raw.chat.completions, "create", _mock_stream):
            tokens = []
            async for token in client.stream_chat([{"role": "user", "content": "hi"}]):
                tokens.append(token)

        assert tokens == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_stream_chat_uses_max_completion_tokens(self) -> None:
        """max_tokens is rejected (HTTP 400) by newer chat models — the
        wire-level parameter must be max_completion_tokens."""
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}

        async def _mock_stream(**kwargs: object) -> object:
            captured.update(kwargs)

            class _EmptyStream:
                def __aiter__(self) -> object:
                    return self

                async def __anext__(self) -> object:
                    raise StopAsyncIteration

            return _EmptyStream()

        with patch.object(client._raw.chat.completions, "create", _mock_stream):
            async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
                pass

        assert "max_completion_tokens" in captured
        assert "max_tokens" not in captured

    @pytest.mark.asyncio
    async def test_tts_stream_yields_chunks(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        audio_data = b"fake-mp3-data-chunk1fake-mp3-data-chunk2"

        class _FakeStreamingResponse:
            async def __aenter__(self) -> _FakeStreamingResponse:
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def iter_bytes(self, chunk_size: int = 4096):  # type: ignore[return]
                for i in range(0, len(audio_data), chunk_size):
                    yield audio_data[i : i + chunk_size]

        class _FakeStreamingManager:
            def create(self, **kwargs: object) -> _FakeStreamingResponse:
                return _FakeStreamingResponse()

        client._raw.audio.speech.with_streaming_response = _FakeStreamingManager()

        collected = b""
        async for chunk in client.tts_stream("Hello world"):
            collected += chunk

        assert collected == audio_data


# ---------------------------------------------------------------------------
# OpenAIClient — transcription
# ---------------------------------------------------------------------------


class TestOpenAIClientTranscription:
    @pytest.mark.asyncio
    async def test_transcribe_returns_text(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_transcription_response("  Hello there  ")

        with patch.object(
            client._raw.audio.transcriptions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.transcribe(b"fake-audio-bytes")

        assert result == "Hello there"

    @pytest.mark.asyncio
    async def test_transcribe_passes_language_en(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_transcription_response("ok")

        with patch.object(client._raw.audio.transcriptions, "create", _capture):
            await client.transcribe(b"audio", language="en")

        assert captured.get("language") == "en"

    @pytest.mark.asyncio
    async def test_transcribe_passes_language_hi(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_transcription_response("ok")

        with patch.object(client._raw.audio.transcriptions, "create", _capture):
            await client.transcribe(b"audio", language="hi")

        assert captured.get("language") == "hi"

    @pytest.mark.asyncio
    async def test_transcribe_skips_language_for_hinglish(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_transcription_response("ok")

        with patch.object(client._raw.audio.transcriptions, "create", _capture):
            await client.transcribe(b"audio", language="hi-en")

        assert "language" not in captured

    @pytest.mark.asyncio
    async def test_transcribe_skips_language_when_not_yet_known(self) -> None:
        """language=None (the caller's language hasn't been confirmed yet
        for this call) must let Whisper auto-detect — never silently
        defaulting to English."""
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_transcription_response("ok")

        with patch.object(client._raw.audio.transcriptions, "create", _capture):
            await client.transcribe(b"audio")  # no language kwarg at all

        assert "language" not in captured

    @pytest.mark.asyncio
    async def test_transcribe_retries_on_timeout(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        call_count = 0

        async def _flaky(**kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_timeout_error()
            return _mock_transcription_response("ok")

        with (
            patch.object(client._raw.audio.transcriptions, "create", _flaky),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await client.transcribe(b"audio")

        assert call_count == 2
        assert result == "ok"


# ---------------------------------------------------------------------------
# OpenAIClient — TTS
# ---------------------------------------------------------------------------


class TestOpenAIClientTTS:
    @pytest.mark.asyncio
    async def test_tts_returns_bytes(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        mock_resp = MagicMock()
        mock_resp.content = b"audio-bytes"

        with patch.object(
            client._raw.audio.speech,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            result = await client.tts("Hello")

        assert result == b"audio-bytes"

    @pytest.mark.asyncio
    async def test_tts_forwards_model_and_voice(self) -> None:
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}
        mock_resp = MagicMock()
        mock_resp.content = b""

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return mock_resp

        with patch.object(client._raw.audio.speech, "create", _capture):
            await client.tts("Hello", model="tts-1-hd", voice="nova")

        assert captured["model"] == "tts-1-hd"
        assert captured["voice"] == "nova"


# ---------------------------------------------------------------------------
# OpenAIClient — prompt loading
# ---------------------------------------------------------------------------


class TestOpenAIClientLoadPrompt:
    def test_load_known_prompt_no_format(self) -> None:
        from app.ai.openai_client import OpenAIClient

        result = OpenAIClient.load_prompt("greeting_prompt")
        assert "{company_name}" in result

    def test_load_known_prompt_with_format(self) -> None:
        from app.ai.openai_client import OpenAIClient

        result = OpenAIClient.load_prompt(
            "greeting_prompt", company_name="Kinikh", business_context="Departments:\n- rural"
        )
        assert "Kinikh" in result
        assert "rural" in result

    def test_load_unknown_prompt_raises(self) -> None:
        from app.ai.openai_client import OpenAIClient

        with pytest.raises(KeyError, match="NONEXISTENT_PROMPT"):
            OpenAIClient.load_prompt("nonexistent_prompt")

    def test_load_prompt_case_insensitive(self) -> None:
        from app.ai.openai_client import OpenAIClient

        result = OpenAIClient.load_prompt("FAREWELL_EN", company_name="Kinikh")
        assert "Kinikh" in result


# ---------------------------------------------------------------------------
# ChatSession — conversation memory
# ---------------------------------------------------------------------------


class TestChatSession:
    @pytest.mark.asyncio
    async def test_send_appends_to_history(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        mock_resp = _mock_chat_response("Nice to meet you!")

        with patch.object(
            client._raw.chat.completions,
            "create",
            AsyncMock(return_value=mock_resp),
        ):
            session = ChatSession(client, system_prompt="You are helpful.")
            reply = await session.send("Hello")

        assert reply == "Nice to meet you!"
        assert session.turn_count == 1
        history = session.history
        assert history[0]["role"] == "system"
        assert history[1]["role"] == "user"
        assert history[2]["role"] == "assistant"
        assert history[2]["content"] == "Nice to meet you!"

    @pytest.mark.asyncio
    async def test_second_turn_includes_history(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        messages_seen: list[list] = []

        async def _capture(**kwargs: object) -> object:
            messages_seen.append(list(kwargs["messages"]))
            return _mock_chat_response(f"turn {len(messages_seen)}")

        with patch.object(client._raw.chat.completions, "create", _capture):
            session = ChatSession(client)
            await session.send("first")
            await session.send("second")

        assert len(messages_seen[1]) == 3
        assert messages_seen[1][0]["role"] == "user"
        assert messages_seen[1][0]["content"] == "first"
        assert messages_seen[1][1]["role"] == "assistant"
        assert messages_seen[1][2]["role"] == "user"
        assert messages_seen[1][2]["content"] == "second"

    def test_clear_resets_history_keeps_system(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        session = ChatSession(client, system_prompt="You are helpful.")
        session.add_context("user", "hi")
        session.add_context("assistant", "hello")
        assert len(session.history) == 3

        session.clear()
        history = session.history
        assert len(history) == 1
        assert history[0]["role"] == "system"

    def test_clear_without_system_empties_history(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        session = ChatSession(client)
        session.add_context("user", "hi")
        session.clear()
        assert session.history == []

    def test_turn_count_counts_user_turns(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        session = ChatSession(client, system_prompt="sys")
        session.add_context("user", "turn1")
        session.add_context("assistant", "resp1")
        session.add_context("user", "turn2")
        assert session.turn_count == 2

    def test_add_context_injects_message(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()
        session = ChatSession(client)
        session.add_context("system", "Be concise.")
        history = session.history
        assert history[0] == {"role": "system", "content": "Be concise."}

    @pytest.mark.asyncio
    async def test_stream_send_yields_tokens_and_updates_history(self) -> None:
        from app.ai.openai_client import ChatSession, OpenAIClient

        client = OpenAIClient()

        def _make_chunk(content: str | None) -> MagicMock:
            chunk = MagicMock()
            chunk.choices = [MagicMock(delta=MagicMock(content=content))]
            return chunk

        async def _mock_stream(**kwargs: object) -> object:
            class _Stream:
                _chunks = [_make_chunk("A"), _make_chunk("B"), _make_chunk(None)]
                _i = 0

                def __aiter__(self) -> object:
                    return self

                async def __anext__(self) -> object:
                    if self._i >= len(self._chunks):
                        raise StopAsyncIteration
                    c = self._chunks[self._i]
                    self._i += 1
                    return c

            return _Stream()

        with patch.object(client._raw.chat.completions, "create", _mock_stream):
            session = ChatSession(client)
            tokens = []
            async for token in session.stream_send("hello"):
                tokens.append(token)

        assert tokens == ["A", "B"]
        history = session.history
        assert history[-1]["role"] == "assistant"
        assert history[-1]["content"] == "AB"


# ---------------------------------------------------------------------------
# RealtimeClient
# ---------------------------------------------------------------------------


class TestRealtimeClient:
    def test_instantiates_without_error(self) -> None:
        from app.ai.realtime_client import RealtimeClient

        client = RealtimeClient()
        assert client is not None
        assert client._model is not None

    def test_connect_returns_session(self) -> None:
        from app.ai.realtime_client import RealtimeClient, RealtimeSession

        client = RealtimeClient()
        session = client.connect(system_prompt="Hello", voice="alloy")
        assert isinstance(session, RealtimeSession)

    def test_session_stores_system_prompt(self) -> None:
        from app.ai.realtime_client import RealtimeClient

        client = RealtimeClient()
        session = client.connect(system_prompt="Be helpful.", voice="nova")
        assert session._system_prompt == "Be helpful."
        assert session._voice == "nova"

    @pytest.mark.asyncio
    async def test_session_context_manager_connects_and_disconnects(self) -> None:
        from app.ai.realtime_client import RealtimeClient

        client = RealtimeClient()
        mock_conn = MagicMock()
        mock_conn.session = MagicMock()
        mock_conn.session.update = AsyncMock()
        mock_conn.__aiter__ = MagicMock(return_value=iter([]))

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client._raw.beta.realtime, "connect", return_value=mock_cm):
            async with client.connect(system_prompt="sys") as session:
                assert session._conn is mock_conn
            mock_conn.session.update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_audio_encodes_base64(self) -> None:
        from app.ai.realtime_client import RealtimeClient

        client = RealtimeClient()
        import base64

        audio = b"\x00\x01\x02\x03"
        expected_b64 = base64.b64encode(audio).decode()

        mock_conn = MagicMock()
        mock_conn.session = MagicMock()
        mock_conn.session.update = AsyncMock()
        mock_conn.input_audio_buffer = MagicMock()
        mock_conn.input_audio_buffer.append = AsyncMock()

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client._raw.beta.realtime, "connect", return_value=mock_cm):
            async with client.connect() as session:
                await session.send_audio(audio)

        mock_conn.input_audio_buffer.append.assert_awaited_once_with(audio=expected_b64)

    @pytest.mark.asyncio
    async def test_stream_response_yields_audio_chunks(self) -> None:
        import base64

        from app.ai.realtime_client import RealtimeClient

        chunk1 = base64.b64encode(b"audio1").decode()
        chunk2 = base64.b64encode(b"audio2").decode()

        def _make_event(etype: str, delta: str = "") -> MagicMock:
            e = MagicMock()
            e.type = etype
            e.delta = delta
            return e

        events = [
            _make_event("response.audio.delta", chunk1),
            _make_event("response.audio.delta", chunk2),
            _make_event("response.done"),
        ]

        client = RealtimeClient()
        mock_conn = MagicMock()
        mock_conn.session = MagicMock()
        mock_conn.session.update = AsyncMock()

        async def _iter_events() -> object:
            for ev in events:
                yield ev

        mock_conn.__aiter__ = lambda _: _iter_events()

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client._raw.beta.realtime, "connect", return_value=mock_cm):
            async with client.connect() as session:
                collected = []
                async for chunk in session.stream_response():
                    collected.append(chunk)

        assert collected == [b"audio1", b"audio2"]

    @pytest.mark.asyncio
    async def test_stream_response_stops_on_error_event(self) -> None:
        from app.ai.realtime_client import RealtimeClient

        def _make_event(etype: str, delta: str = "") -> MagicMock:
            e = MagicMock()
            e.type = etype
            e.delta = delta
            e.error = {"message": "something went wrong"}
            return e

        import base64

        events = [
            _make_event("response.audio.delta", base64.b64encode(b"x").decode()),
            _make_event("error"),
            _make_event("response.audio.delta", base64.b64encode(b"y").decode()),
        ]

        client = RealtimeClient()
        mock_conn = MagicMock()
        mock_conn.session = MagicMock()
        mock_conn.session.update = AsyncMock()

        async def _iter_events() -> object:
            for ev in events:
                yield ev

        mock_conn.__aiter__ = lambda _: _iter_events()

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client._raw.beta.realtime, "connect", return_value=mock_cm):
            async with client.connect() as session:
                collected = []
                async for chunk in session.stream_response():
                    collected.append(chunk)

        assert collected == [b"x"]


# ---------------------------------------------------------------------------
# _parse_retry_after helper
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_extracts_header_value(self) -> None:
        from app.ai.openai_client import _parse_retry_after

        exc = _make_rate_limit_error(retry_after="15")
        result = _parse_retry_after(exc, default=5.0)
        assert result == 15.0

    def test_falls_back_to_default_when_no_header(self) -> None:
        from app.ai.openai_client import _parse_retry_after

        exc = _make_rate_limit_error(retry_after=None)
        result = _parse_retry_after(exc, default=3.0)
        assert result == 3.0

    def test_caps_at_max_backoff(self) -> None:
        from app.ai.openai_client import _MAX_BACKOFF, _parse_retry_after

        exc = _make_rate_limit_error(retry_after="9999")
        result = _parse_retry_after(exc, default=1.0)
        assert result == _MAX_BACKOFF
