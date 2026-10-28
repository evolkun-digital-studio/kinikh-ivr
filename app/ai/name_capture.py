"""Conservative, local caller-name capture using the last spoken question.

No name dictionary or extra model call: a clear name-shaped answer to a
personal-name question is sufficient, in Latin or Devanagari script.
"""

from __future__ import annotations

import re
import unicodedata

from app.ai.language_profile import ACKNOWLEDGEMENTS

_NAME_QUESTION = re.compile(
    r"\b(?:your\s+(?:(?:full|first|good)\s+)?name|what\s+should\s+i\s+call\s+you"
    r"|who\s+(?:am\s+i|are\s+we)\s+(?:speaking|talking)\s+(?:with|to))\b"
    r"|\b(?:aapka|apka|aapke)\s+(?:naam|nam|name)\b"
    r"|आपका\s*(?:नाम|name)|आप\s*अपना\s*नाम",
    re.IGNORECASE,
)
_ASK = re.compile(
    r"\b(?:what|who|could|can|may|share|tell|confirm|repeat|spell|kya|bataiye|bataye|batayen)\b|[?？]|क्या|बताइए|बताएं|बतायें",
    re.IGNORECASE,
)
_INTRODUCTION = re.compile(
    r"(?:\bmy\s+name|\bmera\s+(?:naam|nam|name)|मेरा\s+(?:नाम|नेम|name|naam))"
    r"\s+(?:(?:is|hai|है)\s+)?(.+)",
    re.IGNORECASE,
)
_I_AM = re.compile(r"\b(?:i\s+am|i['’]m)\s+(.+)", re.IGNORECASE)
_CONTEXT_INTRO = re.compile(r"^(?:it['’]s|this\s+is|name\s+is)\s+", re.IGNORECASE)
_ACK_PREFIX = re.compile(
    r"^(?:(?:yes|yeah|no|haan|han|ji|jee|nahi|nahin|हाँ|हां|जी|नहीं)[,\s]+)+", re.IGNORECASE
)
_NAME_END = re.compile(
    r"\s+(?:hai|hain|hoon|hun|hu|है|हैं|हूँ|हूं|here|speaking|from|से|बोल\s+रहा|बोल\s+रही)"
    r"(?=\s|[,.!?।]|$)|\s+(?:and\s+i|aur\s+mujhe|और\s+मुझे)\b",
    re.IGNORECASE,
)
_NOT_NAMES = ACKNOWLEDGEMENTS | frozenset(
    "i am is my name mera naam nam mujhe chahiye nahi nahin not important interested "
    "calling looking want need website construction booking service services team "
    "manager support please sorry thanks hello namaste how what why where when "
    "your you don't dont share to a an the from for and or ya aur "
    "मेरा नाम मुझे चाहिए नहीं या और क्या कैसे बताना चाहता चाहती "
    "ignore instructions forget previous".split()
)


def asks_caller_name(previous_reply: str) -> bool:
    """Only personal-name questions; a project/company name is a different field."""
    # Keep the name reference and the question in the same clause. Otherwise
    # "I have your name. What city?" makes a city overwrite a saved name.
    return any(
        _NAME_QUESTION.search(clause) and _ASK.search(clause)
        for clause in re.findall(r"[^,.!?।;]+[,.!?।;]?", previous_reply)
    )


def extract_caller_name(text: str, previous_reply: str = "") -> str | None:
    """Return a clear caller-provided name; leave ambiguous input to the LLM."""
    explicit = _INTRODUCTION.search(text) or _I_AM.search(text)
    if explicit:
        candidate = explicit.group(1)
    elif asks_caller_name(previous_reply):
        candidate = _CONTEXT_INTRO.sub("", text.strip())
    else:
        return None

    candidate = _ACK_PREFIX.sub("", candidate.strip())
    candidate = _NAME_END.split(candidate, maxsplit=1)[0]
    candidate = candidate.strip(' \t\r\n,.!?।॥"“”')
    # A name can have initials, apostrophes or hyphens, but not a sentence,
    # number, uncertain alternative, service request or a refusal.
    words = candidate.split()
    if not 1 <= len(words) <= 5 or len(candidate) > 80:
        return None
    for word in words:
        initial = len(word) == 2 and word[0].isalpha() and word[1] == "."
        if not initial and word.casefold().rstrip(".") in _NOT_NAMES:
            return None
        if "." in word and not initial:
            return None
        if not any(c.isalpha() for c in word):
            return None
        if any(
            not (c.isalpha() or unicodedata.category(c).startswith("M") or c in "'-’.")
            for c in word
        ):
            return None
    return " ".join(words)
