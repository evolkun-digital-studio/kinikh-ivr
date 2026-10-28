"""Language detection — heuristic fast-path then OpenAI fallback.

The live call path uses only the local quick_detect()/analyze_language();
LanguageDetector (LLM fallback) is kept for offline/batch callers.
"""

from __future__ import annotations

from loguru import logger

from app.ai.language_profile import UNKNOWN, analyze_language
from app.ai.openai_client import OpenAIClient, get_openai_client
from app.ai.prompt_manager import PromptManager
from app.core.call_logging import describe_error

# Devanagari Unicode block: U+0900-U+097F
_DEVANAGARI_START = 0x0900
_DEVANAGARI_END = 0x097F


def _has_devanagari(text: str) -> bool:
    return any(_DEVANAGARI_START <= ord(c) <= _DEVANAGARI_END for c in text)


def _has_latin_words(text: str) -> bool:
    return any(c.isascii() and c.isalpha() for c in text)


def quick_detect(text: str) -> str:
    """Instant, offline language guess for one caller utterance: "hi",
    "hi-en" (Hinglish) or "en" ("en" when there is no evidence either way).
    No API call — see app.ai.language_profile.analyze_language for the full
    analysis (confidence, meaningful, mixed) used on the live call path."""
    language = analyze_language(text, min_words=1).language
    return "en" if language == UNKNOWN else language


class LanguageDetector:
    """Detects language of caller input — en, hi, or hi-en (Hinglish)."""

    _VALID: frozenset[str] = frozenset({"en", "hi", "hi-en"})

    def __init__(self, client: OpenAIClient | None = None) -> None:
        self._client = client or get_openai_client()
        self._prompt_manager = PromptManager()

    async def detect(self, text: str) -> str:
        """Return language code for the given text. See
        detect_with_confidence() for the confidence flag — kept as a
        separate method so existing callers of detect() are unaffected."""
        language, _confident = await self.detect_with_confidence(text)
        return language

    async def detect_with_confidence(self, text: str) -> tuple[str, bool]:
        """Return (language code, is_confident).

        Uses Devanagari script detection as a fast path to avoid API calls.
        Falls back to LLM classification for romanized Hindi or ambiguous
        text. `is_confident` is False whenever the result is really just a
        "couldn't tell, defaulting to en" fallback (empty/garbled
        transcript, an LLM answer outside the known language codes, or an
        API failure) — a real PSTN call showed one noisy/garbled early
        utterance permanently locking the whole rest of a Hindi-speaking
        call to English, because the caller (SpeechHandler) couldn't tell
        the difference between a confident "en" and a shrug. Callers should
        only treat the language as settled for the call when this is True.
        """
        if not text.strip():
            return "en", False

        # Script-based heuristics — covers the majority of cases without an
        # API call, and is always a confident result.
        has_dev = _has_devanagari(text)
        has_lat = _has_latin_words(text)
        if has_dev and has_lat:
            return "hi-en", True
        if has_dev:
            return "hi", True

        # LLM path: romanized Hindi or genuinely ambiguous text.
        prompt = self._prompt_manager.language_detection(text)
        logger.info("[AI_CALL] purpose=language_detection")
        try:
            result = await self._client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=10,
            )
            result = result.strip().lower()
            if result in self._VALID:
                return result, True
            logger.warning(
                f"[lang_detector] LLM returned invalid code {result!r}, defaulting to en"
            )
            return "en", False
        except Exception as exc:
            logger.warning(
                f"[lang_detector] detection failed: {describe_error(exc)}, defaulting to en"
            )
            return "en", False
