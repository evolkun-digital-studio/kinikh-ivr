"""Per-turn language analysis and the sticky-but-adaptive call language profile.

Everything here is local, deterministic and allocation-light: no network, no
LLM. It runs on every caller turn between speech-to-text and the main
conversation LLM, so it must stay in the sub-millisecond range.

Language codes are the project's existing ones (stored on calls/leads, used
by TTS voice maps): "en", "hi", "hi-en" (Hinglish), plus "unknown" for "no
evidence yet".

Two separate decisions are made per turn:

- RESPONSE language for *this* reply — mirrors any clear (meaningful) caller
  turn immediately, so a caller who switches is answered in the new language
  straight away.
- Session PREFERENCE (CallContext.language) — the long-term call language.
  It only moves after `switch_turns` consecutive meaningful turns in a new
  language, one very strong full turn, or an explicit request. Short
  acknowledgements ("haan", "okay", "ji") never move either decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.context_manager import CallContext

EN = "en"
HI = "hi"
HINGLISH = "hi-en"
UNKNOWN = "unknown"
LANGUAGES = (EN, HI, HINGLISH)
LANGUAGE_NAMES = {EN: "English", HI: "Hindi", HINGLISH: "Hinglish"}

# Confidence is a coarse band, not a probability: the detector is a lexicon
# heuristic and cannot produce calibrated numbers.
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

# A single turn this long, with no evidence of the other language, moves the
# session preference on its own (otherwise `switch_turns` turns are needed).
STRONG_TURN_MIN_WORDS = 12

# ── Lexicons (the single place to extend) ───────────────────────────────────

# Low-evidence words: acknowledgements, fillers and closings. On their own
# they say nothing about the caller's language, and inside a sentence they
# are ignored rather than counted for either language. Extend at runtime via
# the LANGUAGE_EXTRA_ACKNOWLEDGEMENTS setting (comma separated).
ACKNOWLEDGEMENTS: frozenset[str] = frozenset(
    """
    yes yeah yep yup ya no nope nah okay ok okk okey k right sure fine alright
    hmm hm hmmm mm mhm uh um umm haan han haa ha ji jee achha acha accha achchha
    theek thik theekh nahi nahin bas thanks thank shukriya dhanyavad dhanyavaad bye
    हाँ हां हा जी ठीक अच्छा ओके हम्म हम्मम नहीं नही बस शुक्रिया धन्यवाद
    """.split()
)

# Greetings and forms of address: also no language signal ("Hi, mujhe ..."
# must not count as English just because it starts with "Hi").
NEUTRAL_WORDS: frozenset[str] = frozenset(
    """
    hello helo hallo hi hii hey namaste namaskar namaskaar namastey sir madam mam
    maam maa'am bhaiya bhai didi english hindi hinglish kinikh
    नमस्ते नमस्कार हेलो हैलो सर मैडम
    """.split()
)

# Romanised Hindi words that are NOT also everyday English words. Ambiguous
# spellings ("to", "me", "the", "main", "do", "so", "us", "par", "hi",
# "banana", "log") are deliberately excluded so English sentences are never
# mistaken for Hinglish.
ROMAN_HINDI: frozenset[str] = frozenset(
    """
    hai hain ho hoon hun hu tha thi kya kyu kyun kyon kyunki kaise kaisa kaisi
    kab kahan kaha kitna kitne kitni kaun kaunsa kaunsi kaunse konsa kon kis
    kisi kisko kiske jo jab tab mujhe mujhko mera meri mere hum hamara hamari
    hamare humein hume humko aap aapka aapki aapke aapko apna apni apne tum
    tumhara tumhe ka ki ke ko se mein mai bhi abhi phir chahiye chahte chahta
    chahti karna karni karne karke karo kar karein kariye kijiye karte karta
    karti karenge karega karoge kiya kiye batao bataiye bataye bataiyega bata
    batana bolo boliye boliyega dekho dekhiye suniye samjha samjhe samajh wala
    wali wale vala vali vale ghar makaan kaam paisa paise rupaye rupay lakh
    hazaar hazar din baad pehle aur lekin magar toh matlab liye liya baare bare
    jaanna janna jankari jaankari sakte sakta sakti sake hoga hogi honge hona
    hone hota hoti lagta lagti lagte lagega lagegi lagenge banwana banwane
    banvana banane banega raha rahe rahi rha gaya gayi diya dena lena dijiye
    lijiye jao aao aaye aana jana jaana milega milegi milta chalega yeh woh vo
    wo iska uska inka unka kuch koi sab sabhi kahin yahan wahan andar upar
    niche saath sath zaroor zarur bilkul thoda bahut zyada jyada seva sewa
    sevaon sevayein jagah zameen naya purana kharcha kharch samay waqt jaldi
    aaj parso mahina mahine saal agar isliye wahi yahi puchna pooch poochna
    pooch rahe baat bolna sunna milna chahiye hamein unko inko usko isko
    """.split()
)

# English grammar/function words and common verbs: the real evidence that a
# sentence is English rather than Hindi with English nouns in it.
ENGLISH_FUNCTION: frozenset[str] = frozenset(
    """
    i i'm im i'd i'll i've you you're your yours we we're our us my me he she
    it it's its they them their this that these those what what's which who
    whom whose when where why how is are was were be been being am do does
    did doing done have has had will would shall should can could may might
    must the a an and or but if then so because about of to in on at for with
    from by into over under after before up out than as not don't dont
    doesn't didn't can't cant won't isn't aren't there here want wants wanted
    need needs know tell explain provide provides offer get give take takes
    make build call speak talk send help like looking interested please also
    just any some more much many usually approximately really actually
    """.split()
)

# Business/technical English that Hindi and Hinglish speakers use as-is.
# Counts as English evidence (so "Interior design bhi karte ho?" is
# Hinglish, not Hindi), but never makes a sentence meaningful English on its
# own — "residential project in Pune" is not a language switch.
BUSINESS_TERMS: frozenset[str] = frozenset(
    """
    architecture architectural architect architects interior interiors design
    designs designing designer construction construct project projects
    residential commercial consultation consult consultant site sites budget
    planning plan plans renovation renovate service services company office
    team price prices pricing cost costs quote quotation estimate details
    detail information info contact email number phone mobile website app home
    house building property plot land flat apartment villa hotel booking
    marketing brand branding process time start date meeting appointment
    requirement problem complaint support payment bill account visit location
    city area square feet sqft month months week weeks day days year years
    next step steps work available option options package packages
    """.split()
)

# ── Explicit language requests (highest priority) ───────────────────────────

_EXPLICIT_HINGLISH = re.compile(r"\bhinglish\b")
_EXPLICIT_NOT_ENGLISH = re.compile(
    r"\benglish\s+(?:nahi|nahin|nai|mat)\b|\b(?:not|no|don'?t)\s+(?:\w+\s+){0,2}english\b"
)
_EXPLICIT_ENGLISH = re.compile(
    r"\b(?:speak|talk|reply|answer|explain|continue|say|tell|go)\b[\w\s']{0,40}?\bin english\b"
    r"|\b(?:speak|talk)\s+english\b"
    r"|\benglish\s+(?:mein|me|main|mai|mei|please|bolo|boliye|mein\s+baat|me\s+baat)\b"
    r"|\bin english\s+please\b"
    r"|अंग्रेज़ी\s*में|अंग्रेजी\s*में|इंग्लिश\s*में"
)
_EXPLICIT_HINDI = re.compile(
    r"\b(?:speak|talk|reply|answer|explain|continue|say|tell|go)\b[\w\s']{0,40}?\bin hindi\b"
    r"|\b(?:speak|talk)\s+hindi\b"
    r"|\bhindi\s+(?:mein|me|main|mai|mei|please|bolo|boliye|aati|aata)\b"
    r"|\bin hindi\s+please\b"
    r"|हिंदी\s*में|हिन्दी\s*में"
)


def detect_explicit_request(text: str) -> str | None:
    """Language the caller explicitly asked to be spoken to in, else None."""
    lowered = text.lower()
    if _EXPLICIT_HINGLISH.search(lowered):
        return HINGLISH
    if _EXPLICIT_NOT_ENGLISH.search(lowered):
        return HI
    wants_en = bool(_EXPLICIT_ENGLISH.search(lowered))
    wants_hi = bool(_EXPLICIT_HINDI.search(lowered))
    if wants_en != wants_hi:  # "Hindi ya English?" is a question, not a request
        return EN if wants_en else HI
    return None


# ── Per-turn analysis ───────────────────────────────────────────────────────

_SPLIT = re.compile(r"[\s,.!?;:\"“”‘’()\[\]{}।॥\-–—/…]+")


def _is_hindi_script(token: str) -> bool:
    # Devanagari, plus Arabic script: speech-to-text sometimes writes spoken
    # Hindi in Urdu script, which is the same spoken language.
    return any("ऀ" <= c <= "ॿ" or "؀" <= c <= "ۿ" for c in token)


@lru_cache(maxsize=8)
def _low_evidence_words(extra: str) -> frozenset[str]:
    extra_words = {w.strip().lower() for w in extra.split(",") if w.strip()}
    return ACKNOWLEDGEMENTS | NEUTRAL_WORDS | frozenset(extra_words)


@lru_cache(maxsize=8)
def _ack_phrases(extra: str) -> frozenset[str]:
    return frozenset(w.strip().lower() for w in extra.split(",") if " " in w.strip())


@dataclass(frozen=True, slots=True)
class LanguageAnalysis:
    language: str  # en | hi | hi-en | unknown
    confidence: str  # high | medium | low
    meaningful: bool  # enough evidence to act on
    mixed_language: bool  # both Hindi and English evidence present
    explicit_request: str | None = None
    content_words: int = 0

    def as_log_fields(self) -> dict[str, object]:
        return {
            "detected": self.language,
            "confidence": self.confidence,
            "meaningful": self.meaningful,
            "mixed_language": self.mixed_language,
            "explicit_request": self.explicit_request,
        }


def analyze_language(
    text: str, *, min_words: int = 3, extra_acknowledgements: str = ""
) -> LanguageAnalysis:
    """Classify one caller utterance. Pure and local (a few microseconds)."""
    explicit = detect_explicit_request(text) if text else None
    raw = [t.strip("'").lower() for t in _SPLIT.split(text or "")]
    tokens = [t for t in raw if t and not t.isdigit()]
    low_evidence = _low_evidence_words(extra_acknowledgements)
    if not tokens or " ".join(tokens) in _ack_phrases(extra_acknowledgements):
        return LanguageAnalysis(UNKNOWN, LOW, False, False, explicit, 0)

    hindi_script = roman_hindi = en_function = business = content = 0
    for token in tokens:
        if token in low_evidence:
            continue
        content += 1
        if _is_hindi_script(token):
            hindi_script += 1
        elif token in ROMAN_HINDI:
            roman_hindi += 1
        elif token in ENGLISH_FUNCTION:
            en_function += 1
        elif token in BUSINESS_TERMS:
            business += 1
        # anything else (names, places, unknown words) is neutral

    hindi = hindi_script + roman_hindi
    english = en_function + business
    mixed = hindi > 0 and english > 0

    if hindi == 0 and english == 0:
        language = UNKNOWN
    elif hindi == 0:
        language = EN
    elif english == 0:
        language = HI
    elif hindi_script:
        # Devanagari with a few English words is how Hindi is spoken; only a
        # real share of English makes it Hinglish.
        language = HINGLISH if english / (english + hindi) >= 0.25 else HI
    elif hindi == 1 and en_function >= 3:
        # One stray Hindi word in an otherwise English sentence.
        language = EN
    else:
        language = HINGLISH

    meaningful = (
        language != UNKNOWN
        and content >= min_words
        and (hindi >= 2 or en_function >= 2 or (hindi >= 1 and english >= 1))
    )
    if not meaningful:
        confidence = LOW
    elif hindi + english >= 4 and (not mixed or language == HINGLISH or hindi_script):
        confidence = HIGH
    else:
        confidence = MEDIUM
    return LanguageAnalysis(language, confidence, meaningful, mixed, explicit, content)


# ── Session profile (hysteresis) ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    response_language: str
    previous_preference: str
    preference: str
    primary: str
    switch_reason: str
    preference_changed: bool


def _same_family(a: str, b: str) -> bool:
    return a == b or (a in (HI, HINGLISH) and b in (HI, HINGLISH))


def update_language_profile(
    ctx: CallContext, analysis: LanguageAnalysis, *, switch_turns: int = 2
) -> LanguageDecision:
    """Apply one caller turn to the call's language profile (mutates `ctx`)
    and return the language the reply to this turn should use."""
    established = ctx.language_locked
    previous = ctx.language if established else UNKNOWN
    changed = False

    def _set_preference(language: str) -> None:
        nonlocal changed
        changed = changed or ctx.language != language or not ctx.language_locked
        ctx.language = language
        if not ctx.language_locked:
            ctx.primary_language = language
        ctx.language_locked = True
        ctx.language_detected = True
        ctx.pending_language = None
        ctx.consecutive_other_language_turns = 0

    if analysis.meaningful:
        ctx.current_turn_language = analysis.language
        ctx.language_confidence = analysis.confidence
    else:
        ctx.current_turn_language = UNKNOWN
        ctx.language_confidence = LOW

    if analysis.explicit_request:
        _set_preference(analysis.explicit_request)
        ctx.language_explicit = True
        reason = "explicit_request"
        response = analysis.explicit_request
    elif not analysis.meaningful:
        # Acknowledgements, names, one-word answers: keep speaking whatever
        # the conversation is currently in.
        reason = "low_evidence_retain" if established else "awaiting_evidence"
        response = ctx.reply_language if established else UNKNOWN
    elif not established:
        _set_preference(analysis.language)
        reason = "first_meaningful_turn"
        response = analysis.language
    elif ctx.language_explicit:
        # The caller chose a language; follow it until they choose again,
        # or speak another language turn after turn.
        reason = "explicit_preference"
        if _same_family(analysis.language, ctx.language):
            ctx.pending_language = None
            ctx.consecutive_other_language_turns = 0
        else:
            _count_toward(ctx, analysis.language)
            if ctx.consecutive_other_language_turns > switch_turns:
                _set_preference(analysis.language)
                ctx.language_explicit = False
                reason = "sustained_switch_over_explicit"
        response = ctx.language
    else:
        response = analysis.language
        reason = "matches_preference" if analysis.language == ctx.language else "clear_current_turn"
        if analysis.language == ctx.language:
            ctx.pending_language = None
            ctx.consecutive_other_language_turns = 0
        else:
            _count_toward(ctx, analysis.language)
            strong = (
                analysis.confidence == HIGH
                and not analysis.mixed_language
                and analysis.content_words >= STRONG_TURN_MIN_WORDS
            )
            if strong or ctx.consecutive_other_language_turns >= switch_turns:
                _set_preference(analysis.language)
                reason = "strong_turn_switch" if strong else "consecutive_turns_switch"

    ctx.response_language = response
    return LanguageDecision(
        response_language=response,
        previous_preference=previous,
        preference=ctx.language if ctx.language_locked else UNKNOWN,
        primary=ctx.primary_language,
        switch_reason=reason,
        preference_changed=changed,
    )


def _count_toward(ctx: CallContext, language: str) -> None:
    if ctx.pending_language == language:
        ctx.consecutive_other_language_turns += 1
    else:
        ctx.pending_language = language
        ctx.consecutive_other_language_turns = 1
