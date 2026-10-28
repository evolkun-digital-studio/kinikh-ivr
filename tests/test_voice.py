"""Voice module tests — providers, STT, TTS, VoiceManager, AudioBuffer."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ExternalServiceError
from app.voice.base import BaseVoiceProvider


@pytest.fixture(autouse=True)
def _reset_elevenlabs_http_client():
    """ElevenLabsClient caches ONE httpx client per process; without a reset,
    patching httpx.AsyncClient in a test has no effect after the first test
    created the real client (why these tests used to fail)."""
    import app.voice.elevenlabs as el

    el._http_client = None
    yield
    el._http_client = None


def _pcm16(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)

# ---------------------------------------------------------------------------
# Shared test helper
# ---------------------------------------------------------------------------


class _MockProvider(BaseVoiceProvider):
    """Configurable BaseVoiceProvider stub for unit tests."""

    def __init__(
        self,
        *,
        transcript: str = "",
        audio: bytes = b"",
        stream_chunks: list[bytes] | None = None,
        transcription_error: Exception | None = None,
        synthesis_error: Exception | None = None,
        stream_error: Exception | None = None,
        transcription_supported: bool = True,
    ) -> None:
        self._transcript = transcript
        self._audio = audio
        self._stream_chunks = stream_chunks if stream_chunks is not None else []
        self._transcription_error = transcription_error
        self._synthesis_error = synthesis_error
        self._stream_error = stream_error
        self._transcription_supported = transcription_supported
        self.transcribe_calls: list = []
        self.synthesize_calls: list = []

    @property
    def supports_transcription(self) -> bool:
        return self._transcription_supported

    async def transcribe(self, audio_bytes, *, language=None):
        self.transcribe_calls.append((audio_bytes, language))
        if self._transcription_error:
            raise self._transcription_error
        return self._transcript

    async def synthesize(self, text, *, language="en", voice="alloy"):
        self.synthesize_calls.append((text, language, voice))
        if self._synthesis_error:
            raise self._synthesis_error
        return self._audio

    async def synthesize_stream(self, text, *, language="en", voice="alloy"):
        if self._stream_error:
            raise self._stream_error
        for chunk in self._stream_chunks:
            yield chunk


# Custom async iterator that raises on first iteration (avoids unreachable yield)
class _ErrorStream:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


# ---------------------------------------------------------------------------
# TestBaseVoiceProvider
# ---------------------------------------------------------------------------


class TestBaseVoiceProvider:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            BaseVoiceProvider()

    def test_default_supports_transcription_false(self):
        class _Stub(BaseVoiceProvider):
            async def transcribe(self, *a, **k):
                return ""

            async def synthesize(self, *a, **k):
                return b""

            async def synthesize_stream(self, *a, **k):
                for _ in ():
                    yield b""

        stub = _Stub()
        assert stub.supports_transcription is False
        assert stub.supports_synthesis is True


# ---------------------------------------------------------------------------
# TestOpenAIVoiceProvider
# ---------------------------------------------------------------------------


class TestOpenAIVoiceProvider:
    def test_supports_transcription(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        p = OpenAIVoiceProvider(client=MagicMock())
        assert p.supports_transcription is True

    def test_supports_synthesis(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        p = OpenAIVoiceProvider(client=MagicMock())
        assert p.supports_synthesis is True

    @pytest.mark.asyncio
    async def test_transcribe_delegates_to_client(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        mock_client = MagicMock()
        mock_client.transcribe = AsyncMock(return_value="hello world")
        p = OpenAIVoiceProvider(client=mock_client)

        result = await p.transcribe(b"audio", language="en")

        assert result == "hello world"
        mock_client.transcribe.assert_called_once_with(b"audio", language="en")

    @pytest.mark.asyncio
    async def test_transcribe_raises_external_service_error(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        mock_client = MagicMock()
        mock_client.transcribe = AsyncMock(side_effect=RuntimeError("network"))
        p = OpenAIVoiceProvider(client=mock_client)

        with pytest.raises(ExternalServiceError):
            await p.transcribe(b"audio")

    @pytest.mark.asyncio
    async def test_synthesize_returns_audio_bytes(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        mock_client = MagicMock()
        pcm_24k = _pcm16([0, 1000, 2000, 1000, 0, -1000] * 400)  # 100 ms @ 24 kHz
        mock_client.tts = AsyncMock(return_value=pcm_24k)
        p = OpenAIVoiceProvider(client=mock_client)

        result = await p.synthesize("hello", voice="nova")

        # Raw PCM requested (never MP3) and converted 24 kHz -> 8 kHz.
        mock_client.tts.assert_called_once_with("hello", voice="nova", response_format="pcm")
        assert len(result) % 2 == 0
        assert abs(len(result) - len(pcm_24k) // 3) <= 64
        assert not result.startswith((b"ID3", b"\xff\xfb", b"RIFF"))

    @pytest.mark.asyncio
    async def test_synthesize_raises_external_service_error(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        mock_client = MagicMock()
        mock_client.tts = AsyncMock(side_effect=RuntimeError("api down"))
        p = OpenAIVoiceProvider(client=mock_client)

        with pytest.raises(ExternalServiceError):
            await p.synthesize("hello")

    @pytest.mark.asyncio
    async def test_synthesize_stream_yields_chunks(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        seen_kwargs = {}

        async def _tts_stream(*args, **kwargs):
            seen_kwargs.update(kwargs)
            yield _pcm16([100] * 2400)
            yield _pcm16([100] * 2401) + b"\x00"  # odd trailing byte held over

        mock_client = MagicMock()
        mock_client.tts_stream = _tts_stream
        p = OpenAIVoiceProvider(client=mock_client)

        chunks = [c async for c in p.synthesize_stream("hello")]

        assert seen_kwargs["response_format"] == "pcm"
        assert chunks and all(len(c) % 2 == 0 for c in chunks)
        total_samples = sum(len(c) for c in chunks) // 2
        assert abs(total_samples - 4801 // 3) <= 32

    @pytest.mark.asyncio
    async def test_synthesize_stream_raises_on_error(self):
        from app.voice.openai_provider import OpenAIVoiceProvider

        mock_client = MagicMock()
        mock_client.tts_stream = MagicMock(return_value=_ErrorStream(RuntimeError("stream fail")))
        p = OpenAIVoiceProvider(client=mock_client)

        with pytest.raises(ExternalServiceError):
            async for _ in p.synthesize_stream("hello"):
                pass


# ---------------------------------------------------------------------------
# TestElevenLabsClient — real HTTP payload/format sent to ElevenLabs
# ---------------------------------------------------------------------------


def _fake_elevenlabs_settings(**overrides):
    settings = MagicMock()
    settings.elevenlabs_api_key = "fake-key"
    settings.elevenlabs_model_id = "eleven_multilingual_v2"
    settings.elevenlabs_voice_id_en = "voice-en"
    settings.elevenlabs_voice_id_hi = "voice-hi"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class TestElevenLabsClient:
    """Locks in the pcm_16000 output format Exotel's AgentStream/Voicebot
    media stream requires (signed 16-bit LE PCM, 8000Hz, mono) — ElevenLabs
    defaults to MP3 unless output_format is explicitly requested as a query
    parameter (not a JSON body field)."""

    @pytest.mark.asyncio
    async def test_synthesize_requests_pcm_16000_output_format(self):
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        fake_response = MagicMock(status_code=200, content=b"RAW_PCM_BYTES")
        fake_http_client = AsyncMock()
        fake_http_client.post = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            audio = await client.synthesize("Hello", "en")

        assert audio and len(audio) % 2 == 0  # whole 16-bit samples, 8 kHz
        _, kwargs = fake_http_client.post.call_args
        assert kwargs["params"] == {"output_format": "pcm_16000"}
        assert kwargs["json"]["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_synthesize_never_requests_mp3_default(self):
        """No output_format at all would silently fall back to ElevenLabs'
        MP3 default — this is the exact regression that broke playback."""
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        fake_response = MagicMock(status_code=200, content=b"RAW_PCM_BYTES")
        fake_http_client = AsyncMock()
        fake_http_client.post = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            await client.synthesize("Hello", "en")

        _, kwargs = fake_http_client.post.call_args
        assert kwargs["params"].get("output_format") != "mp3_44100_128"
        assert kwargs["params"].get("output_format") is not None

    @pytest.mark.asyncio
    async def test_synthesize_stream_requests_pcm_16000_output_format(self):
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()

        async def _aiter_bytes(chunk_size=4096):
            yield b"CHUNK1"

        fake_response = MagicMock(status_code=200)
        fake_response.aiter_bytes = _aiter_bytes
        fake_stream_ctx = AsyncMock()
        fake_stream_ctx.__aenter__ = AsyncMock(return_value=fake_response)
        fake_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        fake_http_client = AsyncMock()
        fake_http_client.stream = MagicMock(return_value=fake_stream_ctx)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            chunks = [chunk async for chunk in client.synthesize_stream("Hello", "en")]

        assert chunks and all(len(c) % 2 == 0 for c in chunks)
        _, kwargs = fake_http_client.stream.call_args
        assert kwargs["params"] == {"output_format": "pcm_16000"}


class TestElevenLabsVoiceSelection:
    """Voice-ID selection per language — deterministic and config-driven
    (never hardcoded), covering English, Hindi, Hinglish, and unknown
    language fallback."""

    def _fake_http_client_with_url_capture(self, captured: list[str]) -> AsyncMock:
        fake_response = MagicMock(status_code=200, content=b"RAW_PCM_BYTES")
        fake_http_client = AsyncMock()

        async def _capture_post(url, **kwargs):
            captured.append(url)
            return fake_response

        fake_http_client.post = _capture_post
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)
        return fake_http_client

    @pytest.mark.asyncio
    async def test_english_uses_configured_english_voice(self):
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        urls: list[str] = []
        fake_http_client = self._fake_http_client_with_url_capture(urls)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            await client.synthesize("Hello", "en")

        assert urls[0].endswith(f"/text-to-speech/{settings.elevenlabs_voice_id_en}")

    @pytest.mark.asyncio
    async def test_hindi_uses_configured_hindi_voice(self):
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        urls: list[str] = []
        fake_http_client = self._fake_http_client_with_url_capture(urls)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            await client.synthesize("नमस्ते", "hi")

        assert urls[0].endswith(f"/text-to-speech/{settings.elevenlabs_voice_id_hi}")

    @pytest.mark.asyncio
    async def test_hinglish_uses_hindi_voice_for_bilingual_output(self):
        """eleven_multilingual_v2 renders whichever language is present in
        the text using the selected voice's accent — the Hindi voice reads
        code-switched Hindi-English more naturally than the English one."""
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        urls: list[str] = []
        fake_http_client = self._fake_http_client_with_url_capture(urls)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            await client.synthesize("Aapka billing department se baat karni hai", "hi-en")

        assert urls[0].endswith(f"/text-to-speech/{settings.elevenlabs_voice_id_hi}")

    @pytest.mark.asyncio
    async def test_unknown_language_falls_back_to_english_voice(self):
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings()
        urls: list[str] = []
        fake_http_client = self._fake_http_client_with_url_capture(urls)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            await client.synthesize("Hello", "fr")

        assert urls[0].endswith(f"/text-to-speech/{settings.elevenlabs_voice_id_en}")

    def test_voice_ids_are_not_hardcoded(self):
        """Voice IDs must come from settings, never literal strings in code —
        e.g. the Hindi voice ID must be configurable via .env, not baked in."""
        from app.voice.elevenlabs import ElevenLabsClient

        settings = _fake_elevenlabs_settings(elevenlabs_voice_id_hi="uuyFQegRan8rpIQCGu9K")
        with patch("app.voice.elevenlabs.get_settings", return_value=settings):
            client = ElevenLabsClient()

        assert client._voice_for("hi") == "uuyFQegRan8rpIQCGu9K"
        # Changing the setting changes the resolved voice — proves it's read
        # from config, not a hardcoded literal.
        settings2 = _fake_elevenlabs_settings(elevenlabs_voice_id_hi="a-different-voice-id")
        with patch("app.voice.elevenlabs.get_settings", return_value=settings2):
            client2 = ElevenLabsClient()
        assert client2._voice_for("hi") == "a-different-voice-id"


class TestElevenLabsOutputIsRealPCM16:
    """Actually inspects the returned bytes as decoded PCM samples, not just
    the output_format metadata sent in the request — locks in that pcm_16000
    audio passes through byte-for-byte with no accidental re-encoding."""

    @pytest.mark.asyncio
    async def test_synthesize_returns_byte_correct_pcm16_samples(self):
        import struct

        from app.voice.elevenlabs import ElevenLabsClient

        # A known signed 16-bit little-endian PCM signal, exactly as
        # ElevenLabs returns for pcm_16000 — no container, no header.
        # 1 kHz tone (speech band): must survive the 16 k -> 8 k conversion.
        import math

        samples = [int(10000 * math.sin(2 * math.pi * 1000 * n / 16000)) for n in range(1600)]
        raw_pcm = struct.pack(f"<{len(samples)}h", *samples)

        settings = _fake_elevenlabs_settings()
        fake_response = MagicMock(status_code=200, content=raw_pcm)
        fake_http_client = AsyncMock()
        fake_http_client.post = AsyncMock(return_value=fake_response)
        fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
        fake_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.voice.elevenlabs.get_settings", return_value=settings),
            patch("app.voice.elevenlabs.httpx.AsyncClient", return_value=fake_http_client),
        ):
            client = ElevenLabsClient()
            audio = await client.synthesize("Hello", "en")

        assert len(audio) % 2 == 0  # 16-bit sample alignment
        decoded = struct.unpack(f"<{len(audio) // 2}h", audio)
        # Half the samples (8 kHz), plus the short filter tail.
        assert abs(len(decoded) - len(samples) // 2) <= 32
        # The in-band tone keeps its level (steady-state part, skip edges).
        middle = decoded[100:700]
        rms = (sum(x * x for x in middle) / len(middle)) ** 0.5
        assert 6500 < rms < 7600  # 10000 / sqrt(2) ~= 7071


# ---------------------------------------------------------------------------
# TestElevenLabsProvider
# ---------------------------------------------------------------------------


class TestElevenLabsProvider:
    def test_supports_transcription_false(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        p = ElevenLabsProvider(client=MagicMock())
        assert p.supports_transcription is False

    def test_supports_synthesis_true(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        p = ElevenLabsProvider(client=MagicMock())
        assert p.supports_synthesis is True

    @pytest.mark.asyncio
    async def test_transcribe_raises_not_implemented(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        p = ElevenLabsProvider(client=MagicMock())
        with pytest.raises(NotImplementedError):
            await p.transcribe(b"audio")

    @pytest.mark.asyncio
    async def test_synthesize_returns_audio(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        mock_client = MagicMock()
        mock_client.synthesize = AsyncMock(return_value=b"elevenlabs_audio")
        p = ElevenLabsProvider(client=mock_client)

        result = await p.synthesize("hi", language="hi")

        assert result == b"elevenlabs_audio"
        mock_client.synthesize.assert_called_once_with("hi", "hi")

    @pytest.mark.asyncio
    async def test_synthesize_raises_on_error(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        mock_client = MagicMock()
        mock_client.synthesize = AsyncMock(side_effect=RuntimeError("api fail"))
        p = ElevenLabsProvider(client=mock_client)

        with pytest.raises(ExternalServiceError):
            await p.synthesize("hello")

    @pytest.mark.asyncio
    async def test_synthesize_stream_yields_chunks(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        async def _mock_stream(*args, **kwargs):
            yield b"a"
            yield b"b"

        mock_client = MagicMock()
        mock_client.synthesize_stream = _mock_stream
        p = ElevenLabsProvider(client=mock_client)

        chunks = [c async for c in p.synthesize_stream("hello", language="en")]

        assert chunks == [b"a", b"b"]

    @pytest.mark.asyncio
    async def test_synthesize_stream_raises_on_error(self):
        from app.voice.elevenlabs_provider import ElevenLabsProvider

        mock_client = MagicMock()
        mock_client.synthesize_stream = MagicMock(
            return_value=_ErrorStream(RuntimeError("stream fail"))
        )
        p = ElevenLabsProvider(client=mock_client)

        with pytest.raises(ExternalServiceError):
            async for _ in p.synthesize_stream("hello"):
                pass


# ---------------------------------------------------------------------------
# TestSpeechToText
# ---------------------------------------------------------------------------


class TestSpeechToText:
    def test_raises_if_provider_lacks_transcription(self):
        from app.voice.stt import SpeechToText

        provider = _MockProvider(transcription_supported=False)
        with pytest.raises(ValueError, match="does not support transcription"):
            SpeechToText(provider)

    @pytest.mark.asyncio
    async def test_returns_transcript_on_success(self):
        from app.voice.stt import SpeechToText

        provider = _MockProvider(transcript="hello world", transcription_supported=True)
        stt = SpeechToText(provider, retry_delay=0)

        result = await stt.transcribe(b"audio", language="en")

        assert result == "hello world"
        assert provider.transcribe_calls == [(b"audio", "en")]

    @pytest.mark.asyncio
    async def test_empty_result_is_not_retried(self):
        """"" means "no speech in this audio" — a valid answer. Retrying it
        only doubled STT cost and added dead air on every noise burst."""
        from app.voice.stt import SpeechToText

        call_count = 0

        class _CountingProvider(_MockProvider):
            async def transcribe(self, audio, *, language=None):
                nonlocal call_count
                call_count += 1
                return ""

        provider = _CountingProvider(transcription_supported=True)
        stt = SpeechToText(provider, max_retries=2, retry_delay=0)

        with patch("asyncio.sleep", AsyncMock()):
            result = await stt.transcribe(b"audio")

        assert result == ""
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_external_service_error(self):
        from app.voice.stt import SpeechToText

        call_count = 0

        class _FailThenOk(_MockProvider):
            async def transcribe(self, audio, *, language=None):
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise ExternalServiceError("test", "fail")
                return "success"

        provider = _FailThenOk(transcription_supported=True)
        stt = SpeechToText(provider, max_retries=2, retry_delay=0)

        with patch("asyncio.sleep", AsyncMock()):
            result = await stt.transcribe(b"audio")

        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_returns_empty_after_max_retries(self):
        from app.voice.stt import SpeechToText

        provider = _MockProvider(transcript="", transcription_supported=True)
        stt = SpeechToText(provider, max_retries=2, retry_delay=0)

        with patch("asyncio.sleep", AsyncMock()):
            result = await stt.transcribe(b"audio")

        assert result == ""
        assert len(provider.transcribe_calls) == 1

    @pytest.mark.asyncio
    async def test_uses_fallback_after_primary_exhaustion(self):
        from app.voice.stt import SpeechToText

        primary = _MockProvider(
            transcription_error=ExternalServiceError("OpenAI", "down"),
            transcription_supported=True,
        )
        fallback = _MockProvider(transcript="fallback result", transcription_supported=True)
        stt = SpeechToText(primary, max_retries=1, retry_delay=0, fallback=fallback)

        result = await stt.transcribe(b"audio")

        assert result == "fallback result"
        assert len(fallback.transcribe_calls) == 1

    @pytest.mark.asyncio
    async def test_skips_fallback_if_no_transcription_support(self):
        from app.voice.stt import SpeechToText

        primary = _MockProvider(transcript="", transcription_supported=True)
        fallback = _MockProvider(transcription_supported=False)
        stt = SpeechToText(primary, max_retries=1, retry_delay=0, fallback=fallback)

        result = await stt.transcribe(b"audio")

        assert result == ""
        assert fallback.transcribe_calls == []

    @pytest.mark.asyncio
    async def test_fallback_failure_returns_empty(self):
        from app.voice.stt import SpeechToText

        primary = _MockProvider(transcript="", transcription_supported=True)
        fallback = _MockProvider(
            transcription_error=RuntimeError("fallback fail"),
            transcription_supported=True,
        )
        stt = SpeechToText(primary, max_retries=1, retry_delay=0, fallback=fallback)

        result = await stt.transcribe(b"audio")

        assert result == ""

    @pytest.mark.asyncio
    async def test_passes_language_to_provider(self):
        from app.voice.stt import SpeechToText

        provider = _MockProvider(transcript="नमस्ते", transcription_supported=True)
        stt = SpeechToText(provider, retry_delay=0)

        result = await stt.transcribe(b"audio", language="hi")

        assert result == "नमस्ते"
        assert provider.transcribe_calls[0][1] == "hi"


# ---------------------------------------------------------------------------
# TestTextToSpeech
# ---------------------------------------------------------------------------


class TestTextToSpeech:
    @pytest.mark.asyncio
    async def test_synthesize_returns_primary_audio(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(audio=b"mp3data")
        tts = TextToSpeech(primary)

        result = await tts.synthesize("hello")

        assert result == b"mp3data"

    @pytest.mark.asyncio
    async def test_synthesize_falls_back_on_empty_primary(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(audio=b"")
        fallback = _MockProvider(audio=b"fallback_audio")
        tts = TextToSpeech(primary, fallback=fallback)

        result = await tts.synthesize("hello")

        assert result == b"fallback_audio"

    @pytest.mark.asyncio
    async def test_synthesize_falls_back_on_exception(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(synthesis_error=ExternalServiceError("el", "fail"))
        fallback = _MockProvider(audio=b"backup_audio")
        tts = TextToSpeech(primary, fallback=fallback)

        result = await tts.synthesize("hello")

        assert result == b"backup_audio"

    @pytest.mark.asyncio
    async def test_synthesize_returns_empty_if_all_fail(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(synthesis_error=ExternalServiceError("el", "fail"))
        fallback = _MockProvider(synthesis_error=ExternalServiceError("oai", "fail"))
        tts = TextToSpeech(primary, fallback=fallback)

        result = await tts.synthesize("hello")

        assert result == b""

    @pytest.mark.asyncio
    async def test_synthesize_returns_empty_with_no_fallback(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(audio=b"")
        tts = TextToSpeech(primary)

        result = await tts.synthesize("hello")

        assert result == b""

    @pytest.mark.asyncio
    async def test_synthesize_stream_yields_chunks(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(stream_chunks=[b"a", b"b", b"c"])
        tts = TextToSpeech(primary)

        chunks = [c async for c in tts.synthesize_stream("hello")]

        assert chunks == [b"a", b"b", b"c"]

    @pytest.mark.asyncio
    async def test_synthesize_stream_falls_back_on_empty_primary(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(stream_chunks=[])
        fallback = _MockProvider(stream_chunks=[b"fb1", b"fb2"])
        tts = TextToSpeech(primary, fallback=fallback)

        chunks = [c async for c in tts.synthesize_stream("hello")]

        assert chunks == [b"fb1", b"fb2"]

    @pytest.mark.asyncio
    async def test_synthesize_stream_falls_back_on_exception(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(stream_error=ExternalServiceError("el", "stream fail"))
        fallback = _MockProvider(stream_chunks=[b"fb"])
        tts = TextToSpeech(primary, fallback=fallback)

        chunks = [c async for c in tts.synthesize_stream("hello")]

        assert chunks == [b"fb"]

    @pytest.mark.asyncio
    async def test_synthesize_stream_no_fallback_empty(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(stream_chunks=[])
        tts = TextToSpeech(primary)

        chunks = [c async for c in tts.synthesize_stream("hello")]

        assert chunks == []

    @pytest.mark.asyncio
    async def test_synthesize_stream_fallback_error_yields_nothing(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(stream_chunks=[])
        fallback = _MockProvider(stream_error=RuntimeError("fallback also fails"))
        tts = TextToSpeech(primary, fallback=fallback)

        chunks = [c async for c in tts.synthesize_stream("hello")]

        assert chunks == []

    @pytest.mark.asyncio
    async def test_synthesize_forwards_language_and_voice(self):
        from app.voice.tts import TextToSpeech

        primary = _MockProvider(audio=b"ok")
        tts = TextToSpeech(primary)

        await tts.synthesize("text", language="hi", voice="nova")

        assert primary.synthesize_calls[0] == ("text", "hi", "nova")


# ---------------------------------------------------------------------------
# TestVoiceManager
# ---------------------------------------------------------------------------


class TestVoiceManager:
    @pytest.mark.asyncio
    async def test_transcribe_delegates_to_stt(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        mock_stt = MagicMock(spec=SpeechToText)
        mock_stt.transcribe = AsyncMock(return_value="hello")
        vm = VoiceManager(stt=mock_stt, tts=MagicMock(spec=TextToSpeech))

        result = await vm.transcribe(b"audio", "en")

        assert result == "hello"
        mock_stt.transcribe.assert_called_once_with(b"audio", language="en")

    @pytest.mark.asyncio
    async def test_transcribe_returns_empty_on_error(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        mock_stt = MagicMock(spec=SpeechToText)
        mock_stt.transcribe = AsyncMock(side_effect=RuntimeError("stt fail"))
        vm = VoiceManager(stt=mock_stt, tts=MagicMock(spec=TextToSpeech))

        result = await vm.transcribe(b"audio")

        assert result == ""

    @pytest.mark.asyncio
    async def test_synthesize_delegates_to_tts(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        mock_tts = MagicMock(spec=TextToSpeech)
        mock_tts.synthesize = AsyncMock(return_value=b"audio_bytes")
        vm = VoiceManager(stt=MagicMock(spec=SpeechToText), tts=mock_tts)

        result = await vm.synthesize("hello", "en")

        assert result == b"audio_bytes"
        mock_tts.synthesize.assert_called_once_with("hello", language="en")

    @pytest.mark.asyncio
    async def test_synthesize_returns_empty_on_error(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        mock_tts = MagicMock(spec=TextToSpeech)
        mock_tts.synthesize = AsyncMock(side_effect=RuntimeError("tts fail"))
        vm = VoiceManager(stt=MagicMock(spec=SpeechToText), tts=mock_tts)

        result = await vm.synthesize("hello")

        assert result == b""

    @pytest.mark.asyncio
    async def test_synthesize_to_stream_yields_chunks(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        async def _mock_stream(*args, **kwargs):
            yield b"a"
            yield b"b"

        mock_tts = MagicMock(spec=TextToSpeech)
        mock_tts.synthesize_stream = _mock_stream
        vm = VoiceManager(stt=MagicMock(spec=SpeechToText), tts=mock_tts)

        chunks = [c async for c in vm.synthesize_to_stream("hello")]

        assert chunks == [b"a", b"b"]

    @pytest.mark.asyncio
    async def test_synthesize_to_stream_handles_error(self):
        from app.voice.stt import SpeechToText
        from app.voice.tts import TextToSpeech
        from app.voice.voice_manager import VoiceManager

        mock_tts = MagicMock(spec=TextToSpeech)
        mock_tts.synthesize_stream = MagicMock(return_value=_ErrorStream(RuntimeError("fail")))
        vm = VoiceManager(stt=MagicMock(spec=SpeechToText), tts=mock_tts)

        chunks = [c async for c in vm.synthesize_to_stream("hello")]

        assert chunks == []


# ---------------------------------------------------------------------------
# TestWrapPcm16Wav — incoming audio container fix (AgentStream: PCM16LE)
# ---------------------------------------------------------------------------


class TestWrapPcm16Wav:
    """Exotel AgentStream/Voicebot's media-stream payload is raw signed
    16-bit little-endian PCM/8000Hz/mono with no container. Whisper's
    ffmpeg-based decoder needs an actual RIFF/WAVE structure, not headerless
    bytes merely named "audio.wav" — this locks in the fix (WAVE_FORMAT_PCM,
    tag 1) field by field, and proves it decodes with the stdlib wave module."""

    def test_produces_valid_riff_wave_pcm_header(self):
        import struct

        from app.voice.audio_stream import _wrap_pcm16_wav

        raw = struct.pack("<80h", *range(80))  # 10ms of PCM16 samples at 8000Hz
        wav = _wrap_pcm16_wav(raw, sample_rate=8000)

        riff_id, riff_size, wave_id = struct.unpack("<4sI4s", wav[0:12])
        assert riff_id == b"RIFF"
        assert wave_id == b"WAVE"
        assert riff_size == len(wav) - 8

        fmt_id, fmt_size, audio_format, channels, sample_rate, byte_rate, block_align, bits = (
            struct.unpack("<4sIHHIIHH", wav[12:36])
        )
        assert fmt_id == b"fmt "
        assert fmt_size == 16
        assert audio_format == 1  # WAVE_FORMAT_PCM
        assert channels == 1
        assert sample_rate == 8000
        assert byte_rate == 16000  # sample_rate * channels * bytes/sample (2)
        assert block_align == 2
        assert bits == 16

        data_id, data_size = struct.unpack("<4sI", wav[36:44])
        assert data_id == b"data"
        assert data_size == len(raw)
        assert wav[44:] == raw

    def test_preserves_audio_bytes_unchanged(self):
        """No transcoding — same PCM16 samples, just containerized."""
        import struct

        from app.voice.audio_stream import _wrap_pcm16_wav

        raw = struct.pack("<4h", 0, 12345, -12345, 32767)
        wav = _wrap_pcm16_wav(raw, sample_rate=8000)

        assert wav.endswith(raw)
        assert len(wav) == len(raw) + 44  # RIFF(12) + fmt(24) + data header(8)

    def test_different_sample_rate_reflected_in_header(self):
        import struct

        from app.voice.audio_stream import _wrap_pcm16_wav

        wav = _wrap_pcm16_wav(b"\x00" * 10, sample_rate=16000)
        sample_rate, byte_rate = struct.unpack("<II", wav[24:32])
        assert sample_rate == 16000
        assert byte_rate == 32000

    def test_decodes_correctly_with_stdlib_wave_module(self):
        """Independent proof beyond our own struct packing: Python's stdlib
        `wave` reader (a different code path than our writer) must parse the
        header and recover the exact original samples."""
        import io
        import struct
        import wave

        from app.voice.audio_stream import _wrap_pcm16_wav

        samples = [0, 1000, -1000, 32767, -32768, 42, -42]
        raw = struct.pack(f"<{len(samples)}h", *samples)
        wav_bytes = _wrap_pcm16_wav(raw, sample_rate=8000)

        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            assert reader.getnchannels() == 1
            assert reader.getsampwidth() == 2
            assert reader.getframerate() == 8000
            assert reader.getnframes() == len(samples)
            frames = reader.readframes(reader.getnframes())

        assert frames == raw
        assert list(struct.unpack(f"<{len(samples)}h", frames)) == samples


# ---------------------------------------------------------------------------
# TestAudioBuffer
# ---------------------------------------------------------------------------


def _tone(num_samples: int, amplitude: int = 8000) -> bytes:
    """Deterministic non-silent PCM16LE audio, loud/long enough (>= the
    default speech_start_ms=200ms => 10 frames => 3200 bytes when
    num_samples >= 1600) for UtteranceSegmenter to confirm speech began."""
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude) for i in range(num_samples)
    )


def _silence(num_samples: int) -> bytes:
    return b"\x00" * (num_samples * 2)


def _complete_utterance(loud_samples: int = 2000, silent_samples: int = 6400) -> bytes:
    """A full, self-finalizing utterance under AudioBuffer's default
    settings: enough sustained loud samples (>= 1600, i.e. 10 frames) to
    confirm speech began, then enough silence (>= 5600 samples, i.e. 35
    frames) to confirm the caller stopped — so push() finalizes it and
    schedules a flush entirely on its own."""
    return _tone(loud_samples) + _silence(silent_samples)


class TestAudioBuffer:
    @pytest.mark.asyncio
    async def test_flush_called_on_drain(self):
        from app.voice.audio_stream import AudioBuffer

        received = []

        async def capture(data: bytes):
            received.append(data)

        buf = AudioBuffer(on_flush=capture, silence_ms=5000)
        speech = _tone(2400)  # 4800 bytes = 15 whole 320-byte frames, over the 10-frame floor
        await buf.push(speech)  # sustained speech, not yet silent
        await buf.drain()

        assert len(received) == 1
        # Flushed audio is wrapped in a real WAV container (not raw bytes
        # mislabeled as .wav) — see TestWrapPcm16Wav for the format details.
        assert received[0].startswith(b"RIFF")
        assert speech in received[0]

    @pytest.mark.asyncio
    async def test_flush_wraps_audio_at_configured_sample_rate(self):
        from app.voice.audio_stream import AudioBuffer

        received = []

        async def capture(data: bytes):
            received.append(data)

        buf = AudioBuffer(on_flush=capture, silence_ms=5000, sample_rate=16000)
        # At 16kHz, one 20ms frame is 640 bytes (320 samples) — need >= 8
        # frames (2560 samples) to confirm speech-start; use a whole number
        # of frames (14) so no trailing partial frame is left unflushed.
        await buf.push(_tone(320 * 14))
        await buf.drain()

        sample_rate = struct.unpack("<I", received[0][24:28])[0]
        assert sample_rate == 16000

    @pytest.mark.asyncio
    async def test_max_bytes_safety_cap_forces_flush(self):
        """max_bytes is a safety cap now, not the primary turn boundary —
        but it must still force-finalize an utterance that never goes
        silent, so a stuck-open line can't grow memory unbounded."""
        from app.voice.audio_stream import AudioBuffer

        received = []

        async def capture(data: bytes):
            received.append(data)

        buf = AudioBuffer(on_flush=capture, max_bytes=3000, silence_ms=5000)
        await buf.push(_tone(2000))  # 4000 bytes of continuous speech > max_bytes=3000

        await asyncio.sleep(0.01)
        assert len(received) >= 1
        await buf.drain()

    @pytest.mark.asyncio
    async def test_max_bytes_flush_does_not_block_push(self):
        """Regression: a real PSTN call showed push() blocking the WebSocket
        receive loop for the full STT->OpenAI->TTS turn (~12s) whenever a
        flush was triggered, because it used to be awaited inline. push()
        must return almost immediately regardless of how slow on_flush is,
        whether the trigger is natural end-of-speech or the max_bytes safety
        cap."""
        import time

        from app.voice.audio_stream import AudioBuffer

        flush_started = asyncio.Event()
        release_flush = asyncio.Event()

        async def slow_flush(data: bytes):
            flush_started.set()
            await release_flush.wait()  # simulates a long AI turn

        buf = AudioBuffer(on_flush=slow_flush, max_bytes=3000, silence_ms=5000)

        t0 = time.monotonic()
        await buf.push(_tone(2000))  # exceeds max_bytes=3000
        elapsed = time.monotonic() - t0

        assert elapsed < 0.1, f"push() blocked for {elapsed:.2f}s — flush must be backgrounded"
        await asyncio.wait_for(flush_started.wait(), timeout=1)

        release_flush.set()
        await buf.drain()  # let the flush task finish and cancel the watchdog

    @pytest.mark.asyncio
    async def test_receive_loop_keeps_consuming_media_during_slow_ai_turn(self):
        """End-to-end version of the same regression at the ExotelAudioStream
        level: incoming 'media' events must keep being processed (reaching
        on_audio) while a previous turn's AI processing is still running in
        the background, proving the WebSocket receive loop is never stalled."""
        import base64
        import json

        from app.telephony.exotel.stream import ExotelAudioStream
        from app.voice.audio_stream import AudioBuffer

        class _FakeWs:
            """Yields control between messages (like real network I/O would)
            so a background flush task genuinely gets a chance to interleave
            with the receive loop, instead of everything running in one
            uninterrupted synchronous burst."""

            def __init__(self, messages: list[str]) -> None:
                self._messages = messages

            async def iter_text(self):
                for msg in self._messages:
                    await asyncio.sleep(0)
                    yield msg

        turn_started = asyncio.Event()
        release_turn = asyncio.Event()

        async def slow_ai_turn(_wav_bytes: bytes):
            turn_started.set()
            await release_turn.wait()

        buffer = AudioBuffer(on_flush=slow_ai_turn, max_bytes=3000, silence_ms=5000)
        media_chunks_received = []

        async def on_audio(chunk: bytes) -> None:
            media_chunks_received.append(chunk)
            await buffer.push(chunk)

        big_chunk = base64.b64encode(_tone(2000)).decode()  # exceeds max_bytes=3000
        ws = _FakeWs(
            [
                json.dumps(
                    {"event": "media", "stream_sid": "MZ1", "media": {"payload": big_chunk}}
                ),
                json.dumps(
                    {
                        "event": "media",
                        "stream_sid": "MZ1",
                        "media": {"payload": base64.b64encode(b"more-audio").decode()},
                    }
                ),
                json.dumps({"event": "stop", "stream_sid": "MZ1"}),
            ]
        )
        stream = ExotelAudioStream(websocket=ws, on_audio=on_audio)

        await asyncio.wait_for(stream.start(), timeout=2)

        # Both media events were consumed by the receive loop even though the
        # AI turn triggered by the first one is still pending in the background.
        assert len(media_chunks_received) == 2
        assert turn_started.is_set()
        assert not release_turn.is_set()

        release_turn.set()
        await buffer.drain()  # let the flush task finish and cancel the watchdog

    @pytest.mark.asyncio
    async def test_overlapping_flush_triggers_do_not_run_concurrent_turns(self):
        """Regression: a real PSTN call showed transcript='you' logged twice
        with message_count jumping by 2 (two overlapping turns), and
        'Bye-bye.' processed twice — duplicate conversational responses for
        one call. While a turn is in progress, a second finalized utterance
        must queue, not run concurrently with the first."""
        from app.voice.audio_stream import AudioBuffer

        turn_count = 0
        concurrent_peak = 0
        currently_running = 0
        release_first_turn = asyncio.Event()

        async def on_flush(data: bytes):
            nonlocal turn_count, concurrent_peak, currently_running
            turn_count += 1
            currently_running += 1
            concurrent_peak = max(concurrent_peak, currently_running)
            if turn_count == 1:
                await release_first_turn.wait()
            currently_running -= 1

        buf = AudioBuffer(on_flush=on_flush, silence_ms=5000)

        await buf.push(_complete_utterance())  # confirms + finalizes, schedules turn 1
        await asyncio.sleep(0)  # let turn 1 actually start

        # A second utterance finishes while turn 1 is still running.
        await buf.push(_complete_utterance())
        await asyncio.sleep(0)

        assert concurrent_peak == 1, "a second turn ran concurrently with the first"
        assert turn_count == 1, "flush attempted a second on_flush before the first finished"

        release_first_turn.set()
        await asyncio.sleep(0.01)  # let turn 1 finish and turn 2 flush from the queue

        assert turn_count == 2, "audio finalized during turn 1 was lost, not queued for turn 2"
        assert concurrent_peak == 1
        await buf.drain()

    @pytest.mark.asyncio
    async def test_different_calls_process_concurrently(self):
        """The turn-serialization guard is per-AudioBuffer-instance (per
        call) — it must never block a different call's turn."""
        from app.voice.audio_stream import AudioBuffer

        call_a_started = asyncio.Event()
        call_b_started = asyncio.Event()
        release_calls = asyncio.Event()

        async def on_flush_a(data: bytes):
            call_a_started.set()
            await release_calls.wait()

        async def on_flush_b(data: bytes):
            call_b_started.set()
            await release_calls.wait()

        buf_a = AudioBuffer(on_flush=on_flush_a, silence_ms=5000, call_sid="CA_A")
        buf_b = AudioBuffer(on_flush=on_flush_b, silence_ms=5000, call_sid="CA_B")

        await buf_a.push(_complete_utterance())
        await buf_b.push(_complete_utterance())

        # Both calls' turns must be able to start without waiting on each other.
        await asyncio.wait_for(call_a_started.wait(), timeout=1)
        await asyncio.wait_for(call_b_started.wait(), timeout=1)

        release_calls.set()
        await buf_a.drain()
        await buf_b.drain()

    @pytest.mark.asyncio
    async def test_stall_watchdog_flush_does_not_block(self):
        """The stall-watchdog flush path (force-finalizing an in-progress
        utterance if audio genuinely stops arriving) must be as
        non-blocking as the others — push() and subsequent code must not
        stall waiting for a slow on_flush triggered by the watchdog."""
        import time

        from app.voice.audio_stream import AudioBuffer

        release_flush = asyncio.Event()

        async def slow_flush(data: bytes):
            await release_flush.wait()

        buf = AudioBuffer(on_flush=slow_flush, silence_ms=1, max_bytes=999_999)
        await buf.push(_tone(2000))  # sustained speech, in progress — never goes silent

        t0 = time.monotonic()
        await asyncio.sleep(0.05)  # let the stall watchdog fire
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2  # sleep(0.05) itself returned promptly, not blocked further

        release_flush.set()
        await buf.drain()

    @pytest.mark.asyncio
    async def test_cancel_pending_produces_no_unhandled_exception(self):
        """Cancelling the pending stall watchdog (e.g. when a call stops)
        must not surface as an unhandled asyncio exception/warning."""
        from app.voice.audio_stream import AudioBuffer

        called = []

        async def on_flush(data: bytes):
            called.append(data)

        buf = AudioBuffer(on_flush=on_flush, silence_ms=5000, max_bytes=999_999)
        await buf.push(_tone(2000))  # confirms speech, in progress — not yet finalized
        await buf.cancel_pending()

        # Give the cancelled task a chance to be garbage-collected/settled.
        await asyncio.sleep(0)
        assert called == []  # cancelled before it could fire

    @pytest.mark.asyncio
    async def test_empty_drain_does_nothing(self):
        from app.voice.audio_stream import AudioBuffer

        called = []

        async def capture(data: bytes):
            called.append(data)

        buf = AudioBuffer(on_flush=capture)
        await buf.drain()
        assert called == []


# ---------------------------------------------------------------------------
# TestExotelAudioStream (telephony integration — kept for regression)
# ---------------------------------------------------------------------------


class TestExotelAudioStream:
    @pytest.mark.asyncio
    async def test_parses_start_event(self):
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        received = []

        async def on_audio(data):
            received.append(data)

        stream = ExotelAudioStream(ws, on_audio=on_audio)

        start_msg = json.dumps(
            {
                "event": "start",
                "stream_sid": "MZ123",
                "start": {"call_sid": "CA456"},
            }
        )
        await stream._handle_message(start_msg)
        assert stream.stream_sid == "MZ123"
        assert stream.call_sid == "CA456"

    @pytest.mark.asyncio
    async def test_start_event_fires_on_start_callback(self):
        """Regression: on_start must actually fire for a real Exotel start
        event — this was silently never firing because call_sid parsed to
        None (wrong field name), so the `call_sid is not None` gate never
        passed and SpeechHandler was never initialized."""
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        on_start = AsyncMock()
        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock(), on_start=on_start)

        start_msg = json.dumps(
            {
                "event": "start",
                "stream_sid": "MZ123",
                "start": {"call_sid": "CA456", "from": "+919876543210"},
            }
        )
        await stream._handle_message(start_msg)

        on_start.assert_awaited_once_with("CA456", "+919876543210")

    @pytest.mark.asyncio
    async def test_stop_event_fires_on_stop_callback_immediately(self):
        """on_stop must fire the instant 'stop' is parsed — before the
        receive loop even finishes — so SpeechHandler can be marked closed
        well before finalize() runs its trailing buffer drain."""
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        on_stop = AsyncMock()
        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock(), on_stop=on_stop)
        stream._call_sid = "CA456"

        await stream._handle_message(json.dumps({"event": "stop", "stream_sid": "MZ123"}))

        on_stop.assert_awaited_once_with("CA456")

    @pytest.mark.asyncio
    async def test_parses_real_agentstream_start_event_full_shape(self):
        """Locks in the exact shape Exotel's AgentStream/Voicebot actually
        sends (confirmed against developer.exotel.com/docs/agentstream/
        websocket-protocol) — snake_case throughout, caller number as a
        direct `start.from` field, not nested under custom_parameters."""
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        stream = ExotelAudioStream(AsyncMock(), on_audio=AsyncMock())
        start_msg = json.dumps(
            {
                "event": "start",
                "sequence_number": "1",
                "stream_sid": "MZxxxxxxxxxxxxxxxxxxxxxxxx",
                "start": {
                    "stream_sid": "MZxxxxxxxxxxxxxxxxxxxxxxxx",
                    "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxx",
                    "account_sid": "ACxxxxxxxxxxxxxxxxxxxxxxxx",
                    "from": "+919876543210",
                    "to": "+918047491899",
                    "custom_parameters": {"key1": "value1"},
                    "media_format": {
                        "encoding": "audio/x-raw",
                        "sample_rate": "8000",
                        "bit_rate": "16",
                    },
                },
            }
        )
        await stream._handle_message(start_msg)

        assert stream.stream_sid == "MZxxxxxxxxxxxxxxxxxxxxxxxx"
        assert stream.call_sid == "CAxxxxxxxxxxxxxxxxxxxxxxxx"
        assert stream.caller_number == "+919876543210"

    @pytest.mark.asyncio
    async def test_old_camel_case_fields_no_longer_the_expected_format(self):
        """Documents the actual bug: the previously-used camelCase field
        names (streamSid/callSid/customParameters) do not exist in Exotel's
        real protocol, so a message shaped that way correctly fails to
        populate call_sid/stream_sid — this is the exact bug that caused
        was_initialized=false on every real call."""
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        stream = ExotelAudioStream(AsyncMock(), on_audio=AsyncMock())
        old_shape_msg = json.dumps(
            {
                "event": "start",
                "streamSid": "MZ123",
                "start": {"callSid": "CA456", "customParameters": {"From": "+91000"}},
            }
        )
        await stream._handle_message(old_shape_msg)

        assert stream.call_sid is None
        assert stream.stream_sid is None

    @pytest.mark.asyncio
    async def test_parses_media_event(self):
        import base64
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        received = []

        async def on_audio(data):
            received.append(data)

        stream = ExotelAudioStream(ws, on_audio=on_audio)
        payload = base64.b64encode(b"\x00\x01\x02").decode()
        media_msg = json.dumps({"event": "media", "media": {"payload": payload}})
        await stream._handle_message(media_msg)

        assert len(received) == 1
        assert received[0] == b"\x00\x01\x02"

    @pytest.mark.asyncio
    async def test_stop_event_sets_running_false(self):
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._running = True
        stream._stream_sid = "MZ123"

        await stream._handle_message(json.dumps({"event": "stop"}))
        assert not stream._running

    @pytest.mark.asyncio
    async def test_send_audio_sends_json_media_text_frame(self):
        """send_audio() must build Exotel's exact protocol frame and send it as
        TEXT, never a raw binary frame."""
        import base64
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._stream_sid = "MZ999"

        await stream.send_audio(b"\x01\x02\x03")

        ws.send_text.assert_called_once()
        ws.send_bytes.assert_not_called()
        frame = json.loads(ws.send_text.call_args[0][0])
        assert frame == {
            "event": "media",
            "stream_sid": "MZ999",
            "media": {"payload": base64.b64encode(b"\x01\x02\x03").decode()},
        }

    @pytest.mark.asyncio
    async def test_clear_audio_sends_exotel_clear_event(self):
        import json

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._stream_sid = "MZ999"
        await stream.clear_audio()
        assert json.loads(ws.send_text.call_args.args[0]) == {
            "event": "clear",
            "stream_sid": "MZ999",
        }

    @pytest.mark.asyncio
    async def test_send_audio_drops_frame_when_stream_sid_missing(self):
        """Sending before a 'start' event (no stream_sid yet) must not emit
        an invalid frame with stream_sid=null."""
        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        assert stream.stream_sid is None

        await stream.send_audio(b"\x01\x02\x03")

        ws.send_text.assert_not_called()
        ws.send_bytes.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_audio_preserves_pcm16_samples_through_frame(self):
        """End-to-end frame check: real PCM16LE sample values must survive
        send_audio()'s base64/JSON round trip unchanged."""
        import base64
        import json
        import struct

        from app.telephony.exotel.stream import ExotelAudioStream

        samples = [0, 12345, -12345, 32767, -32768]
        pcm16_bytes = struct.pack(f"<{len(samples)}h", *samples)

        ws = AsyncMock()
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._stream_sid = "MZ_PCM"

        await stream.send_audio(pcm16_bytes)

        frame = json.loads(ws.send_text.call_args[0][0])
        assert frame["event"] == "media"
        assert frame["stream_sid"] == "MZ_PCM"
        decoded_bytes = base64.b64decode(frame["media"]["payload"])
        assert decoded_bytes == pcm16_bytes
        assert struct.unpack(f"<{len(samples)}h", decoded_bytes) == tuple(samples)

    @pytest.mark.asyncio
    async def test_send_audio_skips_when_websocket_disconnected(self):
        """Regression: a real PSTN call showed send_audio() attempted after
        Exotel had already hung up, raising 'WebSocket is not connected.
        Need to call accept first.' send_audio() must check the socket's
        state first and drop the frame instead of attempting (and failing)
        the send."""
        from starlette.websockets import WebSocketState

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        ws.application_state = WebSocketState.DISCONNECTED
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._stream_sid = "MZ1"

        await stream.send_audio(b"\x01\x02\x03")

        ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_audio_proceeds_when_websocket_connected(self):
        """Sanity check for the same guard: a genuinely CONNECTED state must
        not block sending."""
        from starlette.websockets import WebSocketState

        from app.telephony.exotel.stream import ExotelAudioStream

        ws = AsyncMock()
        ws.application_state = WebSocketState.CONNECTED
        stream = ExotelAudioStream(ws, on_audio=AsyncMock())
        stream._stream_sid = "MZ1"

        await stream.send_audio(b"\x01\x02\x03")

        ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_send_audio_calls_are_serialized(self):
        """Now that flushes run as background tasks, two AI turns can finish
        around the same time and both try to send a reply — outbound writes
        to the single shared socket must be serialized, not interleaved."""
        import asyncio as _asyncio

        from app.telephony.exotel.stream import ExotelAudioStream

        send_order: list[str] = []

        class _SlowWs:
            application_state = None

            async def send_text(self, message: str) -> None:
                send_order.append("start:" + message)
                await _asyncio.sleep(0.01)
                send_order.append("end:" + message)

        stream = ExotelAudioStream(_SlowWs(), on_audio=AsyncMock())
        stream._stream_sid = "MZ1"

        await _asyncio.gather(
            stream.send_audio(b"\x01"),
            stream.send_audio(b"\x02"),
        )

        # Each send's start/end must be adjacent — never interleaved with
        # the other send's start/end in between.
        assert send_order[0].startswith("start:")
        assert send_order[1].startswith("end:")
        assert send_order[2].startswith("start:")
        assert send_order[3].startswith("end:")
