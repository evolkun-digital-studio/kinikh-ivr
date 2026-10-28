"""ElevenLabs TTS client — converts text to PCM 16-bit/8000Hz/mono audio bytes.

Uses a SINGLE persistent httpx.AsyncClient for the process lifetime, so
TLS/connection setup is amortized across all synthesis calls — not paid
fresh on every caller turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json

import httpx
from loguru import logger

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError
from app.voice.pcm import Downsampler


# Global persistent HTTP client — reused across ALL TTS calls for this
# process. Avoids TLS handshake + TCP connection per synthesis,
# which was adding 200-500ms of latency to every TTS request.
_http_client: httpx.AsyncClient | None = None

def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _http_client


_STATUS_HINTS: dict[int, str] = {
    401: "invalid API key or quota used up",
    402: "payment required / plan limit",
    403: "not allowed on this plan (voice, model or output format)",
    404: "voice ID not found in this account",
    422: "invalid request (model / output format / voice settings)",
    429: "rate limit or too many concurrent requests",
}


def _log_elevenlabs_failure(http_status: int, body: bytes) -> None:
    """Log ElevenLabs' real HTTP status and error code for a failed request.

    The ExternalServiceError raised afterwards always carries our own 502,
    which hid the actual cause (bad key, quota, unknown voice, ...). Only
    the status and ElevenLabs' short error code are logged — never the
    request text or the API key.
    """
    reason = "unknown"
    try:
        detail = json.loads(body).get("detail")
        value = None
        if isinstance(detail, dict):
            value = detail.get("status") or detail.get("code")
        elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
            value = detail[0].get("type") or detail[0].get("msg")
        if value:
            reason = str(value)[:60]
    except Exception:
        reason = "unknown"  # body was not the JSON error shape ElevenLabs documents
    try:
        hint = _STATUS_HINTS.get(http_status)
        if hint is None:
            hint = "ElevenLabs service/network problem" if http_status >= 500 else "unknown"
    except Exception:
        hint = "unknown"
    logger.error(f"[ELEVENLABS] http_status={http_status} reason={reason} hint={hint}")


_NEUTRAL_STYLE = "neutral_indian"
# Language code -> settings key part (ELEVENLABS_VOICE_<LANG>_<STYLE>).
_LANGUAGE_KEYS = {"en": "en", "hi": "hi", "hi-en": "hinglish"}
# Speech style -> settings key part. Add a style here plus its settings
# fields to make a curated voice configurable for it.
_STYLE_KEYS = {
    _NEUTRAL_STYLE: "neutral",
    "gujarati_influenced": "gujarati",
    "punjabi_influenced": "punjabi",
    "bengali_influenced": "bengali",
    "marathi_influenced": "marathi",
    "south_indian_influenced": "south_indian",
}


def _setting(settings: object, name: str) -> str:
    """A configured voice ID, or "" when unset (or not a string)."""
    value = getattr(settings, name, "")
    return value.strip() if isinstance(value, str) else ""


class ElevenLabsClient:
    """Async ElevenLabs TTS client supporting Hindi and English voices."""

    _BASE_URL = "https://api.elevenlabs.io/v1"
    # pcm_16000 is requested (a format verified to work on this account)
    # and converted to Exotel's 8 kHz with a proper anti-aliasing filter
    # (app/voice/pcm.py). The old "drop every other sample" conversion had
    # no filter and folded 4-8 kHz sibilance back into the audible band —
    # heard on calls as hiss/metallic noise over every assistant sentence.
    _OUTPUT_FORMAT = "pcm_16000"
    _SOURCE_RATE = 16000

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.elevenlabs_api_key
        self._model_id = settings.elevenlabs_model_id
        default_voice = _setting(settings, "elevenlabs_voice_id_en")
        hindi_voice = _setting(settings, "elevenlabs_voice_id_hi")
        neutral_hi = _setting(settings, "elevenlabs_voice_hi_neutral") or hindi_voice
        self._default_voice = default_voice
        # Neutral voice per language (fallback for every speech style).
        self._voice_ids = {
            "en": _setting(settings, "elevenlabs_voice_en_neutral") or default_voice,
            "hi": neutral_hi,
            # eleven_multilingual_v2 renders whichever language is actually
            # present in the text using the selected voice's timbre/accent —
            # the "voice" doesn't lock output to one language. For
            # code-switched Hindi-English replies aimed at a Hindi-speaking
            # caller, the Hindi voice reads more naturally than the English
            # one attempting the Hindi portions. This is a judgment call
            # (not verifiable by listening in this environment) — flip back
            # to elevenlabs_voice_id_en here if live calls sound wrong.
            "hi-en": _setting(settings, "elevenlabs_voice_hinglish_neutral") or neutral_hi,
        }
        # Optional curated voices per (language, speech style), read from
        # ELEVENLABS_VOICE_<LANG>_<STYLE> settings; missing ones are skipped.
        self._style_voice_ids: dict[tuple[str, str], str] = {}
        for language, lang_key in _LANGUAGE_KEYS.items():
            for style in _STYLE_KEYS:
                voice_id = _setting(settings, f"elevenlabs_voice_{lang_key}_{_STYLE_KEYS[style]}")
                if voice_id and style != _NEUTRAL_STYLE:
                    self._style_voice_ids[(language, style)] = voice_id
        self._http = _get_http_client()

    def has_style_voice(self, language: str, speech_style: str) -> bool:
        """True if a curated voice is configured for exactly this pair."""
        return (language, speech_style) in self._style_voice_ids

    @property
    def model_id(self) -> str:
        return self._model_id

    def _voice_for(self, language: str, speech_style: str | None = None) -> str:
        """Voice ID for (language, speech style): the exact curated voice,
        else the neutral voice for the language, else the default voice.
        Never fails for a missing specialised voice."""
        slot = "default"
        voice_id = None
        if speech_style and speech_style != _NEUTRAL_STYLE:
            voice_id = self._style_voice_ids.get((language, speech_style))
            slot = f"{language}/{speech_style}"
        if not voice_id:
            voice_id = self._voice_ids.get(language)
            slot = f"{language}/{_NEUTRAL_STYLE}"
        if not voice_id:
            voice_id = self._default_voice
            slot = "default"
        # Logs the slot, not the configured voice ID.
        logger.info(f"[TTS] language={language} voice_slot={slot} model={self._model_id}")
        return voice_id

    def _headers(self) -> dict[str, str]:
        return {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    def _payload(self, text: str) -> dict:
        return {
            "text": text,
            "model_id": self._model_id,
            # Higher stability = steadier, less breathy delivery, which
            # survives the narrow-band phone line better; speaker boost off
            # (it adds processing artefacts at 8 kHz).
            "voice_settings": {
                "stability": 0.6,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": False,
            },
        }

    async def synthesize(
        self, text: str, language: str = "en", *, speech_style: str | None = None
    ) -> bytes:
        """Return complete PCM audio bytes (signed 16-bit little-endian,
        8000Hz, mono) for the given text — the raw format Exotel's
        AgentStream/Voicebot media stream expects."""
        voice_id = self._voice_for(language, speech_style)
        url = f"{self._BASE_URL}/text-to-speech/{voice_id}"
        response = await self._http.post(
            url,
            params={"output_format": self._OUTPUT_FORMAT},
            json=self._payload(text),
            headers=self._headers(),
        )
        if response.status_code != 200:
            _log_elevenlabs_failure(response.status_code, response.content)
            raise ExternalServiceError("ElevenLabs", f"TTS failed: {response.text}")
        
        resampler = Downsampler(self._SOURCE_RATE)
        return resampler.process(response.content) + resampler.flush()

    async def synthesize_stream(
        self, text: str, language: str = "en", *, speech_style: str | None = None
    ) -> AsyncIterator[bytes]:
        """Stream audio chunks for lower latency, resampling to 8kHz on the fly."""
        voice_id = self._voice_for(language, speech_style)
        url = f"{self._BASE_URL}/text-to-speech/{voice_id}/stream"
        async with self._http.stream(
            "POST",
            url,
            params={"output_format": self._OUTPUT_FORMAT},
            json=self._payload(text),
            headers=self._headers(),
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                _log_elevenlabs_failure(response.status_code, body)
                raise ExternalServiceError("ElevenLabs", f"Stream failed: {body.decode()}")
            
            resampler = Downsampler(self._SOURCE_RATE)
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if chunk:
                    out = resampler.process(chunk)
                    if out:
                        yield out
            tail = resampler.flush()
            if tail:
                yield tail

    async def close(self) -> None:
        """Close the persistent HTTP client (process shutdown)."""
        await self._http.aclose()
