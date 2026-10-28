"""Reject speech-to-text output that is a known hallucination, not speech.

STT models trained on captioned video (Whisper especially) turn line noise,
breathing and silence into stock phrases: "Thank you for watching",
"Please subscribe", "Subtitles by ...", a lone "you" or ".". Answering those
is exactly what callers hear as the assistant "responding to noise". Only
phrases that essentially never occur as a genuine caller turn on a phone
call are filtered — a real "thank you" or "bye" from the caller is kept.
"""

from __future__ import annotations

import re

# Whole-transcript matches (after normalisation) that are never a real caller turn.
_EXACT = frozenset(
    {
        "",
        "you",
        "uh",
        "um",
        "hmm",
        "mm",
        "ah",
        "oh",
        "so",
        "the",
        "i",
        "a",
        "music",
        "applause",
        "laughter",
        "silence",
        "noise",
        "inaudible",
        "blank_audio",
        "no speech",
        "thanks for watching",
        "thank you for watching",
        "thank you so much for watching",
        "thanks for watching and see you next time",
        "please subscribe",
        "subscribe",
        "like and subscribe",
        "see you in the next video",
        "सब्सक्राइब",
        "सब्सक्राइब करें",
        "धन्यवाद देखने के लिए",
        "देखने के लिए धन्यवाद",
    }
)

# Substrings that only ever come from captioned-video training data.
_CONTAINS = (
    "subtitles by",
    "subtitle by",
    "captions by",
    "amara.org",
    "transcribed by",
    "translated by",
    "thanks for watching",
    "thank you for watching",
    "like and subscribe",
    "subscribe to",
    "चैनल को सब्सक्राइब",
    "सब्सक्राइब कर",
)

_NON_WORD = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = text.lower().replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ")
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def is_hallucination(transcript: str) -> bool:
    """True if `transcript` is a stock STT hallucination rather than speech."""
    norm = _normalise(transcript)
    if norm in _EXACT:
        return True
    if any(marker in norm for marker in _CONTAINS):
        return True
    # A single character repeated / pure punctuation ("...", "- - -").
    return len(norm.replace(" ", "")) <= 1


# A caller turn ending in one of these is almost always mid-sentence: they
# paused to think, not to hand the turn over ("mujhe interior chahiye aur...").
_TRAILING_CONNECTORS = frozenset(
    {
        "and", "but", "so", "because", "like", "or", "um", "umm", "uh",
        "aur", "lekin", "toh", "matlab", "ki", "jo", "ya", "phir", "means",
        "और", "लेकिन", "तो", "मतलब", "कि", "जो", "या", "फिर",
    }
)
_MIN_COMPLETE_WORDS = 3
_COMPLETE_SHORT_REPLIES = frozenset({
    "yes", "yeah", "yes please", "no", "no thanks", "no thank you", "okay", "ok",
    "sure", "haan", "han", "haan ji", "haanji", "ji", "jee", "nahi", "nahin",
    "theek hai", "thik hai", "हाँ", "हां", "हाँ जी", "हां जी", "जी", "नहीं", "नही",
    "ठीक है", "अच्छा",
})


def classify_turn_end(transcript: str) -> tuple[str, str]:
    """Decide whether a finished caller utterance is a complete turn.

    Returns (decision, reason): decision is "reply" or "wait_unfinished".
    """
    text = transcript.strip()
    if text.endswith("?"):
        return "reply", "question"
    if text.endswith((",", "...", "…")):
        return "wait_unfinished", "trailing_punctuation"
    words = text.split()
    last = _normalise(words[-1]).strip("।॥ ") if words else ""
    if last in _TRAILING_CONNECTORS:
        return "wait_unfinished", f"trailing_connector:{last}"
    if _normalise(text) in _COMPLETE_SHORT_REPLIES:
        return "reply", "complete_short_answer"
    if len(words) < _MIN_COMPLETE_WORDS:
        return "wait_unfinished", "too_short"
    return "reply", "complete"
