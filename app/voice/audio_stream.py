"""Audio buffer — real-time frame-level utterance detection that triggers STT."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
import struct
import time

from loguru import logger

from app.core.call_logging import describe_error
from app.voice.vad import NoiseFloor, UtteranceSegmenter, compute_pcm16_rms

_SAMPLE_WIDTH_BYTES = 2  # signed 16-bit PCM
_CHANNELS = 1
_DEBUG_FRAME_BYTES = 320  # 20ms @ 8kHz/16-bit, matches UtteranceSegmenter's frame size


def wrap_pcm16_wav(data: bytes, sample_rate: int) -> bytes:
    """Wrap raw signed 16-bit little-endian PCM samples (Exotel
    AgentStream/Voicebot's media-stream payload, after base64 decoding) in a
    minimal valid RIFF/WAVE container using format tag 1 (WAVE_FORMAT_PCM).

    Without this, the flushed bytes are headerless raw audio — passing them
    to Whisper merely named "audio.wav" (no actual WAV structure) gives its
    ffmpeg-based decoder nothing to parse. The underlying sample data is not
    modified in any way — this only adds the container Whisper needs.
    """
    byte_rate = sample_rate * _CHANNELS * _SAMPLE_WIDTH_BYTES
    block_align = _CHANNELS * _SAMPLE_WIDTH_BYTES
    bits_per_sample = _SAMPLE_WIDTH_BYTES * 8

    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,  # subchunk size for plain PCM (no cbSize field)
        1,  # WAVE_FORMAT_PCM
        _CHANNELS,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    data_chunk_header = struct.pack("<4sI", b"data", len(data))
    riff_size = 4 + len(fmt_chunk) + len(data_chunk_header) + len(data)
    riff_header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return riff_header + fmt_chunk + data_chunk_header + data


_wrap_pcm16_wav = wrap_pcm16_wav  # original private name, still used by tests


class AudioBuffer:
    """
    Detects caller utterances in real time from a continuous stream of
    Exotel's ~20ms PCM16 media frames (see UtteranceSegmenter), and flushes
    each COMPLETE utterance to a callback as soon as it's confirmed finished
    — not on a fixed byte-count or a fixed silence timer applied to
    whatever's accumulated. `silence_ms` is a stall-watchdog safety net only
    (see _stall_watchdog): if frames genuinely stop arriving mid-utterance
    (e.g. a network hiccup), it force-finalizes what was captured so far
    rather than waiting forever. `max_bytes` is likewise only a safety cap
    against unbounded memory, not the turn boundary.

    At most one flush (one STT -> AI -> TTS "turn") runs at a time per
    instance. A real PSTN call showed that without this, repeated/overlapping
    audio (e.g. a caller talking longer than the max_bytes window, or
    picked-up echo of our own TTS playback) could trigger a second flush
    while the first turn's STT/OpenAI/TTS was still in flight, producing
    duplicate AI responses for the same call. Utterances that finish while a
    turn is in progress are never dropped — they queue and flush in order
    once the current one finishes.

    Each AudioBuffer instance belongs to exactly one call (created fresh in
    SpeechHandler._ensure_initialized()), so this in-progress flag never
    blocks unrelated calls from processing concurrently.
    """

    def __init__(
        self,
        on_flush: Callable[[bytes], Coroutine],
        silence_ms: int = 2000,
        max_bytes: int = 320_000,
        sample_rate: int = 8000,
        call_sid: str | None = None,
        stream_sid: str | None = None,
        min_rms: float = 150.0,
        speech_start_ms: float = 200,
        end_of_speech_ms: float = 700,
        pre_roll_ms: float = 250,
        frame_ms: int = 20,
        on_utterance_end: Callable[[float], None] | None = None,
        on_speech_start: Callable[[], None] | None = None,
        noise_floor: NoiseFloor | None = None,
    ) -> None:
        self._on_flush = on_flush
        self.noise_floor = noise_floor
        self._on_utterance_end = on_utterance_end
        # Fired synchronously the moment sustained caller speech is confirmed
        # — lets the call session react (cancel a not-yet-audible reply,
        # mark the caller as speaking) without waiting for the utterance to end.
        self._on_speech_start = on_speech_start
        self._silence_ms = silence_ms
        self._sample_rate = sample_rate
        self._call_sid = call_sid
        self._stream_sid = stream_sid
        self._min_rms = min_rms  # kept for [AUDIO_DEBUG] diagnostics only
        frame_bytes = int(frame_ms / 1000 * sample_rate) * _SAMPLE_WIDTH_BYTES
        self._segmenter = UtteranceSegmenter(
            frame_bytes=frame_bytes,
            min_rms=min_rms,
            speech_start_ms=speech_start_ms,
            end_of_speech_ms=end_of_speech_ms,
            pre_roll_ms=pre_roll_ms,
            max_utterance_bytes=max_bytes,
            sample_rate=sample_rate,
            noise_floor=noise_floor,
        )
        self._pending_utterances: deque[bytes] = deque()
        # The stall-watchdog task, re-armed on every push() (see class
        # docstring) — NOT the primary end-of-speech signal anymore.
        self._silence_task: asyncio.Task | None = None
        # EVERY _flush() task currently scheduled or running for this call —
        # not just the most recent one. A real PSTN call showed OpenAI calls
        # continuing for seconds after Exotel's stream had already stopped:
        # a second utterance finalizing while turn 1 was still mid-STT/LLM
        # called _schedule_flush() again, which used to overwrite a SINGLE
        # `_pending_flush_task` reference — cancel_pending() then cancelled
        # only that second (already-a-no-op, since turn_in_progress) task,
        # while the first task — the one actually running STT/OpenAI/TTS —
        # kept going, unreachable, to completion. Tracking every live task
        # in a set means cancel_pending() can never again "lose" the one
        # that's actually doing the work.
        self._flush_tasks: set[asyncio.Task] = set()
        self._turn_in_progress = False

    @property
    def _pending_flush_task(self) -> asyncio.Task | None:
        """An in-flight/scheduled flush task, if any (arbitrary if more than
        one) — kept as a read-only convenience for introspection/tests.
        cancel_pending() itself always uses the full _flush_tasks set, never
        just this one, so it can never "lose" a still-running task."""
        for task in self._flush_tasks:
            if not task.done():
                return task
        return next(iter(self._flush_tasks), None) if self._flush_tasks else None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def has_pending_audio(self) -> bool:
        return self._segmenter.is_speech_active or bool(self._pending_utterances)

    async def push(self, chunk: bytes) -> None:
        event = self._segmenter.process(chunk)
        # Per-packet (~50/s per call) — DEBUG only so the receive loop isn't
        # paying for log formatting/IO on every 20ms frame in production.
        logger.debug(
            f"[INBOUND_AUDIO] call_sid={self._call_sid} stream_sid={self._stream_sid} "
            f"bytes={len(chunk)} speech_active={self._segmenter.is_speech_active}"
        )
        if event.speech_started:
            logger.info(f"[VAD] speech_start rms={event.rms:.1f} call_sid={self._call_sid}")
            if self._on_speech_start:
                self._on_speech_start()
        for utterance in event.utterances:
            duration_ms = len(utterance) / _SAMPLE_WIDTH_BYTES / self._sample_rate * 1000
            logger.info(f"[VAD] speech_end duration_ms={duration_ms:.0f} call_sid={self._call_sid}")
            self._pending_utterances.append(utterance)
            if self._on_utterance_end:
                self._on_utterance_end(time.monotonic())
        # Only an unfinished utterance needs a stall watchdog. A completed
        # utterance or silence must not leave a sleeper after the call ends.
        if self._segmenter.is_speech_active:
            self._reset_stall_watchdog()
        elif self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        if event.utterances:
            # Must NOT `await self._flush()` here. push() is awaited directly
            # from ExotelAudioStream's WebSocket receive loop (via
            # SpeechHandler.handle_audio) — a blocking flush would run the
            # whole STT -> OpenAI -> TTS turn inline on that loop, so no new
            # Exotel media frames are read for the whole turn. Running it as
            # a background task keeps the receive loop free the entire time.
            self._schedule_flush()

    def begin_utterance(self, audio: bytes) -> None:
        """Open a caller utterance with speech already confirmed elsewhere
        (a barge-in during assistant playback) — see UtteranceSegmenter."""
        self._segmenter.begin_utterance(audio)
        self._reset_stall_watchdog()

    def discard_partial(self) -> None:
        """Forget in-progress caller audio (assistant playback started)."""
        self._segmenter.discard_partial()
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()

    def _reset_stall_watchdog(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = asyncio.create_task(self._stall_watchdog())
        self._silence_task.add_done_callback(self._log_flush_task_result)

    async def _stall_watchdog(self) -> None:
        await asyncio.sleep(self._silence_ms / 1000)
        finalized = self._segmenter.force_finalize()
        if finalized is not None:
            duration_ms = len(finalized) / _SAMPLE_WIDTH_BYTES / self._sample_rate * 1000
            logger.info(
                f"[VAD] speech_end duration_ms={duration_ms:.0f} reason=stall "
                f"call_sid={self._call_sid}"
            )
            self._pending_utterances.append(finalized)
            if self._on_utterance_end:
                self._on_utterance_end(time.monotonic())
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        task = asyncio.create_task(self._flush())
        self._flush_tasks.add(task)
        task.add_done_callback(self._on_flush_task_done)

    def _on_flush_task_done(self, task: asyncio.Task) -> None:
        self._flush_tasks.discard(task)
        self._log_flush_task_result(task)

    @staticmethod
    def _log_flush_task_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"[audio_buffer] background flush failed: {describe_error(exc)}")

    def _log_audio_debug(self, data: bytes) -> None:
        """[AUDIO_DEBUG]: exactly what is about to be sent to STT, computed
        directly from the finalized PCM — frame count, active-vs-silent
        frame breakdown, and total duration — so a real call showing an
        unexpectedly long utterance (e.g. audio_duration_ms=9780 for what
        the caller says was a ~2-3s sentence) can be diagnosed from logs
        alone: was it genuinely that long, or is background noise/echo
        being counted as continuous speech? See UtteranceSegmenter for how
        the SAME min_rms threshold decided speech-start/end in real time;
        this just re-derives the frame-by-frame breakdown for visibility.
        """
        frame_count = len(data) // _DEBUG_FRAME_BYTES
        active_frames = sum(
            1
            for i in range(frame_count)
            if compute_pcm16_rms(data[i * _DEBUG_FRAME_BYTES : (i + 1) * _DEBUG_FRAME_BYTES])
            >= self._min_rms
        )
        duration_ms = len(data) / _SAMPLE_WIDTH_BYTES / self._sample_rate * 1000
        speech_ms = (
            active_frames * _DEBUG_FRAME_BYTES / _SAMPLE_WIDTH_BYTES / self._sample_rate * 1000
        )
        logger.info(
            f"[AUDIO_DEBUG] call_sid={self._call_sid} frames={frame_count} "
            f"pcm_bytes={len(data)} duration_ms={duration_ms:.0f} "
            f"speech_ms={speech_ms:.0f} silence_ms={duration_ms - speech_ms:.0f} "
            f"active_frames={active_frames} silent_frames={frame_count - active_frames}"
        )

    async def _flush(self) -> None:
        logger.info(f"[VOICE] flush scheduled call_sid={self._call_sid}")
        if self._turn_in_progress:
            # A turn is already running for this call. The finalized
            # utterance that triggered this attempt stays queued in
            # _pending_utterances (never dropped) — once the running turn
            # finishes it re-checks the queue and flushes the next one.
            logger.info(f"[VOICE] flush skipped/already_processing call_sid={self._call_sid}")
            return
        if not self._pending_utterances:
            return

        self._turn_in_progress = True
        logger.info(f"[VOICE] turn started call_sid={self._call_sid}")
        try:
            data = self._pending_utterances.popleft()
            self._log_audio_debug(data)
            wav_bytes = wrap_pcm16_wav(data, self._sample_rate)
            logger.info(
                f"[STT] incoming audio decoded_bytes={len(data)} wav_bytes={len(wav_bytes)} "
                f"format=pcm_s16le sample_rate={self._sample_rate} channels={_CHANNELS} "
                f"sample_width_bytes={_SAMPLE_WIDTH_BYTES}"
            )
            await self._on_flush(wav_bytes)
        finally:
            self._turn_in_progress = False
            logger.info(f"[VOICE] turn completed call_sid={self._call_sid}")

        # Every entry in _pending_utterances is already a COMPLETE, finished
        # utterance (end-of-speech already confirmed when it was queued) —
        # unlike the old fixed-buffer design, there's no "might still be
        # incomplete" ambiguity here, so it's always correct to flush the
        # next one immediately rather than waiting on anything further.
        if self._pending_utterances:
            self._schedule_flush()

    async def cancel_pending(self) -> None:
        """Cancel the stall watchdog AND any in-flight/queued flush turn,
        awaiting their actual termination. Safe to call any time — used
        when the call is stopping so no stale task can go on to run
        STT/OpenAI/TTS (or send audio) after the call is already closed.

        Cancelling a task that's mid-STT/OpenAI/TTS is safe: asyncio raises
        CancelledError at the current await point, _flush()'s try/finally
        still resets _turn_in_progress and logs "turn completed", and
        _log_flush_task_result already treats a cancelled task as expected
        (no error logged).

        Cancels EVERY task in _flush_tasks, not just one — if a second
        utterance finalized (and called _schedule_flush() again) while an
        earlier one was still actually running its STT/OpenAI/TTS turn,
        both must be reachable here, or the actually-running one is never
        cancelled at all (see _flush_tasks' docstring in __init__).
        """
        tasks = {self._silence_task, *self._flush_tasks}
        tasks.discard(None)
        pending = [t for t in tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # Already logged via _log_flush_task_result's done-callback —
                # just noted here at debug level for this specific context.
                logger.debug(
                    f"[audio_buffer] task raised during cancel_pending: {describe_error(exc)}"
                )

    async def drain(self) -> None:
        """Force-finalize and flush any remaining audio (e.g. on call end)."""
        await self.cancel_pending()
        finalized = self._segmenter.force_finalize()
        if finalized is not None:
            self._pending_utterances.append(finalized)
            if self._on_utterance_end:
                self._on_utterance_end(time.monotonic())
        await self._flush()
