"""Lightweight PCM16 energy-based voice-activity gate.

Real PSTN calls showed the receptionist responding to transcripts like
"you", "thank you", and "bye-bye." that the caller never actually said.
Whisper is trained on captioned video and tends to hallucinate exactly
these filler phrases when handed near-silent audio (PSTN line/comfort
noise, faint bleed-back of our own TTS, or other non-speech audio) — so a
text-based check on the transcript ("if transcript == 'you': ignore") can't
be trusted and would only hide the symptom.

Instead, this module looks at the actual PCM16 samples *before* anything is
sent to Whisper: audio whose RMS energy never crosses a minimum threshold
is discarded and never reaches STT. This intentionally does not depend on
whether ElevenLabs TTS happens to be playing at the time — genuine caller
speech is far louder than line noise, so it passes this gate and is
processed as a real turn even if it arrives while the assistant is talking
(barge-in). No third-party VAD library is needed for this.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import struct

# Fragments shorter than this can't be judged reliably and are treated as
# not-speech rather than risking a division/RMS computation on noise.
_MIN_SAMPLES = 8


@dataclass(frozen=True)
class VoiceActivityResult:
    is_speech: bool
    rms: float
    sample_count: int
    reason: str


def extract_wav_pcm_payload(audio_bytes: bytes) -> bytes:
    """Return the raw PCM sample bytes from a RIFF/WAVE container (as
    produced by app.voice.audio_stream._wrap_pcm16_wav) by walking its
    chunks to the "data" chunk — never a hardcoded header-size offset.

    If `audio_bytes` isn't a RIFF/WAVE container at all, it's returned
    unchanged: callers that already hand this raw PCM16 (no WAV wrapper)
    get treated as raw PCM directly instead of being silently discarded.
    """
    if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        offset = 12
        while offset + 8 <= len(audio_bytes):
            chunk_id = audio_bytes[offset : offset + 4]
            (chunk_size,) = struct.unpack_from("<I", audio_bytes, offset + 4)
            data_start = offset + 8
            if chunk_id == b"data":
                return audio_bytes[data_start : data_start + chunk_size]
            offset = data_start + chunk_size + (chunk_size & 1)  # chunks are word-aligned
        return b""
    return audio_bytes


def compute_pcm16_rms(pcm16_bytes: bytes) -> float:
    """Root-mean-square amplitude of signed 16-bit little-endian PCM samples."""
    sample_count = len(pcm16_bytes) // 2
    if sample_count == 0:
        return 0.0
    samples = struct.unpack(f"<{sample_count}h", pcm16_bytes[: sample_count * 2])
    return (sum(s * s for s in samples) / sample_count) ** 0.5


def detect_voice_activity(pcm16_bytes: bytes, min_rms: float) -> VoiceActivityResult:
    """Decide whether `pcm16_bytes` has enough energy to plausibly be
    speech, using nothing but its RMS amplitude — the smallest gate that
    reliably rejects obvious silence/line-noise/echo without a full VAD
    library or any assumption about TTS playback state."""
    sample_count = len(pcm16_bytes) // 2
    if sample_count < _MIN_SAMPLES:
        return VoiceActivityResult(
            is_speech=False, rms=0.0, sample_count=sample_count, reason="too_short"
        )
    rms = compute_pcm16_rms(pcm16_bytes)
    if rms < min_rms:
        return VoiceActivityResult(
            is_speech=False, rms=rms, sample_count=sample_count, reason="below_energy_threshold"
        )
    return VoiceActivityResult(is_speech=True, rms=rms, sample_count=sample_count, reason="speech")


class NoiseFloor:
    """Running estimate of the line's background-noise level (per call).

    A fixed RMS threshold works on a quiet line but fails on a noisy one
    (traffic, fan, crowd, PSTN hiss): the noise itself crosses the fixed
    threshold, is treated as the caller talking, gets sent to STT and comes
    back as a hallucinated sentence the assistant then answers — or it
    "barges in" over the assistant. Requiring speech to be clearly above
    the measured floor (``multiplier`` x floor) keeps the gate tight on
    noisy lines while leaving quiet lines exactly as sensitive as before
    (``min_rms`` is always the lower bound).

    The floor is a LOW PERCENTILE of recent frame energies (last few
    seconds), not an average: natural speech always contains short gaps,
    so the 10th percentile stays at noise level while someone talks, yet a
    new steady noise source is learned within the window length.
    """

    def __init__(
        self,
        multiplier: float = 3.0,
        max_threshold: float = 2000.0,
        window_frames: int = 200,  # 4 s of 20 ms frames
        percentile: float = 10.0,
        min_frames: int = 25,  # 0.5 s before the estimate is trusted
    ) -> None:
        self._multiplier = multiplier
        self._max_threshold = max_threshold
        self._percentile = percentile
        self._min_frames = min_frames
        self._window: deque[float] = deque(maxlen=window_frames)
        self._value = 0.0
        self._since_recompute = 0

    @property
    def value(self) -> float:
        return self._value

    def update(self, rms: float) -> None:
        self._window.append(rms)
        self._since_recompute += 1
        # Recompute every 5 frames (100 ms) — cheap and plenty responsive.
        if len(self._window) >= self._min_frames and self._since_recompute >= 5:
            self._since_recompute = 0
            ordered = sorted(self._window)
            idx = min(len(ordered) - 1, int(len(ordered) * self._percentile / 100))
            self._value = ordered[idx]

    def threshold(self, min_rms: float) -> float:
        if self._multiplier <= 0:
            return min_rms
        return max(min_rms, min(self._value * self._multiplier, self._max_threshold))


@dataclass(frozen=True)
class BargeInResult:
    """A confirmed barge-in: `speech` is the caller audio from just before the
    sustained streak began (pre-roll) through the confirming frame;
    `remainder` is whatever followed in the same inbound chunk, still to be
    run through the ordinary turn detector (it may already contain the
    caller's end-of-speech silence)."""

    speech: bytes
    remainder: bytes


class BargeInDetector:
    """Frame-level speech-activity confirmer, used only while our own TTS is
    audible.

    A single loud ~20ms Exotel media packet must never be enough to
    interrupt the assistant — comfort noise, line clicks, and brief TTS
    acoustic bleed-back can all produce one isolated loud frame. Real PSTN
    calls showed blob-level RMS (computed over several seconds of
    accumulated audio at once) is not enough either: it can't tell a few
    genuine words apart from several seconds of echo/noise averaged
    together, which is exactly what let stale TTS-window audio reach
    Whisper and produce a fabricated response, restarting the loop.

    This instead requires *sustained* per-frame activity (the echo guard):
    only once enough active frames — each individually over `min_rms` —
    add up to `min_active_ms` is a barge-in confirmed. Short dips of up to
    `gap_tolerance_ms` inside the streak (stop consonants, breaths between
    syllables) are tolerated so ordinary speech can confirm; longer silence
    resets the streak. `pre_roll_ms` of audio before the streak is kept so
    the caller's first syllable reaches STT.

    While `suppressed` (the grace window at the start of each assistant
    sentence, when echo of our own voice is loudest), frames are still
    captured into the streak but do not count towards `min_active_ms`: an
    interruption that starts inside the window confirms only once it has
    continued for `min_active_ms` after it, with all of its audio kept.
    """

    def __init__(
        self,
        frame_bytes: int,
        min_rms: float,
        min_active_ms: float,
        sample_rate: int = 8000,
        gap_tolerance_ms: float = 0,
        pre_roll_ms: float = 0,
        noise_floor: NoiseFloor | None = None,
    ) -> None:
        self._frame_bytes = max(2, frame_bytes - (frame_bytes % 2))  # whole samples only
        self._min_rms = min_rms
        # Also keeps LEARNING the floor during playback, so the gate is
        # already calibrated when the caller first speaks after the
        # greeting. The low-percentile estimate ignores echo of our own
        # speech (it has natural gaps), so echo does not inflate it.
        self._noise_floor = noise_floor
        frame_ms = self._frame_bytes / 2 / sample_rate * 1000
        self._min_active_frames = max(1, math.ceil(min_active_ms / frame_ms))
        self._gap_tolerance_frames = max(0, math.floor(gap_tolerance_ms / frame_ms))
        self._pre_roll: deque[bytes] = deque(maxlen=max(0, math.ceil(pre_roll_ms / frame_ms)))
        self._pending = bytearray()
        self._active_streak = 0
        self._armed_streak = 0  # active frames of the streak outside the grace window
        self._gap = 0
        self._captured = bytearray()

    def detect(self, chunk: bytes, suppressed: bool = False) -> BargeInResult | None:
        """Feed one inbound chunk; returns a BargeInResult once sustained
        speech is confirmed (the detector then resets itself), else None."""
        self._pending.extend(chunk)
        while len(self._pending) >= self._frame_bytes:
            frame = bytes(self._pending[: self._frame_bytes])
            del self._pending[: self._frame_bytes]
            threshold = (
                self._noise_floor.threshold(self._min_rms) if self._noise_floor else self._min_rms
            )
            rms = compute_pcm16_rms(frame)
            if self._noise_floor is not None:
                self._noise_floor.update(rms)
            if rms >= threshold:
                if self._active_streak == 0:
                    self._captured = bytearray(b"".join(self._pre_roll))
                    self._pre_roll.clear()
                self._active_streak += 1
                if not suppressed:
                    self._armed_streak += 1
                self._gap = 0
                self._captured.extend(frame)
                if self._armed_streak >= self._min_active_frames:
                    result = BargeInResult(
                        speech=bytes(self._captured), remainder=bytes(self._pending)
                    )
                    self.reset()
                    return result
            elif self._active_streak and self._gap < self._gap_tolerance_frames:
                self._gap += 1
                self._captured.extend(frame)
            else:
                self._active_streak = 0
                self._armed_streak = 0
                self._gap = 0
                self._captured.clear()
                self._pre_roll.append(frame)
        return None

    def process(self, chunk: bytes) -> bytes | None:
        """detect(), flattened: the confirmed speech plus everything after it."""
        result = self.detect(chunk)
        return None if result is None else result.speech + result.remainder

    def reset(self) -> None:
        self._pending.clear()
        self._pre_roll.clear()
        self._active_streak = 0
        self._armed_streak = 0
        self._gap = 0
        self._captured.clear()


@dataclass
class SegmenterEvent:
    """Result of feeding one chunk through UtteranceSegmenter.process()."""

    speech_started: bool = False
    utterances: list[bytes] = field(default_factory=list)
    rms: float = 0.0


class UtteranceSegmenter:
    """Continuous, real-time, frame-level turn detector.

    Replaces "accumulate raw bytes for up to ~2s (32,000 bytes) or 800ms of
    no new pushes, then run one blob-level RMS check" with the same kind of
    thing a human listener does: notice speech starting almost immediately,
    keep listening while it continues (including short natural pauses), and
    only consider the utterance finished after a real stretch of silence.

    This also fixes a structural weakness of blob-level RMS: a short "Hello"
    surrounded by silence inside a large accumulated buffer gets its energy
    diluted by all the silence padding, which is why real calls showed
    genuine short utterances measuring under the RMS floor. Checking each
    ~20ms frame's own RMS independently — with sustained-duration
    confirmation on both ends so isolated noise/clicks don't count — doesn't
    have that problem.

    Frames are classified with the SAME rms threshold already used
    elsewhere (settings.stt_min_rms_energy) — just applied per-frame with a
    duration requirement, not raised.
    """

    def __init__(
        self,
        frame_bytes: int,
        min_rms: float,
        speech_start_ms: float,
        end_of_speech_ms: float,
        pre_roll_ms: float,
        max_utterance_bytes: int,
        sample_rate: int = 8000,
        start_tolerance_frames: int = 1,
        noise_floor: NoiseFloor | None = None,
    ) -> None:
        self._frame_bytes = max(2, frame_bytes - (frame_bytes % 2))
        self._min_rms = min_rms
        self._noise_floor = noise_floor
        frame_ms = self._frame_bytes / 2 / sample_rate * 1000
        self._speech_start_frames = max(1, math.ceil(speech_start_ms / frame_ms))
        self._end_of_speech_frames = max(1, math.ceil(end_of_speech_ms / frame_ms))
        pre_roll_frames = max(0, math.ceil(pre_roll_ms / frame_ms))
        self._max_utterance_bytes = max_utterance_bytes
        # A single ~20ms dip below the RMS floor (a stop-consonant, a
        # breath, a brief line-noise blip) must not restart the whole
        # speech-start confirmation from zero — real speech onsets aren't
        # perfectly continuous. Tolerating up to this many consecutive
        # below-threshold frames during confirmation costs nothing: the
        # pre-roll buffer already captures the lead-in audio regardless of
        # exactly how many frames it took to confirm, so no audio is lost
        # either way — this only prevents losing the first syllable/word to
        # a false restart.
        self._start_tolerance_frames = start_tolerance_frames
        # Deque sized to hold the pre-roll window PLUS the confirming active
        # streak itself, so "list(self._pre_roll)" at confirmation time is
        # exactly [pre_roll_ms of lead-in] + [the speech that confirmed it] —
        # no separate bookkeeping needed for the confirming frames.
        self._pre_roll: deque[bytes] = deque(maxlen=pre_roll_frames + self._speech_start_frames)
        self._pending = bytearray()
        self._active = False  # True once an utterance is confirmed in-progress
        self._active_streak = 0
        self._active_gap = 0
        self._silence_streak = 0
        self._utterance_frames: list[bytes] = []
        self._utterance_bytes = 0

    @property
    def is_speech_active(self) -> bool:
        return self._active

    def process(self, chunk: bytes) -> SegmenterEvent:
        self._pending.extend(chunk)
        event = SegmenterEvent()
        while len(self._pending) >= self._frame_bytes:
            frame = bytes(self._pending[: self._frame_bytes])
            del self._pending[: self._frame_bytes]
            rms = compute_pcm16_rms(frame)
            event.rms = rms
            threshold = (
                self._noise_floor.threshold(self._min_rms) if self._noise_floor else self._min_rms
            )
            is_active = rms >= threshold
            if self._noise_floor is not None:
                self._noise_floor.update(rms)

            if not self._active:
                self._pre_roll.append(frame)
                if is_active:
                    self._active_streak += 1
                    self._active_gap = 0
                else:
                    self._active_gap += 1
                    if self._active_gap > self._start_tolerance_frames:
                        self._active_streak = 0
                        self._active_gap = 0
                if self._active_streak >= self._speech_start_frames:
                    self._active = True
                    self._utterance_frames = list(self._pre_roll)
                    self._utterance_bytes = sum(len(f) for f in self._utterance_frames)
                    self._pre_roll.clear()
                    self._active_streak = 0
                    self._active_gap = 0
                    self._silence_streak = 0
                    event.speech_started = True
            else:
                self._utterance_frames.append(frame)
                self._utterance_bytes += len(frame)
                self._silence_streak = 0 if is_active else self._silence_streak + 1
                if (
                    self._silence_streak >= self._end_of_speech_frames
                    or self._utterance_bytes >= self._max_utterance_bytes
                ):
                    event.utterances.append(b"".join(self._utterance_frames))
                    self._reset_active()
        return event

    def begin_utterance(self, audio: bytes) -> None:
        """Start an utterance that is already confirmed as speech elsewhere
        (a barge-in confirmed by BargeInDetector), seeded with its audio, so
        the caller's words are not re-confirmed or clipped."""
        self._reset_active()
        self._pending.clear()
        self._active = True
        self._utterance_frames = [audio]
        self._utterance_bytes = len(audio)

    def discard_partial(self) -> None:
        """Forget all in-progress audio: pre-roll, unconfirmed onset, and an
        open utterance. Used when assistant audio becomes audible — from
        then on inbound audio is judged by the stricter barge-in detector,
        and nothing from before playback may be stitched onto a later turn.
        (A caller *turn* already in progress would have cancelled the
        not-yet-audible reply instead, so this only drops line noise or
        speech overlapping the very start of the greeting.)"""
        self._reset_active()
        self._pending.clear()

    def force_finalize(self) -> bytes | None:
        """Force-finalize whatever utterance is currently in progress (used
        as a stall safety net if no further audio ever arrives to naturally
        trigger end-of-speech detection). None if nothing is active."""
        if not self._active:
            return None
        finalized = b"".join(self._utterance_frames)
        self._reset_active()
        return finalized

    def _reset_active(self) -> None:
        self._active = False
        self._utterance_frames = []
        self._utterance_bytes = 0
        self._active_streak = 0
        self._active_gap = 0
        self._silence_streak = 0
        self._pre_roll.clear()
