"""Optional caller speech-style (accent) detection — TTS personalisation only.

A speech style is a broad ACOUSTIC style ("gujarati_influenced"), never a
claim about where the caller lives or comes from, and it is never derived
from the phone number, name, location or transcript words. It is kept apart
from the call LANGUAGE (app/ai/language_profile.py): language decides what
language is spoken; speech style may only pick which curated TTS voice
speaks it. It never reaches the LLM prompt, RAG, routing or lead data.

Design:
- AccentDetector is the vendor-neutral interface. SpeechHandler never knows
  which model runs behind it; a real acoustic model is plugged in with
  register_accent_detector() and ACCENT_DETECTOR_PROVIDER.
- No acoustic model ships with this project yet, so the default provider
  ("none") is NeutralAccentDetector: it always answers neutral_indian.
  Nothing here guesses a regional style heuristically.
- SpeechStyleTracker copies voiced caller-only audio (never assistant
  playback or barge-in-over-playback audio), and once enough has
  accumulated runs the detector in a tracked background task — never on
  the STT → RAG → LLM → TTS critical path, never per 20 ms frame.
- apply_accent_result() is the hysteresis: only consistent, confident,
  well-evidenced results change the profile; a set profile never flaps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger

from app.core.call_logging import describe_error, log_call_event

if TYPE_CHECKING:
    from app.ai.context_manager import CallContext
    from app.core.config import Settings


class SpeechStyle(StrEnum):
    NEUTRAL_INDIAN = "neutral_indian"
    GUJARATI_INFLUENCED = "gujarati_influenced"
    PUNJABI_INFLUENCED = "punjabi_influenced"
    BENGALI_INFLUENCED = "bengali_influenced"
    MARATHI_INFLUENCED = "marathi_influenced"
    SOUTH_INDIAN_INFLUENCED = "south_indian_influenced"
    UNKNOWN = "unknown"


NEUTRAL = SpeechStyle.NEUTRAL_INDIAN.value
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# One utterance must hold at least this much voiced speech to be used at
# all — "hello", "haan", "ji", "okay" never feed the detector.
MIN_UTTERANCE_VOICED_MS = 1000
_FRAME_MS = 20
# Frames quieter than this (PCM16 RMS) are silence/line noise and dropped.
_VOICED_RMS = 300.0
_PCM_DTYPE = np.dtype("<i2")


@dataclass(frozen=True, slots=True)
class AccentResult:
    style: str
    confidence: str  # low | medium | high — a band, not a probability
    usable_audio_ms: int


class AccentDetector(ABC):
    """Classifies a broad speech style from caller-only PCM16 mono audio.

    Implementations must be safe to share across calls (load any model
    once, in __init__) and must not block the event loop — CPU-bound
    inference belongs in a thread (see ThreadedAccentDetector).
    """

    name: str = "abstract"
    # Sample rate the detector needs; None accepts the telephony rate as-is.
    sample_rate: int | None = None

    @abstractmethod
    async def detect(self, pcm: bytes, sample_rate: int) -> AccentResult: ...


class NeutralAccentDetector(AccentDetector):
    """Used while no real acoustic model is configured: always neutral."""

    name = "none"

    async def detect(self, pcm: bytes, sample_rate: int) -> AccentResult:
        return AccentResult(NEUTRAL, "low", _duration_ms(pcm, sample_rate))


class ThreadedAccentDetector(AccentDetector):
    """Base for a local CPU model: subclasses load the model once in
    __init__ and implement classify_sync(); inference runs in a worker
    thread so the call's event loop is never blocked."""

    @abstractmethod
    def classify_sync(self, pcm: bytes, sample_rate: int) -> AccentResult: ...

    async def detect(self, pcm: bytes, sample_rate: int) -> AccentResult:
        return await asyncio.to_thread(self.classify_sync, pcm, sample_rate)


class ScriptedAccentDetector(AccentDetector):
    """Test/dev double returning pre-set results in order (the last one
    repeats). Not selectable through configuration — it is not a model."""

    name = "scripted"

    def __init__(
        self,
        results: list[AccentResult | BaseException],
        *,
        delay: float = 0.0,
        sample_rate: int | None = None,
    ) -> None:
        self._results = list(results)
        self._delay = delay
        self.sample_rate = sample_rate
        self.calls: list[tuple[int, int]] = []  # (bytes, sample_rate)

    async def detect(self, pcm: bytes, sample_rate: int) -> AccentResult:
        self.calls.append((len(pcm), sample_rate))
        if self._delay:
            await asyncio.sleep(self._delay)
        result = self._results[min(len(self.calls), len(self._results)) - 1]
        if isinstance(result, BaseException):
            raise result
        return result


# ── Provider registry (where a real model is plugged in) ────────────────────

_FACTORIES: dict[str, Callable[[], AccentDetector]] = {"none": NeutralAccentDetector}
_INSTANCES: dict[str, AccentDetector] = {}


def register_accent_detector(name: str, factory: Callable[[], AccentDetector]) -> None:
    """Make an acoustic detector selectable via ACCENT_DETECTOR_PROVIDER."""
    _FACTORIES[name.strip().lower()] = factory
    _INSTANCES.pop(name.strip().lower(), None)


def get_accent_detector(provider: str) -> AccentDetector:
    """Process-wide detector instance (a model is loaded once, not per call).
    Unknown providers fall back to the neutral detector."""
    key = (provider or "none").strip().lower()
    if key not in _FACTORIES:
        logger.warning(f"[ACCENT] unknown detector provider={key!r}; using neutral")
        key = "none"
    if key not in _INSTANCES:
        _INSTANCES[key] = _FACTORIES[key]()
    return _INSTANCES[key]


def accent_mode(settings: Settings) -> str:
    """Effective mode: "disabled" unless the feature flag is on."""
    if not settings.accent_detection_enabled:
        return "disabled"
    return settings.accent_detection_mode


# ── Audio helpers ───────────────────────────────────────────────────────────


def _duration_ms(pcm: bytes, sample_rate: int) -> int:
    return int(len(pcm) // 2 * 1000 / sample_rate) if sample_rate else 0


def voiced_pcm(pcm: bytes, sample_rate: int) -> bytes:
    """`pcm` with silent/near-silent 20 ms frames removed (a copy — the
    caller's buffer is never modified)."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=_PCM_DTYPE)
    frame = sample_rate * _FRAME_MS // 1000
    count = len(samples) // frame
    if count == 0:
        return b""
    frames = samples[: count * frame].reshape(count, frame).astype(np.float32)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return frames[rms >= _VOICED_RMS].astype(_PCM_DTYPE).tobytes()


def resample_pcm16(pcm: bytes, input_rate: int, output_rate: int) -> bytes:
    """Linear-interpolation resample, only for detectors that need another
    rate (e.g. 8 kHz telephony -> 16 kHz model input). Band-limited phone
    audio has nothing above 4 kHz to alias, so this is adequate here."""
    if input_rate == output_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=_PCM_DTYPE).astype(np.float32)
    out_len = int(len(samples) * output_rate / input_rate)
    positions = np.linspace(0, len(samples) - 1, out_len)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(np.round(resampled), -32768, 32767).astype(_PCM_DTYPE).tobytes()


# ── Profile hysteresis ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StyleUpdate:
    updated: bool
    reason: str


def parse_styles(value: str) -> frozenset[str]:
    styles = {s.strip().lower() for s in (value or "").split(",") if s.strip()}
    return frozenset(styles | {NEUTRAL})


def apply_accent_result(
    ctx: CallContext,
    result: AccentResult,
    *,
    min_confidence: str = "high",
    min_evidence: int = 2,
    min_audio_ms: int = 4000,
    styles: frozenset[str] = frozenset(s.value for s in SpeechStyle),
) -> StyleUpdate:
    """Apply one detection to the call's speech-style profile (mutates ctx).

    Only a result that is a known style, from enough audio, at or above
    `min_confidence`, counts as evidence; `min_evidence` consecutive
    agreeing results set the style, after which it never changes for the
    rest of the call. Everything else leaves the profile untouched.
    """
    style = result.style
    if ctx.speech_style_locked:
        if style == ctx.speech_style:
            return StyleUpdate(False, "already_applied")
        return StyleUpdate(False, "conflicting_result")
    if result.usable_audio_ms < min_audio_ms:
        return StyleUpdate(False, "not_enough_audio")
    if style not in styles or style == SpeechStyle.UNKNOWN:
        return StyleUpdate(False, "unknown_style")
    if _CONFIDENCE_RANK.get(result.confidence, -1) < _CONFIDENCE_RANK[min_confidence]:
        return StyleUpdate(False, "low_confidence")

    conflicting = ctx.speech_style_candidate not in (None, style)
    if ctx.speech_style_candidate == style:
        ctx.speech_style_evidence_turns += 1
    else:
        ctx.speech_style_candidate = style
        ctx.speech_style_evidence_turns = 1
    if ctx.speech_style_evidence_turns < min_evidence:
        return StyleUpdate(False, "conflicting_result" if conflicting else "awaiting_evidence")
    ctx.speech_style = style
    ctx.speech_style_confidence = result.confidence
    ctx.speech_style_locked = True
    return StyleUpdate(True, "consistent_evidence")


# ── Per-call background tracker ─────────────────────────────────────────────


class SpeechStyleTracker:
    """Accumulates caller speech for one call and runs the detector in the
    background. add_caller_audio() is synchronous and cheap (it only copies
    voiced frames); detection happens in a task spawned via `spawn`, which
    the owner tracks and cancels when the call ends."""

    def __init__(
        self,
        detector: AccentDetector,
        settings: Settings,
        *,
        get_context: Callable[[], CallContext | None],
        spawn: Callable[[Coroutine[Any, Any, None]], Any],
    ) -> None:
        self._detector = detector
        self._mode = accent_mode(settings)
        self._min_audio_ms = settings.accent_min_audio_ms
        self._timeout = settings.accent_timeout_ms / 1000
        self._min_confidence = settings.accent_min_confidence
        self._min_evidence = settings.accent_min_evidence
        self._max_analyses = settings.accent_max_analyses
        self._styles = parse_styles(settings.accent_styles)
        self._get_context = get_context
        self._spawn = spawn
        self._window = bytearray()
        self._window_ms = 0
        self._analyses = 0
        self._running = False
        self._closed = False

    @property
    def analyses(self) -> int:
        return self._analyses

    def close(self) -> None:
        """Call ended: drop buffered audio; late results are discarded."""
        self._closed = True
        self._window = bytearray()

    def add_caller_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Offer one finished caller utterance (caller audio only)."""
        ctx = self._get_context()
        if (
            self._closed
            or ctx is None
            or ctx.speech_style_locked
            or self._analyses >= self._max_analyses
        ):
            return
        voiced = voiced_pcm(pcm, sample_rate)
        voiced_ms = _duration_ms(voiced, sample_rate)
        if voiced_ms < MIN_UTTERANCE_VOICED_MS:
            return
        self._window.extend(voiced)
        self._window_ms += voiced_ms
        if self._window_ms < self._min_audio_ms or self._running:
            return
        window, self._window, self._window_ms = bytes(self._window), bytearray(), 0
        self._analyses += 1
        self._running = True
        self._spawn(self._analyse(window, sample_rate, self._analyses, ctx.call_sid))

    async def _analyse(self, pcm: bytes, sample_rate: int, analysis: int, call_sid: str) -> None:
        usable_ms = _duration_ms(pcm, sample_rate)
        log_call_event(
            "voice.accent.analysis_started",
            call_sid=call_sid,
            analysis=analysis,
            usable_audio_ms=usable_ms,
            detector=self._detector.name,
            mode=self._mode,
        )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout):
                rate = self._detector.sample_rate or sample_rate
                audio = await asyncio.to_thread(resample_pcm16, pcm, sample_rate, rate) if rate != sample_rate else pcm
                result = await self._detector.detect(audio, rate)
        except TimeoutError:
            self._fallback(call_sid, "timeout", started)
            return
        except Exception as exc:
            self._fallback(call_sid, "detector_error", started, error=describe_error(exc))
            return
        finally:
            self._running = False
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        ctx = self._get_context()
        if self._closed or ctx is None or ctx.call_sid != call_sid:
            return  # the call ended while analysing: never touch its context
        update = apply_accent_result(
            ctx,
            result,
            min_confidence=self._min_confidence,
            min_evidence=self._min_evidence,
            min_audio_ms=self._min_audio_ms,
            styles=self._styles,
        )
        log_call_event(
            "voice.accent.detected",
            call_sid=call_sid,
            analysis=analysis,
            style=result.style,
            confidence=result.confidence,
            usable_audio_ms=result.usable_audio_ms,
            accent_detection_ms=elapsed_ms,
            applied_to_tts=(
                self._mode == "adaptive_tts"
                and ctx.speech_style_locked
                and ctx.speech_style == result.style
                and result.style != NEUTRAL
            ),
            mode=self._mode,
        )
        if update.updated:
            log_call_event(
                "voice.accent.profile_updated",
                call_sid=call_sid,
                style=ctx.speech_style,
                confidence=ctx.speech_style_confidence,
                evidence=ctx.speech_style_evidence_turns,
                mode=self._mode,
            )
        else:
            log_call_event(
                "voice.accent.ignored", call_sid=call_sid, analysis=analysis, reason=update.reason
            )

    def _fallback(self, call_sid: str, reason: str, started: float, **extra: Any) -> None:
        log_call_event(
            "voice.accent.fallback",
            call_sid=call_sid,
            reason=reason,
            style=NEUTRAL,
            accent_detection_ms=round((time.perf_counter() - started) * 1000, 1),
            **extra,
        )


def build_speech_style_tracker(
    settings: Settings,
    *,
    get_context: Callable[[], CallContext | None],
    spawn: Callable[[Coroutine[Any, Any, None]], Any],
    detector: AccentDetector | None = None,
) -> SpeechStyleTracker | None:
    """Tracker for one call, or None when the feature is disabled (then no
    audio is copied and no detector is ever invoked)."""
    if accent_mode(settings) == "disabled":
        return None
    return SpeechStyleTracker(
        detector or get_accent_detector(settings.accent_detector_provider),
        settings,
        get_context=get_context,
        spawn=spawn,
    )
