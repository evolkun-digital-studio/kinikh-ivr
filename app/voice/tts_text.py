"""Text normalization applied just before text-to-speech.

Brand names are spelled for the eye ("KINIKH", "evolKun"), and TTS voices
read them wrongly: ALL-CAPS words get spelled out letter by letter, and
mixed-case names are split in odd places. This rewrites the text the voice
receives — never the text the conversation history or transcript keeps.

Driven by settings (see app/core/config.py):
- TTS_PRONUNCIATIONS: ";"-separated "from=to" pairs, case-sensitive and
  whole-word, applied in order (so "KINIKH=Kinikh;Kinikh=किनिख" chains).
- TTS_PRONUNCIATIONS_HI: applied first when the TTS language is hi / hi-en.
- TTS_KEEP_ACRONYMS: ALL-CAPS words left as they are. Every other ALL-CAPS
  word of 4+ letters still present after the maps is Title-cased.
"""

from __future__ import annotations

from functools import lru_cache
import re

from app.core.config import get_settings

_HINDI_LANGUAGES = frozenset({"hi", "hi-en"})
_ALL_CAPS_WORD = re.compile(r"(?<!\w)[A-Z]{4,}(?!\w)")
# "Ek pal" sounds too heavy/formal on a call; the prompt bans it, and this
# catches any reply that slips through.
_EK_PAL = re.compile(r"(?<![\w\u0900-\u097F])(?:एक\s+पल|ek\s+pal)(?![\w\u0900-\u097F])", re.I)
_EK_PAL_REPLACEMENT = "One second"


@lru_cache(maxsize=16)
def _parse_pronunciations(spec: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    rules: list[tuple[re.Pattern[str], str]] = []
    for pair in spec.split(";"):
        source, sep, target = pair.partition("=")
        source, target = source.strip(), target.strip()
        if not sep or not source:
            continue
        rules.append((re.compile(rf"(?<!\w){re.escape(source)}(?!\w)"), target))
    return tuple(rules)


@lru_cache(maxsize=8)
def _parse_acronyms(spec: str) -> frozenset[str]:
    return frozenset(a.strip() for a in spec.split(",") if a.strip())


def normalize_for_tts(text: str, language: str = "en") -> str:
    """The text the TTS voice should actually read for `text`."""
    if not text:
        return text
    text = _EK_PAL.sub(_EK_PAL_REPLACEMENT, text)
    settings = get_settings()
    spec = settings.tts_pronunciations
    if language in _HINDI_LANGUAGES and settings.tts_pronunciations_hi:
        text = _apply(text, settings.tts_pronunciations_hi)
    text = _apply(text, spec)
    keep = _parse_acronyms(settings.tts_keep_acronyms)
    return _ALL_CAPS_WORD.sub(
        lambda m: m.group(0) if m.group(0) in keep else m.group(0).title(), text
    )


def _apply(text: str, spec: str) -> str:
    for pattern, target in _parse_pronunciations(spec):
        text = pattern.sub(lambda match: target, text)
    return text
