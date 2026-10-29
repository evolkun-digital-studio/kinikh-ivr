"""Central OpenAI client: retry, exponential backoff, rate-limit, logging, streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import io
import json
import time
import wave
from typing import Any

from loguru import logger
import openai
from openai import AsyncOpenAI

from app.core.config import get_settings

_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_MAX_BACKOFF = 30.0
_DEFAULT_TIMEOUT = 30.0

_RETRYABLE_TYPES = (
    openai.APITimeoutError,
    openai.APIConnectionError,
)


def _parse_retry_after(exc: openai.RateLimitError, default: float) -> float:
    """Extract Retry-After header value or fall back to default."""
    try:
        if hasattr(exc, "response") and exc.response is not None:
            header = exc.response.headers.get("retry-after")
            if header:
                return min(float(header), _MAX_BACKOFF)
    except Exception as parse_exc:
        logger.debug(f"[openai] could not parse retry-after header: {parse_exc}")
    return min(default, _MAX_BACKOFF)


# Global persistent AsyncOpenAI client to ensure connection pooling and avoid leaks
_async_openai_client: AsyncOpenAI | None = None
_reasoning_effort_supported: dict[str, bool] = {}
# Non-reasoning chat models that accept a custom temperature. Reasoning
# models (gpt-5.x, o-series) reject anything but the default of 1.
_CUSTOM_TEMPERATURE_MODELS = ("gpt-4.", "gpt-4o", "gpt-3.5")


def _accepts_temperature(model: str, temperature: float) -> bool:
    return temperature == 1 or model.startswith(_CUSTOM_TEMPERATURE_MODELS)


def _get_async_openai_client() -> AsyncOpenAI:
    global _async_openai_client
    if _async_openai_client is None:
        settings = get_settings()
        _async_openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=_DEFAULT_TIMEOUT,
            max_retries=0,
        )
    return _async_openai_client


class OpenAIClient:
    """
    Async OpenAI wrapper with retry, exponential backoff, and structured logging.

    All public methods log at INFO before each request and DEBUG on success.
    Retryable errors (timeout, connection, 429) are retried up to _MAX_RETRIES times
    with exponential backoff. Non-retryable 4xx errors raise immediately.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._raw = _get_async_openai_client()
        self._model = settings.openai_model
        self._temperature = settings.openai_temperature
        self._max_tokens = settings.openai_max_tokens
        self.last_stt_model: str | None = None

    async def _retry(
        self,
        label: str,
        coro_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        backoff = _BASE_BACKOFF
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            t0 = time.monotonic()
            try:
                result = await coro_fn()
                elapsed = time.monotonic() - t0
                logger.debug(f"[openai] {label} ok attempt={attempt} elapsed={elapsed:.2f}s")
                return result
            except openai.RateLimitError as exc:
                last_exc = exc
                wait = _parse_retry_after(exc, backoff)
                logger.warning(
                    f"[openai] {label} rate_limit attempt={attempt}/{_MAX_RETRIES}"
                    f" retry_after={wait:.1f}s"
                )
                await asyncio.sleep(wait)
            except _RETRYABLE_TYPES as exc:
                last_exc = exc
                exc_name = type(exc).__name__
                logger.warning(
                    f"[openai] {label} {exc_name} attempt={attempt}/{_MAX_RETRIES}"
                    f" backoff={backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
            except openai.APIStatusError as exc:
                if exc.status_code == 429:
                    last_exc = exc
                    logger.warning(
                        f"[openai] {label} 429 attempt={attempt}/{_MAX_RETRIES}"
                        f" backoff={backoff:.1f}s"
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(f"[openai] {label} http={exc.status_code}")
                    raise

            backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

        logger.error(f"[openai] {label} exhausted {_MAX_RETRIES} attempts: {last_exc}")
        raise last_exc  # type: ignore[misc]

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        timeout: float = _DEFAULT_TIMEOUT,
        reasoning_effort: str | None = None,
    ) -> str:
        """Single chat completion — returns assistant message content."""
        m = model or self._model
        t = temperature if temperature is not None else self._temperature
        mt = max_tokens or self._max_tokens
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            # max_tokens is deprecated on the Chat Completions API and rejected
            # outright (HTTP 400) by newer models — max_completion_tokens is
            # the current parameter and is accepted across all chat models.
            "max_completion_tokens": mt,
            "timeout": timeout,
        }
        # gpt-5.6-luna rejects any non-default temperature outright: HTTP 400
        # "Unsupported value: 'temperature' does not support <x> with this
        # model. Only the default (1) value is supported." GPT-4.x/4o accept
        # it, and the live-call model needs the configured low temperature.
        if _accepts_temperature(m, t):
            kwargs["temperature"] = t
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_effort and _reasoning_effort_supported.get(m, True):
            kwargs["reasoning_effort"] = reasoning_effort

        logger.info(f"[openai] chat model={m} turns={len(messages)} json_mode={json_mode}")

        try:
            resp = await self._retry(
                f"chat:{m}",
                lambda: self._raw.chat.completions.create(**kwargs),
            )
            if "reasoning_effort" in kwargs:
                _reasoning_effort_supported[m] = True
        except openai.APIStatusError as exc:
            if "reasoning_effort" not in kwargs or exc.status_code != 400:
                raise
            logger.warning(
                f"[openai] chat model={m} does not accept reasoning_effort; retrying without it"
            )
            _reasoning_effort_supported[m] = False
            kwargs.pop("reasoning_effort", None)
            resp = await self._retry(
                f"chat:{m}:without_reasoning_effort",
                lambda: self._raw.chat.completions.create(**kwargs),
            )
        content = resp.choices[0].message.content.strip()
        usage = resp.usage
        logger.debug(f"[openai] chat done tokens={usage.total_tokens if usage else '?'}")
        return content

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Chat completion with JSON response_format — returns parsed dict."""
        raw = await self.chat(
            messages,
            model=model,
            temperature=0,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"[openai] json_decode_error type={type(exc).__name__}")
            return {}

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens — yields each content delta."""
        m = model or self._model
        t = temperature if temperature is not None else self._temperature
        mt = max_tokens or self._max_tokens

        logger.info(f"[openai] stream_chat model={m} turns={len(messages)}")

        stream_kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            "max_completion_tokens": mt,
            "stream": True,
            # Final chunk carries token usage, including how much of the
            # prompt was served from OpenAI's prompt cache (faster first token).
            "stream_options": {"include_usage": True},
        }
        if _accepts_temperature(m, t):  # see chat()
            stream_kwargs["temperature"] = t
        if reasoning_effort and _reasoning_effort_supported.get(m, True):
            stream_kwargs["reasoning_effort"] = reasoning_effort
        try:
            stream = await self._raw.chat.completions.create(**stream_kwargs)
            if "reasoning_effort" in stream_kwargs:
                _reasoning_effort_supported[m] = True
        except openai.APIStatusError as exc:
            if "reasoning_effort" not in stream_kwargs or exc.status_code != 400:
                raise
            logger.warning(
                f"[openai] stream model={m} does not accept reasoning_effort; retrying without it"
            )
            _reasoning_effort_supported[m] = False
            stream_kwargs.pop("reasoning_effort", None)
            stream = await self._raw.chat.completions.create(**stream_kwargs)
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                details = getattr(usage, "prompt_tokens_details", None)
                cached = getattr(details, "cached_tokens", None) if details else None
                logger.info(
                    f"[openai] stream usage model={m} prompt_tokens={usage.prompt_tokens} "
                    f"cached_tokens={cached or 0}"
                )
            if not chunk.choices:
                continue  # the usage-only final chunk
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> list[list[float]]:
        """Embed a batch of texts — returns one vector per input, in order."""
        logger.info(f"[openai] embed model={model} texts={len(texts)}")

        resp = await self._retry(
            f"embed:{model}",
            lambda: self._raw.embeddings.create(model=model, input=texts, timeout=timeout),
        )
        return [item.embedding for item in sorted(resp.data, key=lambda item: item.index)]

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        """Transcribe phone audio. Uses settings.stt_model (default
        gpt-4o-transcribe) with a vocabulary prompt, falling back to
        settings.stt_fallback_model (whisper-1) if the model is rejected."""
        from app.ai.knowledge import stt_vocabulary_prompt

        settings = get_settings()
        lang_code: str | None = None
        # language=None (not yet confirmed for this call) intentionally omits
        # the hint — the model auto-detects instead of being biased. Hinglish
        # is left to auto-detect too (forcing "hi" would transliterate the
        # English words into Devanagari, forcing "en" would drop Hindi).
        if language and language != "hi-en":
            lang_code = "hi" if language == "hi" else "en"
        prompt = stt_vocabulary_prompt()

        def _kwargs(model: str) -> dict[str, Any]:
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "audio.wav"
            kwargs: dict[str, Any] = {
                "model": model,
                "file": audio_file,
                "timeout": timeout,
                "temperature": 0,
            }
            if prompt:
                kwargs["prompt"] = prompt
            if lang_code:
                kwargs["language"] = lang_code
            return kwargs

        model = settings.stt_model or "whisper-1"
        fallback = settings.stt_fallback_model
        self.last_stt_model = model
        logger.info(
            f"[openai] transcribe model={model} language_hint={language or 'auto'} "
            f"bytes={len(audio_bytes)}"
        )
        primary_error: Exception | None = None
        try:
            resp = await self._retry(
                f"stt:{model}",
                lambda: self._raw.audio.transcriptions.create(**_kwargs(model)),
            )
        except Exception as exc:
            primary_error = exc
            if not fallback or fallback == model:
                raise
            logger.warning(
                f"[openai] stt model={model} failed type={type(exc).__name__}; using {fallback}"
            )
            self.last_stt_model = fallback
            resp = await self._retry(
                f"stt:{fallback}",
                lambda: self._raw.audio.transcriptions.create(**_kwargs(fallback)),
            )
        text = resp.text.strip()
        if not text and primary_error is None and fallback and fallback != model:
            duration_ms = 0.0
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                    duration_ms = wav.getnframes() / wav.getframerate() * 1000
            except (wave.Error, EOFError, ZeroDivisionError):
                pass
            if duration_ms > 600:
                logger.warning(
                    f"[openai] stt model={model} returned empty for "
                    f"speech_ms={duration_ms:.0f}; using {fallback}"
                )
                self.last_stt_model = fallback
                resp = await self._retry(
                    f"stt:{fallback}",
                    lambda: self._raw.audio.transcriptions.create(**_kwargs(fallback)),
                )
                text = resp.text.strip()
        logger.debug(f"[openai] transcribe chars={len(text)}")
        return text

    async def tts(
        self,
        text: str,
        *,
        model: str = "tts-1",
        voice: str = "alloy",
        timeout: float = _DEFAULT_TIMEOUT,
        response_format: str = "mp3",
    ) -> bytes:
        """Text-to-speech via OpenAI TTS. `response_format="pcm"` returns raw
        24 kHz signed 16-bit LE mono (what the phone path needs); the
        default stays MP3 for non-telephony callers."""
        logger.info(
            f"[openai] tts model={model} voice={voice} chars={len(text)} format={response_format}"
        )

        resp = await self._retry(
            f"tts:{voice}",
            lambda: self._raw.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                timeout=timeout,
                response_format=response_format,
            ),
        )
        return resp.content

    async def tts_stream(
        self,
        text: str,
        *,
        model: str = "tts-1",
        voice: str = "alloy",
        response_format: str = "mp3",
    ) -> AsyncIterator[bytes]:
        """Stream TTS audio in 4 KB chunks for lower first-byte latency."""
        logger.info(f"[openai] tts_stream model={model} voice={voice} format={response_format}")

        async with self._raw.audio.speech.with_streaming_response.create(
            model=model, voice=voice, input=text, response_format=response_format
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    @staticmethod
    def load_prompt(name: str, **kwargs: str) -> str:
        """
        Load and render a named prompt template from app.prompts.system.

        name must match an uppercase constant in that module, e.g. "greeting_prompt".
        kwargs are forwarded to str.format(); placeholders not supplied are
        rendered empty instead of raising, so templates can gain optional
        fields without breaking callers.
        """
        from app.prompts import system as prompt_module

        template: str | None = getattr(prompt_module, name.upper(), None)
        if template is None:
            raise KeyError(f"Prompt {name.upper()!r} not found in app.prompts.system")
        if not kwargs:
            return template

        class _Defaults(dict):
            def __missing__(self, key: str) -> str:
                return ""

        return template.format_map(_Defaults(kwargs))


class ChatSession:
    """
    Stateful multi-turn conversation backed by OpenAIClient.

    Maintains full message history so each call automatically includes context.
    Useful for long-running call sessions where GPT needs prior turns.
    """

    def __init__(
        self,
        client: OpenAIClient,
        *,
        system_prompt: str = "",
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._messages: list[dict[str, str]] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    async def send(self, user_message: str) -> str:
        """Append a user turn, get assistant reply, append reply to history."""
        self._messages.append({"role": "user", "content": user_message})
        reply = await self._client.chat(
            self._messages,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        self._messages.append({"role": "assistant", "content": reply})
        return reply

    async def stream_send(self, user_message: str) -> AsyncIterator[str]:
        """Streaming version of send — yields tokens and appends final reply."""
        self._messages.append({"role": "user", "content": user_message})
        accumulated = ""
        async for token in self._client.stream_chat(
            self._messages,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):
            accumulated += token
            yield token
        self._messages.append({"role": "assistant", "content": accumulated})

    def add_context(self, role: str, content: str) -> None:
        """Inject a message into the history without triggering a completion."""
        self._messages.append({"role": role, "content": content})

    def clear(self) -> None:
        """Reset history, preserving the system prompt if one was set."""
        system = next((m for m in self._messages if m["role"] == "system"), None)
        self._messages = [system] if system else []

    @property
    def history(self) -> list[dict[str, str]]:
        return list(self._messages)

    @property
    def turn_count(self) -> int:
        return sum(1 for m in self._messages if m["role"] == "user")


_singleton: OpenAIClient | None = None


def get_openai_client() -> OpenAIClient:
    """Return the module-level OpenAIClient singleton."""
    global _singleton
    if _singleton is None:
        _singleton = OpenAIClient()
    return _singleton
