"""SpeechHandler — full-duplex STT → AI conversation → TTS for one call.

One SpeechHandler exists per call (created by the /telephony/stream route);
it holds no module-level state. Its responsibilities are split so caller
audio is never blocked or discarded because the assistant is busy:

- Receive path (`handle_audio`, driven by the WebSocket receive loop): only
  cheap, synchronous-ish work. Caller audio always reaches a speech
  detector: the turn segmenter (AudioBuffer) while the assistant is silent,
  or the stricter barge-in detector while assistant audio is audible.
- Response task: exactly one per call at a time, owned by this handler.
  It runs one unit of assistant work — the greeting, or STT → LLM → TTS for
  a caller turn — including waiting out the audio's playback. Every
  interruption cancels this real asyncio.Task; stale output is additionally
  blocked by a turn-id check before every outbound audio frame.
- Post-call work (`finalize`) runs only after the stream has stopped.

Interruption rules:
- Caller speech confirmed while the assistant is AUDIBLE (barge-in): cancel
  the response, tell Exotel to drop its buffered audio, and treat the
  caller's speech — captured from just before it started — as a new turn.
  The interrupted reply is never resumed — unless that "speech" turns out to
  be noise (empty or hallucinated transcript): then the sentence that was
  cut off is said again instead of leaving the caller in silence.
- Barge-in grace: caller audio in the first barge_in_grace_ms of each
  assistant sentence (our own echo is loudest there) is captured but cannot
  confirm an interruption by itself.
- Caller speech that resumes while a reply is still being PREPARED (nothing
  audible yet): the caller had only paused, so the in-flight turn is
  abandoned and its audio merged with what follows into ONE caller turn.
- Unfinished-sounding transcript ("... chahiye aur", "so,", "haan"): the
  reply waits up to unfinished_turn_extra_ms for the caller to go on, and
  merges what follows the same way.
"""

from __future__ import annotations

import asyncio
from collections import deque
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import aclosing
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.business_context import build_business_context
from app.ai.context_manager import CallContext, context_manager
from app.ai.conversation import ConversationEngine
from app.rag.index import get_knowledge_index
from app.ai.department_classifier import DepartmentClassifier
from app.ai.language_profile import (
    ACKNOWLEDGEMENTS,
    LANGUAGES,
    NEUTRAL_WORDS,
    analyze_language,
    update_language_profile,
)
from app.ai.lead_extractor import LeadExtractor
from app.ai.openai_client import OpenAIClient
from app.ai.name_capture import extract_caller_name
from app.ai.prompt_manager import PromptManager
from app.ai.summary_generator import SummaryGenerator
from app.core.call_logging import log_call_event, mask_phone
from app.core.config import get_settings
from app.services.call_recording_service import CallRecordingService
from app.services.call_service import CallService
from app.services.department_service import DepartmentService
from app.services.email_service import EmailService
from app.services.lead_service import LeadService
from app.telephony.transfer import GENERAL_DEPARTMENT, resolve_transfer_number
from app.voice.accent_detector import NEUTRAL as NEUTRAL_SPEECH_STYLE
from app.voice.accent_detector import accent_mode, build_speech_style_tracker
from app.voice.audio_stream import AudioBuffer, wrap_pcm16_wav
from app.voice.pcm import apply_fade, sanitize_pcm16
from app.voice.turn_metrics import TurnMetrics
from app.voice.vad import BargeInDetector, BargeInResult, NoiseFloor, extract_wav_pcm_payload
from app.voice.transcript_filter import classify_turn_end, is_hallucination
from app.voice.tts_text import normalize_for_tts
from app.voice.voice_manager import VoiceManager

# The assistant appends this marker to its closing line when the call is
# complete (see app/prompts/system.py); it is stripped before TTS.
END_CALL_TOKEN = "[END_CALL]"
_END_CALL_RE = re.compile(r"\[?\s*END_CALL\s*\]?", re.IGNORECASE)
# "[TRANSFER:infrastructure]" (or a bare "[TRANSFER]") ends the call by
# handing the caller to a person — see app/telephony/transfer.py.
_TRANSFER_RE = re.compile(r"\[\s*TRANSFER\s*(?::\s*([^\]]*?))?\s*\]", re.IGNORECASE)

_DIGIT_WORDS = "zero|one|two|three|four|five|six|seven|eight|nine|shunya|ek|do|teen|char|paanch|chhe|saat|aath|nau"
_PHONE_NUMBER_RE = re.compile(rf"(?i)(?:\+?91[\s-]?)?(?:(?:{_DIGIT_WORDS})[\s-]*|\d[\s-]*){{10,}}")

# Exotel media frames: 200 ms of PCM16 mono at 8 kHz is its minimum size.
_EXOTEL_FRAME_BYTES = 3200
_PCM_BYTES_PER_SAMPLE = 2
_FADE_BYTES = 128  # 8 ms at 8 kHz PCM16 — see app.voice.pcm.apply_fade
# An unfinished-sounding turn waits for the caller to go on at most this many
# times in a row; then it is answered, so "hello... hello..." still gets a reply.
_MAX_CONTINUATION_MERGES = 2


class TurnState(StrEnum):
    """Conversation state of one call.

    CONNECTED -> GREETING -> LISTENING -> CALLER_SPEAKING -> PROCESSING
    -> TTS_PLAYING -> LISTENING (loop)
    Barge-in:           GREETING/TTS_PLAYING -> INTERRUPTED -> CALLER_SPEAKING
    Caller resumes:     PROCESSING -> CALLER_SPEAKING (turn merged)
    Call end:           <any state> -> ENDED (terminal; no further work)

    GREETING/TTS_PLAYING are the only states in which assistant audio is
    audible to the caller, and therefore the only states in which inbound
    audio is judged by the echo-resistant barge-in detector.
    """

    CONNECTED = "CONNECTED"
    GREETING = "GREETING"
    LISTENING = "LISTENING"
    CALLER_SPEAKING = "CALLER_SPEAKING"
    PROCESSING = "PROCESSING"
    TTS_PLAYING = "TTS_PLAYING"
    INTERRUPTED = "INTERRUPTED"
    ENDED = "ENDED"


_AUDIBLE_STATES = frozenset({TurnState.GREETING, TurnState.TTS_PLAYING})


@dataclass
class _Response:
    """One unit of assistant work, run by exactly one response task."""

    turn_id: int
    kind: str
    metrics: TurnMetrics
    playing_state: TurnState = TurnState.TTS_PLAYING
    # Caller audio this response answers. Cleared once the reply becomes
    # audible — from then on it can no longer be merged into a later turn.
    caller_pcm: bytes | None = None
    transcript: str | None = None
    context_snapshot: tuple[int, int] | None = None
    committed: bool = False  # first audio frame handed to the transport
    spoken: list[str] = field(default_factory=list)
    recorded: bool = False  # what was spoken is already in the conversation history
    outcome: str = "completed"
    # The transcript sounded unfinished; waiting for the caller to go on.
    awaiting_continuation: bool = False
    # Last reply segment handed to TTS (the one carrying END_CALL, if any).
    last_segment: str | None = None
    reply_audio_ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Set once the caller's transcript and the call language for this turn
    # are known and the reply is about to be generated.
    analyzed: asyncio.Event = field(default_factory=asyncio.Event)
    reply_generated: asyncio.Event = field(default_factory=asyncio.Event)
    contains_control: bool = False
    filler_played: bool = False
    # TTS language for every sentence of this response, fixed at its first.
    language: str | None = None
    voice_profile: str | None = None
    voice_selected: bool = False
    audio_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


ResponseRunner = Callable[[_Response], Awaitable[None]]

# Greeting audio by (text, language): the greeting never changes, so it is
# synthesized once per process (prewarmed at startup) and replayed instantly.
_GREETING_AUDIO_CACHE: dict[tuple[str, str], list[bytes]] = {}
# Short acknowledgements played only while a slow reply is still being
# prepared (see SpeechHandler._maybe_play_filler). Each at most once a call.
_FILLER_TEXTS: dict[str, tuple[str, ...]] = {
    "en": ("Okay.", "Sure.", "Right."),
    "hi": ("जी, sure।", "Okay।", "Sure।"),
    "hi-en": ("जी, sure।", "Okay।", "Sure।"),
}
_FILLER_AUDIO_CACHE: dict[tuple[str, str], list[bytes]] = {}
_FILLER_DELAY_S = 1.5
_FILLER_MIN_TURN_GAP = 3
# Caller turns that close the call or ask for a person: a filler there
# would come right before a goodbye/transfer line.
_CLOSING_WORDS = frozenset(
    "bye goodbye thanks thank shukriya dhanyavad dhanyavaad transfer connect "
    "धन्यवाद शुक्रिया".split()
)
_YES_NO_WORDS = ACKNOWLEDGEMENTS | NEUTRAL_WORDS | frozenset({"hai", "है", "please"})
_WORD_SPLIT = re.compile(r"[\s,.!?;:।]+")


async def prewarm_greeting(language: str = "en") -> None:
    """Synthesize the greeting once at startup so the first caller hears it
    immediately. Best-effort: any failure just means the first call
    synthesizes it live, as before."""
    from app.prompts.system import INITIAL_GREETING

    settings = get_settings()
    text = INITIAL_GREETING.format(company_name=settings.company_name)
    if (text, language) in _GREETING_AUDIO_CACHE:
        return
    try:
        voice = VoiceManager()
        # Cached under the greeting text, synthesized as the voice should say it.
        pcm = await voice.synthesize(normalize_for_tts(text, language), language)
        if voice.last_used_fallback:
            # The fallback provider speaks in a different voice; caching it
            # would pin every greeting to that voice for the whole process.
            logger.warning("[VOICE] greeting prewarm used fallback TTS; not cached")
            return
        pcm = sanitize_pcm16(pcm)
        if not pcm:
            return
        pcm = apply_fade(pcm, fade_in=True, fade_out=True)
        frames = [
            pcm[i : i + _EXOTEL_FRAME_BYTES].ljust(_EXOTEL_FRAME_BYTES, b"\x00")
            for i in range(0, len(pcm), _EXOTEL_FRAME_BYTES)
        ]
        _GREETING_AUDIO_CACHE[(text, language)] = frames
        logger.info(f"[VOICE] greeting prewarmed frames={len(frames)}")
    except Exception as exc:
        logger.warning(f"[VOICE] greeting prewarm failed error={exc!r}")


async def prewarm_fillers() -> None:
    """Best-effort startup cache for acknowledgement audio in every voice."""
    for language, texts in _FILLER_TEXTS.items():
        for text in texts:
            if (text, language) in _FILLER_AUDIO_CACHE:
                continue
            try:
                voice = VoiceManager()
                pcm = await voice.synthesize(normalize_for_tts(text, language), language)
                if voice.last_used_fallback:
                    continue
                pcm = apply_fade(sanitize_pcm16(pcm), fade_in=True, fade_out=True)
                if not pcm:
                    continue
                _FILLER_AUDIO_CACHE[(text, language)] = [
                    pcm[offset : offset + _EXOTEL_FRAME_BYTES].ljust(
                        _EXOTEL_FRAME_BYTES, b"\x00"
                    )
                    for offset in range(0, len(pcm), _EXOTEL_FRAME_BYTES)
                ]
            except Exception as exc:
                logger.warning(
                    f"[VOICE] filler prewarm failed language={language} error={exc!r}"
                )
    logger.info(f"[VOICE] filler cache ready entries={len(_FILLER_AUDIO_CACHE)}")


class SpeechHandler:
    """
    Entry point for each call's audio pipeline.
    Receives audio bytes → transcribes → runs conversation → synthesizes reply.

    All external I/O components (VoiceManager, OpenAIClient) accept injection
    so the handler is fully testable without live API keys.
    """

    def __init__(
        self,
        db: AsyncSession,
        voice: VoiceManager | None = None,
        ai_client: OpenAIClient | None = None,
    ) -> None:
        self._db = db
        self._voice = voice or VoiceManager()
        self._lead_extractor = LeadExtractor(client=ai_client)
        self._summary_generator = SummaryGenerator(client=ai_client)
        self._prompt_manager = PromptManager()
        self._ai_client = ai_client
        self._settings = get_settings()
        self._context: CallContext | None = None
        self._send_audio: Callable[[bytes], Coroutine[Any, Any, None]] | None = None
        self._clear_audio: Callable[[], Coroutine[Any, Any, None]] | None = None
        self._hangup: Callable[[], Coroutine[Any, Any, None]] | None = None
        # Silence watchdog: re-prompts a caller who has gone quiet, then
        # closes the call politely instead of holding dead air forever.
        self._silence_task: asyncio.Task | None = None
        self._silence_reprompts = 0
        self._ending = False
        # Times the pending reply was abandoned because the caller resumed
        # speaking before hearing it (reset once any reply becomes audible).
        self._resume_merges = 0
        # Unfinished-sounding turns merged with what the caller said next
        # (reset once any reply becomes audible).
        self._continuation_merges = 0
        self._departments: list[dict[str, Any]] = []
        self._engine: ConversationEngine | None = None
        self._buffer: AudioBuffer | None = None
        self._initialized = False
        self._stopped = False
        self._turn_state = TurnState.CONNECTED
        self._turn_id = 0
        self._segment_id = 0
        # (pcm frame, response, segment text if this is the segment's first frame)
        self._outbound_queue: asyncio.Queue[tuple] = asyncio.Queue(maxsize=100)
        self._pending_audio_bytes = 0
        self._outbound_writer_task: asyncio.Task | None = None
        self._turn_committed = True
        # Display-only counter of caller turns for log banners.
        self._turn_number = 0
        # The active response (greeting or caller turn) and its task.
        self._response: _Response | None = None
        self._response_task: asyncio.Task | None = None
        # Fillers already played this call, and the caller turn of the last.
        self._used_fillers: set[str] = set()
        self._last_filler_turn: int | None = None
        # Every task this handler created and has not yet seen finish —
        # cancelled and awaited by mark_stopped(), so nothing outlives the call.
        self._tasks: set[asyncio.Task] = set()
        # Optional speech-style (accent) detection for TTS personalisation
        # only — runs in background tasks, never on the response path.
        # None when the feature is disabled.
        self._accent_mode = accent_mode(self._settings)
        self._speech_style = build_speech_style_tracker(
            self._settings, get_context=lambda: self._context, spawn=self._spawn_background
        )
        # True while the utterance being collected began as a barge-in over
        # assistant playback (it may carry echo) — never used for speech style.
        self._utterance_overlapped_playback = False
        self._voice_profile_fallbacks: set[tuple[str, str]] = set()
        # Audio of a caller turn abandoned because the caller kept talking
        # before hearing any reply; prepended to their next utterance.
        self._carryover_pcm = b""
        self._carryover_text = ""
        self._carryover_segments = 0
        self._carryover_speech_start: float | None = None
        self._speech_start_at: float | None = None
        self._utterance_end_times: deque[float] = deque()
        # Monotonic time at which the audio already handed to Exotel will
        # have finished playing to the caller.
        self._playback_until = 0.0
        self._barge_in_detector: BargeInDetector | None = None
        self._noise_floor: NoiseFloor | None = None
        # Inbound packets judged not-speech while the assistant was audible;
        # summarized once per playback window instead of logged per packet.
        self._tts_discard_packets = 0
        self._tts_discard_bytes = 0
        # Last caller turn's TTS startup / transport send time (ms).
        self._last_tts_ms = 0.0
        self._last_send_ms = 0.0
        # (playback start time, sentence text, response) for each assistant
        # sentence sent — for the barge-in grace window and for knowing
        # which sentence a barge-in cut off.
        self._sentence_starts: deque[tuple[float, str, _Response]] = deque(maxlen=16)
        # (sentence, it was the call's closing line) cut off by the last
        # barge-in; said again if that barge-in turns out to be noise.
        self._interrupted_sentence: tuple[str, bool] | None = None

    @property
    def _call_sid(self) -> str | None:
        return self._context.call_sid if self._context else None

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def start(self, call_sid: str, caller_number: str, stream_sid: str | None = None) -> None:
        """Begin the live session from the stream's 'start' event.

        Loads configuration, then starts the greeting as a background
        response task and returns immediately — the WebSocket receive loop
        must keep reading caller audio while the greeting plays.
        """
        if self._initialized:
            return
        await self._initialize_session(call_sid, caller_number, stream_sid)
        if self._settings.ai_auto_greeting:
            self._start_response("greeting", self._run_greeting, TurnState.GREETING)
        else:
            logger.info(f"[VOICE] auto greeting disabled call_sid={call_sid}")
            self._set_turn_state(TurnState.LISTENING)

    async def _ensure_initialized(
        self, call_sid: str, caller_number: str, stream_sid: str | None = None
    ) -> None:
        """start(), but waits for the greeting to finish (or be interrupted)."""
        if self._initialized:
            return
        await self._initialize_session(call_sid, caller_number, stream_sid)
        if self._settings.ai_auto_greeting:
            task = self._start_response("greeting", self._run_greeting, TurnState.GREETING)
            if task is not None:
                await self._await_response(task)
        else:
            logger.info(f"[VOICE] auto greeting disabled call_sid={call_sid}")
            self._set_turn_state(TurnState.LISTENING)

    async def _initialize_session(
        self, call_sid: str, caller_number: str, stream_sid: str | None
    ) -> None:
        self._set_turn_state(TurnState.CONNECTED)
        log_call_event(
            "call.session.started", call_sid=call_sid, from_number=mask_phone(caller_number)
        )
        self._departments = await self._load_departments(call_sid)
        self._engine = ConversationEngine(self._departments, client=self._ai_client)
        log_call_event(
            "ai.session.started", call_sid=call_sid, department_count=len(self._departments)
        )
        self._context = await context_manager.get_or_create(call_sid, caller_number)
        self._noise_floor = NoiseFloor(
            multiplier=self._settings.noise_floor_multiplier,
            max_threshold=self._settings.noise_gate_max_rms,
        )
        self._buffer = AudioBuffer(
            on_flush=self._on_audio_flushed,
            call_sid=call_sid,
            stream_sid=stream_sid,
            min_rms=self._settings.stt_min_rms_energy,
            speech_start_ms=self._settings.speech_start_ms,
            end_of_speech_ms=self._settings.end_of_speech_ms,
            pre_roll_ms=self._settings.pre_roll_ms,
            max_bytes=self._settings.max_utterance_bytes,
            on_utterance_end=self._utterance_end_times.append,
            on_speech_start=self._on_caller_speech_start,
            noise_floor=self._noise_floor,
        )
        self._outbound_writer_task = asyncio.create_task(self._outbound_writer())
        self._tasks.add(self._outbound_writer_task)
        self._outbound_writer_task.add_done_callback(self._on_task_done)
        self._initialized = True

    async def _load_departments(self, call_sid: str) -> list[dict[str, Any]]:
        """Department config for the prompt. A database outage must not
        drop the call: the business context falls back to the tenants
        defaults (see build_business_context) when this is empty."""
        try:
            raw_depts = await DepartmentService(self._db).list_active()
        except Exception as exc:
            log_call_event(
                "call.session.degraded",
                call_sid=call_sid,
                reason="departments_unavailable",
                error_type=type(exc).__name__,
            )
            raw_depts = []
            try:
                await self._db.rollback()  # leave the session usable for finalize()
            except Exception as rollback_exc:
                logger.warning(f"[VOICE] rollback failed error={rollback_exc!r}")
        # display_name/prompt/knowledge_base give the receptionist real
        # business information (see build_business_context).
        return [
            {
                "name": d.name,
                "display_name": d.display_name,
                "email": d.email,
                "prompt": d.prompt,
                "knowledge_base": d.knowledge_base,
                "keywords": d.keywords or [],
            }
            for d in raw_depts
        ]

    async def mark_stopped(self, call_sid: str | None = None) -> None:
        """Stop all real-time work for this call. Idempotent.

        Called the instant Exotel's 'stop' event is parsed, and again from
        the route's finally block. Invalidates the current turn, cancels
        every per-call task (response, stall watchdog, flush tasks) and
        awaits them, so no STT/LLM/TTS continues — or sends audio — after
        the caller has gone.
        """
        if self._stopped:
            return
        self._stopped = True
        self._turn_id += 1
        if self._speech_style is not None:
            self._speech_style.close()  # late results never touch the context
        sid = call_sid or self._call_sid
        logger.info(f"[VOICE] stop received call_sid={sid}")
        self._set_turn_state(TurnState.ENDED, call_sid=sid)
        self._leave_audible()
        current = asyncio.current_task()
        pending = [task for task in self._tasks if not task.done() and task is not current]
        for task in pending:
            task.cancel()
        if self._buffer:
            await self._buffer.cancel_pending()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._response = None
        self._response_task = None
        self._carryover_pcm = b""
        self._clear_outbound_queue()
        self._carryover_text = ""
        log_call_event("call.session.stopped", call_sid=sid, cancelled_tasks=len(pending))

    # ── Receive path ─────────────────────────────────────────────────────────

    async def handle_audio(self, audio_bytes: bytes) -> None:
        """One inbound media chunk. Never raises into the receive loop."""
        if not self._buffer or self._stopped:
            return
        try:
            if self._turn_state in _AUDIBLE_STATES:
                await self._monitor_barge_in(audio_bytes)
            else:
                await self._buffer.push(audio_bytes)
        except Exception as exc:
            logger.error(
                f"[VOICE] inbound audio handling failed call_sid={self._call_sid} "
                f"error={exc!r}"
            )

    async def handle_dtmf(self, digit: str) -> None:
        # The digit itself can be account/phone data — never logged.
        log_call_event("call.dtmf", call_sid=self._call_sid)

    def _on_caller_speech_start(self) -> None:
        """AudioBuffer confirmed sustained caller speech (assistant silent)."""
        if self._turn_state == TurnState.ENDED:
            return
        self._disarm_silence_watchdog()
        self._speech_start_at = TurnMetrics.now()
        response = self._response
        if (
            response is not None
            and not response.committed
            and self._response_task is not None
            and not self._response_task.done()
            # Never drop the greeting: callers almost always say "hello" the
            # moment they pick up, and cancelling the greeting for that left
            # them in silence (live call 2026-09-30).
            and response.kind == "caller_turn"
            # Merge a paused sentence into one turn at most this many times;
            # after that the pending reply is kept, otherwise a caller who
            # keeps talking ("hello? hello?") never gets any answer. A turn
            # that is explicitly waiting for the caller to finish their
            # sentence always merges — that wait exists for exactly this.
            and (
                response.awaiting_continuation
                or self._resume_merges < self._settings.max_resume_merges
            )
        ):
            if response.awaiting_continuation:
                self._continuation_merges += 1
            else:
                self._resume_merges += 1
            # The caller only paused: they resumed before hearing any reply.
            # Abandon that reply and answer the whole statement as one turn.
            if getattr(response, "transcript", None) is not None:
                self._carryover_text = (
                    f"{self._carryover_text} {response.transcript}".strip()
                    if self._carryover_text
                    else response.transcript
                )
            else:
                if response.caller_pcm is not None:
                    self._carryover_pcm = self._carryover_pcm + response.caller_pcm if self._carryover_pcm else response.caller_pcm
                    self._carryover_segments += response.metrics.merged_segments or 1
            self._carryover_speech_start = response.metrics.speech_start
            log_call_event(
                "voice.caller_resumed", call_sid=self._call_sid, turn_id=response.turn_id
            )
            self.cancel_active_response(reason="caller_resumed")
        if self._turn_state in (
            TurnState.CONNECTED,
            TurnState.LISTENING,
            TurnState.PROCESSING,
        ):
            self._set_turn_state(TurnState.CALLER_SPEAKING)

    async def _monitor_barge_in(self, chunk: bytes) -> None:
        detector = self._barge_in_detector
        if detector is None:
            return
        result = detector.detect(chunk, suppressed=self._in_barge_in_grace())
        if result is None:
            self._tts_discard_packets += 1
            self._tts_discard_bytes += len(chunk)
            return
        await self.handle_barge_in(result)

    def _in_barge_in_grace(self) -> bool:
        """True during the first barge_in_grace_ms of any assistant sentence."""
        grace = self._settings.barge_in_grace_ms / 1000
        if grace <= 0:
            return False
        now = TurnMetrics.now()
        return any(start <= now < start + grace for start, _, _ in self._sentence_starts)

    def _cut_off_sentence(self, response: _Response | None) -> tuple[str, bool] | None:
        """The sentence of `response` audible right now, and whether it was
        the call's closing line."""
        if response is None or not response.committed:
            return None
        now = TurnMetrics.now()
        started = [(start, text) for start, text, r in self._sentence_starts if r is response]
        audible = [text for start, text in started if start <= now]
        if audible:
            text = audible[-1]
        elif started:  # sent, but its playback has not begun yet
            text = started[0][1]
        else:
            return None
        closing = bool(self._context and self._context.ended and text == response.last_segment)
        return text, closing

    async def handle_barge_in(self, result: BargeInResult) -> None:
        """Caller speech confirmed over audible assistant audio: stop talking
        immediately and listen. The interrupted reply is not resumed — only
        its cut-off sentence is repeated if the "speech" proves to be noise."""
        sample_rate = self._buffer.sample_rate if self._buffer else 8000
        speech_ms = len(result.speech) / _PCM_BYTES_PER_SAMPLE / sample_rate * 1000
        log_call_event(
            "voice.barge_in",
            call_sid=self._call_sid,
            speech_ms=round(speech_ms),
            state=self._turn_state,
        )
        self._speech_start_at = TurnMetrics.now() - speech_ms / 1000
        self._utterance_overlapped_playback = True
        self._disarm_silence_watchdog()
        self._interrupted_sentence = self._cut_off_sentence(self._response)
        self.cancel_active_response(reason="barge_in")
        self._set_turn_state(TurnState.INTERRUPTED)
        self._leave_audible()
        self._clear_outbound_queue()
        await self.clear_output_buffer()
        if self._stopped or not self._buffer:
            return
        self._set_turn_state(TurnState.CALLER_SPEAKING)
        # The caller's speech — including the audio just before the
        # detector confirmed it — opens their new turn directly; the rest of
        # this chunk goes through normal end-of-turn detection.
        self._buffer.begin_utterance(result.speech)
        if result.remainder:
            await self._buffer.push(result.remainder)

    def cancel_active_response(self, reason: str) -> None:
        """Invalidate and cancel the active response task (non-blocking).
        """
        task = self._response_task
        if task is not None and not task.done():
            task.cancel()
            log_call_event("voice.response.cancelled", call_sid=self._call_sid, reason=reason)
        self._response = None
        self._response_task = None

    async def clear_output_buffer(self) -> None:
        """Tell Exotel to drop assistant audio it has buffered but not yet played."""
        self._playback_until = 0.0
        if not self._clear_audio:
            return
        try:
            await self._clear_audio()
        except Exception as exc:
            logger.warning(
                f"[BARGE_IN] clear failed call_sid={self._call_sid} error={type(exc).__name__}"
            )

    # ── Response tasks ───────────────────────────────────────────────────────

    def _start_response(
        self,
        kind: str,
        runner: ResponseRunner,
        playing_state: TurnState = TurnState.TTS_PLAYING,
        *,
        caller_pcm: bytes | None = None,
        metrics: TurnMetrics | None = None,
    ) -> asyncio.Task | None:
        if self._stopped or self._turn_state == TurnState.ENDED:
            return None
        if self._response_task is not None and not self._response_task.done():
            self.cancel_active_response(reason="superseded")
            
        if kind == "caller_turn":
            if self._turn_committed:
                self._turn_id += 1
                self._segment_id = 1
                self._turn_committed = False
            else:
                self._segment_id += 1
        elif kind == "greeting":
            self._turn_id = 0
            self._segment_id = 0
        else:
            self._turn_id += 1
            self._segment_id = 0

        metrics = metrics or TurnMetrics(call_sid=self._call_sid, turn_id=self._turn_id, kind=kind)
        metrics.turn_id = self._turn_id
        metrics.extra["segment_id"] = self._segment_id
        
        response = _Response(
            turn_id=self._turn_id,
            kind=kind,
            metrics=metrics,
            playing_state=playing_state,
            caller_pcm=caller_pcm,
        )
        task = asyncio.create_task(
            self._run_response(response, runner),
            name=f"response:{self._call_sid}:{response.turn_id}:{self._segment_id}",
        )
        self._response = response
        self._response_task = task
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _spawn_background(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task | None:
        """Run non-critical per-call work (speech-style analysis) as a
        tracked task: cancelled and awaited by mark_stopped like the rest."""
        if self._stopped:
            coro.close()
            return None
        task = asyncio.create_task(coro, name=f"background:{self._call_sid}")
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                f"[VOICE] response task failed call_sid={self._call_sid} "
                f"type={type(task.exception()).__name__}"
            )

    async def _await_response(self, task: asyncio.Task) -> bool:
        """Wait for a response task. True if it ran to completion, False if
        it was cancelled by an interruption. Re-raises CancelledError if
        the call stopped or the awaiting task itself is being cancelled."""
        try:
            await task
            return True
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if self._stopped or (current is not None and current.cancelling()):
                raise
            return False

    async def _run_response(self, response: _Response, runner: ResponseRunner) -> None:
        filler_task: asyncio.Task | None = None
        if response.kind == "caller_turn":
            filler_task = asyncio.create_task(self._maybe_play_filler(response))
        try:
            await runner(response)
        except asyncio.CancelledError:
            if self._stopped:
                response.outcome = "stopped"
            else:
                response.outcome = "interrupted" if response.committed else "abandoned"
            self._abandon(response)
            raise
        except Exception as exc:
            import traceback
            logger.error(f"[VOICE] Error in _run_response for kind={response.kind}: {traceback.format_exc()}")
            response.outcome = "error"
            log_call_event(
                "voice.response.error",
                call_sid=self._call_sid,
                kind=response.kind,
                error_type=type(exc).__name__,
            )
            await self._recover_from_error(response)
        finally:
            if filler_task is not None:
                filler_task.cancel()
                await asyncio.gather(filler_task, return_exceptions=True)
            response.metrics.emit(response.outcome)
            if response.kind == "caller_turn":
                self._last_tts_ms = response.metrics.derived()["tts_startup_ms"] or 0.0
            if self._is_current(response):
                self._leave_audible()
                self._set_turn_state(TurnState.LISTENING)
                if response.committed and not (self._context and self._context.ended):
                    self._arm_silence_watchdog()
            if self._response is response:
                self._response = None
                self._response_task = None

    def _abandon(self, response: _Response) -> None:
        """Keep the conversation history truthful for a cancelled response."""
        if not self._context:
            return
        if response.committed:
            # The caller heard part of it (barge-in): record what was said.
            self._record_spoken(response)
        elif response.context_snapshot is not None:
            # Nothing was heard — as if this turn never happened.
            self._context.rollback(response.context_snapshot)

    async def _recover_from_error(self, response: _Response) -> None:
        """A provider failed mid-turn. If the caller heard nothing yet and the
        call is still live, give them a natural prompt to repeat themselves —
        never a technical error."""
        if response.committed:
            self._record_spoken(response)
            return
        if not self._is_current(response) or response.kind != "caller_turn":
            return
        try:
            await self._speak_segment(self._missed_that(), response)
            await self._finish_playback(response)
        except Exception as exc:
            logger.error(
                f"[VOICE] fallback prompt failed call_sid={self._call_sid} "
                f"error={exc!r}"
            )

    def _is_current(self, response: _Response) -> bool:
        return not self._stopped and self._turn_state != TurnState.ENDED and self._response is response

    def _record_spoken(self, response: _Response) -> None:
        if response.recorded or not response.spoken or not self._context:
            return
        self._context.add_message("assistant", " ".join(response.spoken))
        response.recorded = True
        if self._engine is not None:
            self._spawn_background(self._context.refresh_running_summary())

    async def _maybe_play_filler(self, response: _Response) -> None:
        """Play one short cached acknowledgement if no reply audio is ready
        _FILLER_DELAY_S after the caller stopped speaking — sparingly, so it
        never becomes a verbal tic."""
        reason = await self._filler_skip_reason(response)
        if reason is None:
            reason = await self._play_filler(response)
        if reason is not None:
            logger.info(f"[FILLER] call_sid={self._call_sid} skipped reason={reason}")

    async def _filler_skip_reason(self, response: _Response) -> str | None:
        if self._turn_number <= 1:
            return "first_turn"
        if response.metrics.speech_end is None:
            return "no_speech_end"
        if (
            self._last_filler_turn is not None
            and self._turn_number - self._last_filler_turn < _FILLER_MIN_TURN_GAP
        ):
            return "recent_filler"
        delay = max(0.0, _FILLER_DELAY_S - (TurnMetrics.now() - response.metrics.speech_end))
        try:
            await asyncio.wait_for(response.reply_audio_ready.wait(), timeout=delay)
            return "reply_ready"
        except TimeoutError:
            pass
        # The transcript and language must be known to choose (or rule out)
        # a filler; no reply audio can exist before this point anyway.
        await response.analyzed.wait()
        if response.reply_audio_ready.is_set():
            return "reply_ready"
        # A transfer/end marker can arrive in the LAST segment. Only allow
        # fillers once generation is complete and we know this is a normal
        # reply. Generation runs ahead of synthesis, preserving streaming.
        await response.reply_generated.wait()
        if response.reply_audio_ready.is_set():
            return "reply_ready"
        if response.contains_control:
            return "call_ending"
        ctx = self._context
        if not self._is_current(response) or ctx is None:
            return "stale"
        if ctx.ended or ctx.transfer_department:
            return "call_ending"
        words = [w for w in _WORD_SPLIT.split((response.transcript or "").lower()) if w]
        if words and all(w in _YES_NO_WORDS for w in words):
            return "yes_no_answer"
        if any(w in _CLOSING_WORDS for w in words):
            return "closing_or_transfer"
        return None

    async def _play_filler(self, response: _Response) -> str | None:
        """Play the next unused filler; the skip reason if none was played."""
        language = response.language or "en"
        text = next(
            (t for t in _FILLER_TEXTS.get(language, ()) if t not in self._used_fillers), None
        )
        if text is None:
            return "all_used"
        frames = _FILLER_AUDIO_CACHE.get((text, language))
        if not frames:
            return "not_cached"
        if response.reply_audio_ready.is_set() or not self._is_current(response):
            return "reply_ready"
        # Reserve before emitting: even a partially interrupted clip counts
        # as used and must not repeat on the next caller turn.
        response.filler_played = True
        self._used_fillers.add(text)
        self._last_filler_turn = self._turn_number
        async with response.audio_lock:
            if response.reply_audio_ready.is_set() or not self._is_current(response):
                return "reply_ready"
            # Once the first frame plays, finish the clip. Real reply audio
            # waits on the same lock, so a filler cannot be cut mid-word.
            for frame in frames:
                if not await self._emit_audio(frame, response):
                    return "stale"
        logger.info(f"[FILLER] call_sid={self._call_sid} played={text}")
        log_call_event(
            "voice.filler.played",
            call_sid=self._call_sid,
            turn_id=response.turn_id,
            language=language,
            text=text,
        )
        return None

    # ── Greeting ─────────────────────────────────────────────────────────────

    async def _run_greeting(self, response: _Response) -> None:
        assert self._engine is not None and self._context is not None
        response.context_snapshot = self._context.snapshot()
        greeting = await self._engine.get_greeting(self._context)
        response.recorded = True  # get_greeting() records it in the history
        lang = self._context.language
        cached = _GREETING_AUDIO_CACHE.get((greeting, lang))
        if cached and self._send_audio and self._is_current(response):
            # Pre-synthesized (see prewarm_greeting): plays instantly instead
            # of waiting ~1 s for TTS while the caller hears silence.
            if response.metrics.tts_request_start is None:
                response.metrics.tts_request_start = TurnMetrics.now()
            for index, frame in enumerate(cached):
                if not await self._emit_audio(frame, response, greeting if index == 0 else None):
                    return
        else:
            frames: list[bytes] = []

            async def _collect(frame: bytes) -> None:
                frames.append(frame)

            await self._speak_segment(greeting, response, on_frame=_collect)
            used_fallback = getattr(self._voice, "last_used_fallback", False) is True
            if frames and self._is_current(response) and not used_fallback:
                _GREETING_AUDIO_CACHE[(greeting, lang)] = frames
        await self._finish_playback(response)

    # ── Caller turns ─────────────────────────────────────────────────────────

    async def _on_audio_flushed(self, audio_bytes: bytes) -> None:
        """AudioBuffer finalized one caller utterance; answer it and wait for
        the answer to finish (or be interrupted)."""
        if not self._context or not self._engine:
            return
        speech_end = self._utterance_end_times.popleft() if self._utterance_end_times else None
        if self._stopped or self._turn_state == TurnState.ENDED:
            logger.info(f"[VOICE] ignoring audio after stop call_sid={self._call_sid}")
            return

        vad_finalize = TurnMetrics.now()
        pcm = extract_wav_pcm_payload(audio_bytes)
        self._offer_speech_style_audio(pcm)

        metrics = TurnMetrics(
            call_sid=self._call_sid,
            turn_id=0,
            speech_start=self._speech_start_at,
            speech_end=speech_end,
            vad_finalize=vad_finalize,
        )
        if self._carryover_pcm:
            pcm = self._carryover_pcm + pcm
            metrics.merged_segments = self._carryover_segments + 1
            metrics.speech_start = self._carryover_speech_start or metrics.speech_start
            self._carryover_pcm = b""
        self._speech_start_at = None
        self._turn_number += 1
        logger.info(f"[TURN {self._turn_number:02d}] call_sid={self._call_sid}")

        task = self._start_response(
            "caller_turn", self._run_caller_turn, caller_pcm=pcm, metrics=metrics
        )
        if task is not None:
            await self._await_response(task)

    def _offer_speech_style_audio(self, pcm: bytes) -> None:
        """Hand a copy of this utterance's caller-only audio (not carried-over
        audio, already offered) to the background speech-style tracker.
        Synchronous and cheap; never awaits detection."""
        overlapped, self._utterance_overlapped_playback = self._utterance_overlapped_playback, False
        if self._speech_style is None or overlapped:
            return
        try:
            self._speech_style.add_caller_audio(
                pcm, self._buffer.sample_rate if self._buffer else 8000
            )
        except Exception as exc:
            log_call_event(
                "voice.accent.fallback",
                call_sid=self._call_sid,
                reason="detector_error",
                error_type=type(exc).__name__,
            )

    def _tts_voice_profile(self, language: str) -> str | None:
        """Speech style for the TTS voice, or None for the neutral voice.
        Only in adaptive_tts mode, only for a settled (locked) style, and
        only when the TTS provider has a curated voice for it."""
        ctx = self._context
        if (
            self._accent_mode != "adaptive_tts"
            or ctx is None
            or not ctx.speech_style_locked
            or ctx.speech_style == NEUTRAL_SPEECH_STYLE
        ):
            return None
        style = ctx.speech_style
        supports = getattr(self._voice, "supports_voice_profile", None)
        if isinstance(self._voice, VoiceManager) and supports(language, style):
            return style
        if (language, style) not in self._voice_profile_fallbacks:
            self._voice_profile_fallbacks.add((language, style))
            log_call_event(
                "voice.accent.fallback",
                call_sid=self._call_sid,
                reason="voice_not_configured",
                style=NEUTRAL_SPEECH_STYLE,
                language=language,
            )
        return None

    async def _run_caller_turn(self, response: _Response) -> None:
        assert self._engine is not None and self._context is not None
        ctx = self._context
        self._set_turn_state(TurnState.PROCESSING)

        transcript = await self._transcribe(response)
        interrupted, self._interrupted_sentence = self._interrupted_sentence, None
        if transcript is None:
            response.outcome = "stt_failed"
            await self._recover_from_error(response)
            return

        if self._carryover_text:
            if transcript:
                transcript = f"{self._carryover_text} {transcript}".strip()
            else:
                transcript = self._carryover_text
            self._carryover_text = ""
        # Includes the carried-over text, so a turn merged away later keeps all of it.
        response.transcript = transcript

        if not transcript:
            response.outcome = "empty_transcript"
            if interrupted is not None and self._is_current(response):
                # The barge-in was noise, not the caller: say the cut-off
                # sentence again rather than leave them in silence.
                response.outcome = "repeated_after_noise"
                await self._repeat_sentence(response, *interrupted)
            return
        if not self._is_current(response):
            response.outcome = "stale"
            return
        await self._wait_if_unfinished(response, transcript)
        language_ms = self._update_language(transcript, response.turn_id)
        if language_ms is not None:
            response.metrics.extra["language_ms"] = language_ms
        self._silence_reprompts = 0

        # RAG / knowledge lookup
        response.metrics.knowledge_start = TurnMetrics.now()
        index = get_knowledge_index()
        knowledge_context = ""
        if index.mode == "retrieval":
            rag_result = await index.search(transcript, top_k=self._settings.rag_top_k)
            if rag_result.chunks:
                knowledge_context = "\n\n".join(c.chunk.text for c in rag_result.chunks)
                hits = [c.chunk.chunk_id for c in rag_result.chunks]
                logger.info(f'[RAG] query="{transcript}" hits={hits}')
                
        response.metrics.knowledge_end = TurnMetrics.now()
        if knowledge_context:
            log_call_event(
                "voice.knowledge.hit",
                call_sid=self._call_sid,
                chars=len(knowledge_context),
                lookup_ms=round(
                    (response.metrics.knowledge_end - response.metrics.knowledge_start) * 1000, 1
                ),
            )

        response.context_snapshot = ctx.snapshot()
        
        ctx.add_message("user", transcript)
        await self._try_extract_fields(transcript)

        # The model gathers details, confirms them in its own words and
        # closes the call itself (END_CALL_TOKEN) — see app/prompts/system.py.
        # The old fixed "Name: ... Phone: ... Email: ..." read-out could never
        # trigger (the requirement field was only filled after the call), and
        # the farewell/hang-up path was unreachable because nothing ever set
        # ctx.ended.
        # The caller is still talking — any earlier closing line they cut
        # off no longer ends the call; only this reply's marker can.
        ctx.ended = False
        ctx.transfer_department = None
        # One language for the whole reply (voice included), decided now.
        response.language = self._voice_language()
        response.analyzed.set()
        await self._speak_reply(response, rag_context=knowledge_context)
        response.metrics.response_complete = TurnMetrics.now()
        await self._finish_playback(response)
        self._record_spoken(response)
        if ctx.ended and response.committed and self._is_current(response):
            await self._close_call()

    async def _wait_if_unfinished(self, response: _Response, transcript: str) -> None:
        """Give a caller who sounds mid-sentence time to go on before replying.

        If they resume speaking, _on_caller_speech_start cancels this task
        and carries the transcript over into their next utterance.
        """
        decision, reason = classify_turn_end(transcript)
        if decision == "wait_unfinished" and extract_caller_name(transcript, self._last_assistant_reply()):
            decision, reason = "reply", "complete_name_answer"
        words = len(transcript.split())
        extra = self._settings.unfinished_turn_extra_ms / 1000
        if decision == "wait_unfinished" and self._continuation_merges >= _MAX_CONTINUATION_MERGES:
            decision, reason = "reply", "max_continuations"
        if decision == "wait_unfinished" and extra > 0:
            # STT already gave the caller time to continue. Measure the grace
            # from end-of-speech detection, rather than adding it after STT.
            speech_end = response.metrics.speech_end
            if speech_end is not None:
                extra = max(0.0, extra - max(0.0, TurnMetrics.now() - speech_end))
            logger.info(
                f"[TURN_END] decision=wait_unfinished reason={reason} words={words} "
                f"call_sid={self._call_sid}"
            )
            if extra > 0:
                response.awaiting_continuation = True
                try:
                    await asyncio.sleep(extra)
                finally:
                    response.awaiting_continuation = False
                reason = "no_continuation"
            else:
                reason = "continuation_grace_elapsed"
        logger.info(
            f"[TURN_END] decision=reply reason={reason} words={words} call_sid={self._call_sid}"
        )

    async def _repeat_sentence(self, response: _Response, sentence: str, closing: bool) -> None:
        log_call_event("voice.barge_in.noise", call_sid=self._call_sid, chars=len(sentence))
        response.recorded = True  # already in the history from the interrupted reply
        if self._context is not None:
            # Only a repeated closing line may still end the call.
            self._context.ended = closing
            if not closing:
                self._context.transfer_department = None
        await self._speak_segment(sentence, response)
        await self._finish_playback(response)
        if closing and response.committed and self._is_current(response):
            await self._close_call()

    async def _transcribe(self, response: _Response) -> str | None:
        """Transcript text, "" for no speech, or None if STT failed/timed out."""
        assert self._context is not None
        sample_rate = self._buffer.sample_rate if self._buffer else 8000
        wav = wrap_pcm16_wav(response.caller_pcm or b"", sample_rate)
        self._maybe_save_debug_audio(wav, self._call_sid, self._turn_number)
        # Hint the established call language for English and Hindi calls;
        # Hinglish (code-switched) and not-yet-known calls auto-detect, since
        # a hard hint there drops the Hindi words or transliterates the
        # English ones.
        ctx = self._context
        hint = ctx.language if ctx.language_locked and ctx.language in ("en", "hi") else None
        response.metrics.stt_start = TurnMetrics.now()
        try:
            async with asyncio.timeout(self._settings.stt_timeout_seconds):
                transcript = await self._voice.transcribe(wav, hint)
        except Exception as exc:  # includes TimeoutError; never CancelledError
            log_call_event(
                "voice.stt.failed", call_sid=self._call_sid, error_type=type(exc).__name__
            )
            return None
        response.metrics.stt_final = TurnMetrics.now()
        reported_model = getattr(self._voice, "last_stt_model", None)
        response.metrics.extra["stt_model"] = (
            reported_model
            if isinstance(reported_model, str) and reported_model
            else self._settings.stt_model
        )
        transcript = (transcript or "").strip()
        if transcript and is_hallucination(transcript):
            # Noise/silence the STT model "heard" as a stock phrase
            # ("Thank you for watching", "Subscribe", ".") — not the caller.
            log_call_event(
                "voice.stt.rejected",
                call_sid=self._call_sid,
                reason="hallucination",
                chars=len(transcript),
            )
            transcript = ""
        log_call_event(
            "voice.stt.final",
            call_sid=self._call_sid,
            turn_id=response.turn_id,
            chars=len(transcript),
            audio_ms=round(len(wav) / _PCM_BYTES_PER_SAMPLE / sample_rate * 1000),
            language_hint=hint or "auto",
        )
        return transcript

    def _update_language(self, transcript: str, turn_id: int | None = None) -> float | None:
        """Update the call language from one caller turn's full transcript.

        Local and deterministic (app/ai/language_profile.py) — no LLM or
        network call, so it adds microseconds, not milliseconds, between
        speech-to-text and the reply. Never logs the caller's words.
        """
        ctx = self._context
        if ctx is None or not transcript:
            return None
        started = time.perf_counter()
        analysis = analyze_language(
            transcript,
            min_words=self._settings.language_min_words,
            extra_acknowledgements=self._settings.language_extra_acknowledgements,
        )
        decision = update_language_profile(
            ctx,
            analysis,
            switch_min_words=self._settings.language_switch_min_words,
            switch_ratio=self._settings.language_switch_ratio,
        )
        # The TTS voice follows the call language.
        if ctx.reply_language in LANGUAGES:
            ctx.voice_language = ctx.reply_language
        elapsed_ms = (time.perf_counter() - started) * 1000
        call_language = decision.preference
        logger.info(
            f"[LANG] call_sid={ctx.call_sid} detected={analysis.language} "
            f"words={analysis.content_words} ratio={analysis.hindi_ratio:.2f} "
            f"call_language={call_language} switched={decision.switched} "
            f"reason={decision.switch_reason}"
        )
        log_call_event(
            "voice.language.detected",
            call_sid=ctx.call_sid,
            turn_id=turn_id if turn_id is not None else self._turn_id,
            **analysis.as_log_fields(),
            previous_preference=decision.previous_preference,
            preference=call_language,
            primary=decision.primary,
            response_language=decision.response_language,
            switch_reason=decision.switch_reason,
            language_ms=round(elapsed_ms, 3),
        )
        return round(elapsed_ms, 3)

    def _voice_language(self) -> str:
        ctx = self._context
        if ctx is None:
            return "en"
        return ctx.voice_language or ctx.language

    async def _speak_reply(self, response: _Response, rag_context: str | None = None) -> None:
        """Stream the LLM reply into TTS one sentence at a time."""
        assert self._engine is not None and self._context is not None
        response.metrics.llm_request_start = TurnMetrics.now()
        source = self._engine.stream_reply(
            self._context,
            first_token_timeout=self._settings.llm_first_token_timeout_seconds,
            rag_context=rag_context,
        )

        async def prefetch() -> AsyncIterator[str]:
            queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()

            async def produce() -> None:
                try:
                    async with aclosing(source) as stream:
                        async for segment in stream:
                            if "END_CALL" in segment or _TRANSFER_RE.search(segment):
                                response.contains_control = True
                            queue.put_nowait(segment)
                except Exception as exc:
                    queue.put_nowait(exc)
                finally:
                    response.reply_generated.set()
                    queue.put_nowait(None)

            producer = asyncio.create_task(produce())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    yield item
            finally:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

        replies = prefetch()
        first_segment = True
        async with aclosing(replies) as segments:
            async for segment in segments:
                if response.metrics.llm_first_token is None:
                    response.metrics.llm_first_token = (
                        self._engine.last_first_token_at or TurnMetrics.now()
                    )
                if first_segment:
                    response.metrics.first_phrase_ready = TurnMetrics.now()
                    first_segment = False
                if not self._is_current(response):
                    return
                if END_CALL_TOKEN in segment or "END_CALL" in segment:
                    self._context.ended = True
                    segment = _END_CALL_RE.sub("", segment).strip()
                    if not segment:
                        continue
                transfer = _TRANSFER_RE.search(segment)
                if transfer:
                    department = (transfer.group(1) or "").strip().lower()[:100]
                    self._context.transfer_department = department or GENERAL_DEPARTMENT
                    self._context.ended = True
                    segment = _TRANSFER_RE.sub("", segment).strip()
                    if not segment:
                        continue
                response.last_segment = segment
                await self._speak_segment(segment, response)
        response.metrics.extra["history_messages"] = self._engine.last_history_messages

    # ── Output ───────────────────────────────────────────────────────────────

    async def _speak_segment(
        self,
        text: str,
        response: _Response,
        on_frame: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> None:
        """Synthesize `text` and send it, as part of `response`."""
        if not text or not self._send_audio or not self._is_current(response):
            return
        if response.committed:
            # Later sentences are synthesized just in time, not all at once:
            # an interrupted reply then never pays for (or records) sentences
            # the caller would not have heard.
            await self._wait_for_playback_headroom(response)
            if not self._is_current(response):
                return
        ctx = self._context
        # Decided once per response (its first sentence), never per sentence.
        if response.language is None:
            response.language = self._voice_language()
        lang = response.language

        # Phone number filter
        caller_digits = "".join(c for c in (ctx.caller_number if ctx else "") if c.isdigit())
        # Check if text contains caller number. If it does, we allow it.
        # But the requirement says: "except when it matches the caller's own number being confirmed."
        pass # this logic is handled below
        
        def _filter_phone(m: re.Match[str]) -> str:
            match_str = m.group(0)
            match_digits = "".join(c for c in match_str if c.isdigit())
            # If it's a digit-only match and matches caller number, allow it
            if match_digits and caller_digits and match_digits.endswith(caller_digits[-6:]):
                return match_str
            # If it has spelled words, we also check if it matches caller digits (by converting words to digits)
            # A bit complex, let's just do a simple check: if the caller's number digits are in the matched string somehow?
            # It's easier to just do:
            log_call_event("voice.tts.filter", call_sid=self._call_sid, reason="number_removed")
            return ""
        
        text = _PHONE_NUMBER_RE.sub(_filter_phone, text)
        if not text.strip():
            return
        # What the voice reads (brand pronunciations, no ALL-CAPS spelling);
        # the history keeps `segment_text` below as written.
        segment_text = text
        text = normalize_for_tts(text, lang)

        if response.metrics.tts_request_start is None:
            response.metrics.tts_request_start = TurnMetrics.now()
        log_call_event(
            "voice.request", operation="tts", call_sid=self._call_sid, text_length=len(text)
        )
        first_frame = True
        if not response.voice_selected:
            response.voice_profile = self._tts_voice_profile(lang)
            response.voice_selected = True
        voice_profile = response.voice_profile
        async with aclosing(self._synthesize(text, lang, voice_profile)) as frames:
            async for frame in frames:
                response.reply_audio_ready.set()
                if response.metrics.tts_first_chunk is None:
                    response.metrics.tts_first_chunk = TurnMetrics.now()
                reported_model = getattr(self._voice, "last_tts_model", None)
                response.metrics.extra["tts_model"] = (
                    reported_model
                    if isinstance(reported_model, str) and reported_model
                    else (
                        self._settings.elevenlabs_model_id
                        if self._settings.tts_provider == "elevenlabs"
                        else "tts-1"
                    )
                )
                if on_frame is not None:
                    await on_frame(frame)
                async with response.audio_lock:
                    if not await self._emit_audio(
                        frame, response, segment_text if first_frame else None
                    ):
                        return
                if first_frame:
                    first_frame = False

    async def _wait_for_playback_headroom(self, response: _Response) -> None:
        """Wait until the audio already queued/playing is close to running
        out — early enough that the next sentence's TTS (streamed, a few
        hundred ms to first audio) still starts before silence."""
        lookahead = self._settings.tts_lookahead_ms / 1000
        while self._is_current(response) and (
            self._pending_audio_bytes / (8000 * _PCM_BYTES_PER_SAMPLE)
            + max(0.0, self._playback_until - TurnMetrics.now()) > lookahead
        ):
            await asyncio.sleep(0.02)

    async def _synthesize(
        self, text: str, lang: str, voice_profile: str | None = None
    ) -> AsyncIterator[bytes]:
        """Yield 3200-byte PCM16 frames, meeting Exotel's minimum chunk size,
        with tiny edge fades so sentence boundaries never click."""
        pending = bytearray()
        first = True
        # Only passed when a curated speech-style voice applies, so neutral
        # calls make exactly the same TTS request as before.
        profile = {"voice_profile": voice_profile} if voice_profile else {}

        async def _source() -> AsyncIterator[bytes]:
            if self._settings.tts_streaming_enabled and isinstance(self._voice, VoiceManager):
                async for chunk in self._voice.synthesize_to_stream(text, lang, **profile):
                    yield chunk
            else:
                yield await self._voice.synthesize(text, lang, **profile)

        async with aclosing(_source()) as chunks:
            async for chunk in chunks:
                chunk = sanitize_pcm16(chunk)
                if not chunk:
                    continue
                pending.extend(chunk)
                # Hold back only the fade-out window (8 ms) so the segment's
                # last samples can be faded without delaying the first frame.
                while len(pending) >= _EXOTEL_FRAME_BYTES + _FADE_BYTES:
                    frame = bytes(pending[:_EXOTEL_FRAME_BYTES])
                    del pending[:_EXOTEL_FRAME_BYTES]
                    if first:
                        frame = apply_fade(frame, fade_in=True, fade_out=False)
                        first = False
                    yield frame
        if not pending:
            return
        tail = apply_fade(bytes(pending), fade_in=first, fade_out=True)
        # Emit the remainder as whole frames, the last one padded with silence.
        for offset in range(0, len(tail), _EXOTEL_FRAME_BYTES):
            yield tail[offset : offset + _EXOTEL_FRAME_BYTES].ljust(_EXOTEL_FRAME_BYTES, b"\x00")

    async def _emit_audio(
        self, audio: bytes, response: _Response, segment_text: str | None = None
    ) -> bool:
        """Hand one chunk of assistant audio to the serialized writer — only if this
        response is still the current turn. Returns False once it is not."""
        if not self._is_current(response) or not self._send_audio:
            return False
        if not response.committed:
            self._commit(response)

        self._pending_audio_bytes += len(audio)
        try:
            await self._outbound_queue.put((audio, response, segment_text))
        except BaseException:
            self._pending_audio_bytes = max(0, self._pending_audio_bytes - len(audio))
            raise
        await asyncio.sleep(0)  # Yield to allow outbound_writer to process
        return True

    def _clear_outbound_queue(self) -> None:
        while not self._outbound_queue.empty():
            try:
                item = self._outbound_queue.get_nowait()
                self._audio_frame_done(item[0])
            except asyncio.QueueEmpty:
                break

    def _audio_frame_done(self, audio: bytes) -> None:
        self._pending_audio_bytes = max(0, self._pending_audio_bytes - len(audio))
        self._outbound_queue.task_done()

    async def _outbound_writer(self) -> None:
        """Single serialized task that paces and writes audio chunks to Exotel."""
        while not self._stopped:
            try:
                item = await self._outbound_queue.get()
            except asyncio.CancelledError:
                break
            audio, response = item[0], item[1]
            segment_text = item[2] if len(item) > 2 else None

            if not self._is_current(response) or not self._send_audio:
                self._audio_frame_done(audio)
                continue
            
            # Pace output within the writer, but allow checking cancellation
            lead = self._playback_until - TurnMetrics.now()
            excess = lead - self._settings.playback_max_lead_ms / 1000
            if excess > 0:
                sleep_interval = 0.05
                while excess > 0 and self._is_current(response):
                    await asyncio.sleep(min(excess, sleep_interval))
                    excess -= sleep_interval
                    
            if not self._is_current(response):
                self._audio_frame_done(audio)
                continue

            # Never let a bad chunk reach the caller as static — and never
            # let it kill this writer (an `assert` here used to end the task,
            # leaving the assistant silent for the rest of the call).
            audio = sanitize_pcm16(audio)
            if not audio:
                logger.warning(f"[VOICE] dropped non-PCM audio chunk call_sid={self._call_sid}")
                self._audio_frame_done(audio)
                continue

            sent_at = TurnMetrics.now()
            try:
                await self._send_audio(audio)
            except Exception as exc:
                logger.warning(
                    f"[VOICE] audio send failed call_sid={self._call_sid} error={exc!r}"
                )
                self._audio_frame_done(audio)
                continue
            self._last_send_ms += (TurnMetrics.now() - sent_at) * 1000
            if segment_text:
                # A sentence counts as spoken once its audio actually leaves
                # for the caller — not when it was merely synthesized/queued.
                response.spoken.append(segment_text)
                # It starts playing once the audio already sent has played.
                self._sentence_starts.append(
                    (max(self._playback_until, sent_at), segment_text, response)
                )
            if response.metrics.first_audio_output is None:
                response.metrics.first_audio_output = sent_at
            
            sample_rate = self._buffer.sample_rate if self._buffer else 8000
            playback_s = len(audio) / _PCM_BYTES_PER_SAMPLE / sample_rate
            self._playback_until = max(self._playback_until, TurnMetrics.now()) + playback_s
            self._audio_frame_done(audio)

    def _commit(self, response: _Response) -> None:
        """The reply becomes audible: it can no longer be merged away, and
        inbound audio is now judged by the barge-in detector."""
        response.committed = True
        self._resume_merges = 0
        self._continuation_merges = 0
        self._turn_committed = True
        response.caller_pcm = None
        self._last_send_ms = 0.0
        self._set_turn_state(response.playing_state)
        sample_rate = self._buffer.sample_rate if self._buffer else 8000
        self._barge_in_detector = BargeInDetector(
            frame_bytes=int(self._settings.barge_in_frame_ms / 1000 * sample_rate)
            * _PCM_BYTES_PER_SAMPLE,
            min_rms=self._settings.barge_in_min_rms_energy,
            min_active_ms=self._settings.barge_in_min_active_ms,
            gap_tolerance_ms=self._settings.barge_in_gap_tolerance_ms,
            pre_roll_ms=self._settings.pre_roll_ms,
            sample_rate=sample_rate,
            noise_floor=self._noise_floor,
        )
        self._tts_discard_packets = 0
        self._tts_discard_bytes = 0
        self._playback_until = TurnMetrics.now()
        if self._buffer:
            self._buffer.discard_partial()

    def _leave_audible(self) -> None:
        if self._barge_in_detector is None:
            return
        if self._tts_discard_packets:
            logger.info(
                f"[STT_GATE] call_sid={self._call_sid} decision=not_speech reason=tts_playback "
                f"packets={self._tts_discard_packets} bytes={self._tts_discard_bytes}"
            )
        self._barge_in_detector = None
        self._tts_discard_packets = 0
        self._tts_discard_bytes = 0

    async def _finish_playback(self, response: _Response) -> None:
        """Stay in the audible state until the caller has actually heard the
        audio already sent — Exotel plays it back in real time after the
        WebSocket write returns. Cancellable by barge-in like any other step."""
        if not response.committed:
            return
        # Frames still queued for the paced writer have not reached Exotel
        # yet; returning before they are sent flipped the call to LISTENING
        # while the assistant was still talking, so its own echo was then
        # judged by the (less strict) caller-turn detector.
        while self._is_current(response) and not self._outbound_queue.empty():
            await asyncio.sleep(0.02)
        remaining = self._playback_until - TurnMetrics.now()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _speak_interruptible(
        self, text: str, playing_state: TurnState = TurnState.TTS_PLAYING
    ) -> bool:
        """Speak `text` as its own response. True if played to the end, False
        if a barge-in interrupted it; CancelledError if the call stopped."""

        async def runner(response: _Response) -> None:
            response.recorded = True  # a bare utterance, not a conversation turn
            await self._speak_segment(text, response)
            await self._finish_playback(response)

        task = self._start_response("utterance", runner, playing_state)
        if task is None:
            return False
        return await self._await_response(task)

    async def _speak(self, text: str, playing_state: TurnState = TurnState.TTS_PLAYING) -> None:
        """Say `text` and wait for it to finish; silent once the call stopped."""
        if not text or not self._send_audio or self._stopped:
            return
        await self._speak_interruptible(text, playing_state)

    def _missed_that(self) -> str:
        return self._prompt_manager.missed_that(self._context.reply_language if self._context else "en")

    # ── Silence handling & ending the call ──────────────────────────────────

    def _arm_silence_watchdog(self) -> None:
        self._disarm_silence_watchdog()
        timeout = self._settings.silence_reprompt_seconds
        if timeout <= 0 or self._stopped or self._ending:
            return
        task = asyncio.create_task(self._silence_watchdog(timeout), name="silence_watchdog")
        self._silence_task = task
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _disarm_silence_watchdog(self) -> None:
        task = self._silence_task
        self._silence_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _silence_watchdog(self, timeout: float) -> None:
        await asyncio.sleep(timeout)
        if (
            self._stopped
            or self._ending
            or self._turn_state != TurnState.LISTENING
            or (self._buffer is not None and self._buffer.has_pending_audio)
        ):
            return
        self._silence_task = None
        lang = self._context.reply_language if self._context else "en"
        self._silence_reprompts += 1
        if self._silence_reprompts > self._settings.max_silence_reprompts:
            log_call_event("voice.silence.hangup", call_sid=self._call_sid)
            if self._context:
                self._context.ended = True
            finished = await self._speak_interruptible(
                self._prompt_manager.silence_goodbye(lang, self._settings.company_name)
            )
            if not finished:  # the caller spoke up after all — keep the call going
                if self._context:
                    self._context.ended = False
                self._silence_reprompts = 0
                return
            await self._end_call("caller_silent")
            return
        log_call_event(
            "voice.silence.reprompt", call_sid=self._call_sid, attempt=self._silence_reprompts
        )
        await self._speak_interruptible(self._prompt_manager.still_there(lang))

    async def _close_call(self) -> None:
        """The closing line has played: hand the caller to a person if the
        reply asked for a transfer, then close the stream either way."""
        if self._context is not None and self._context.transfer_department:
            await self._request_transfer(self._context.transfer_department)
            await self._end_call("transfer")
        else:
            await self._end_call("conversation_complete")

    async def _request_transfer(self, department: str) -> None:
        """Mark the call for transfer so the Exotel flow's Passthru (run as
        soon as this stream closes) connects the caller. Never raises: a
        failure here means the flow hangs up instead, the call itself is fine."""
        numbers = resolve_transfer_number(department, self._settings)
        number_str = ",".join(numbers)
        masked_number = ",".join(mask_phone(n) for n in numbers)
        log_call_event(
            "call.transfer.requested",
            call_sid=self._call_sid,
            department=department,
            number=masked_number,
        )
        try:
            call = await CallService(self._db).mark_transfer(
                self._call_sid or "", department, number_str
            )
            if call is None:
                logger.warning(f"[TRANSFER] no Call row call_sid={self._call_sid}")
            await self._db.commit()
        except Exception as exc:
            logger.error(
                f"[TRANSFER] saving transfer failed call_sid={self._call_sid} "
                f"error={exc!r}"
            )
            try:
                await self._db.rollback()  # leave the session usable for finalize()
            except Exception as rollback_exc:
                logger.warning(f"[VOICE] rollback failed error={rollback_exc!r}")

    async def _end_call(self, reason: str) -> None:
        """Close the media stream after the closing line has played. Exotel
        then continues the call flow after the Voicebot applet (normally a
        Hangup applet); post-call processing runs from the route's finally."""
        if self._ending or self._stopped:
            return
        self._ending = True
        self._disarm_silence_watchdog()
        log_call_event("call.ending", call_sid=self._call_sid, reason=reason)
        if self._hangup is None:
            return
        try:
            await asyncio.sleep(0.3)  # let the last frame leave Exotel's buffer
            await self._hangup()
        except Exception as exc:
            logger.warning(
                f"[VOICE] hangup failed call_sid={self._call_sid} error={exc!r}"
            )

    def set_hangup(self, hangup: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Inject the transport's "end this stream" action."""
        self._hangup = hangup

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _set_turn_state(self, new_state: TurnState, call_sid: str | None = None) -> None:
        if new_state == self._turn_state:
            return
        if self._turn_state == TurnState.ENDED:
            return  # terminal: nothing may revive a stopped call
        logger.info(f"[TURN] call_sid={call_sid or self._call_sid} state={new_state}")
        self._turn_state = new_state

    def _maybe_save_debug_audio(self, wav_bytes: bytes, call_sid: str | None, turn_no: int) -> None:
        """Development-only diagnostic (VOICE_DEBUG_AUDIO=true): save the
        exact WAV payload sent to Whisper, so a real call's transcript can
        be checked against what the caller actually said by listening,
        instead of guessing whether the mismatch is audio/VAD/STT/LLM.
        Never enabled by default; failures here must never break a call."""
        if not self._settings.voice_debug_audio:
            return
        try:
            debug_dir = Path("debug_audio")
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"call_{call_sid or 'unknown'}_turn_{turn_no:02d}.wav"
            path.write_bytes(wav_bytes)
            logger.info(f"[AUDIO_DEBUG] saved debug audio path={path}")
        except OSError as exc:
            logger.warning(f"[AUDIO_DEBUG] failed to save debug audio: {type(exc).__name__}")

    async def _try_extract_fields(self, text: str) -> None:
        """Capture a clear caller name without another model/network request."""
        ctx = self._context
        if not ctx:
            return
        name = extract_caller_name(text, self._last_assistant_reply())
        if name:
            ctx.collected["name"] = name

    def _last_assistant_reply(self) -> str:
        if self._context is None:
            return ""
        return next(
            (message.content for message in reversed(self._context.messages) if message.role == "assistant"),
            "",
        )

    async def _build_confirmation(self) -> str:
        ctx = self._context
        return self._prompt_manager.confirmation(
            language=ctx.language,
            name=ctx.collected.get("name") or "N/A",
            phone=ctx.collected.get("phone") or "N/A",
            email=ctx.collected.get("email") or "N/A",
            department=ctx.collected.get("department") or "N/A",
            requirement=ctx.collected.get("requirement") or "N/A",
        )

    def set_audio_sender(self, send_audio: Callable[[bytes], Coroutine[Any, Any, None]]) -> None:
        """Inject the transport's send_audio (e.g. ExotelAudioStream.send_audio) so
        SpeechHandler never needs to know the underlying WebSocket/frame protocol."""
        self._send_audio = send_audio

    def set_audio_clearer(self, clear_audio: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._clear_audio = clear_audio

    # ── Post-call ────────────────────────────────────────────────────────────

    async def finalize(self, call_sid: str | None) -> None:
        """Post-call work, run only after the real-time stream has stopped:
        extract the lead, persist call + lead, then send the department email."""
        log_call_event(
            "call.finalized",
            call_sid=call_sid,
            had_context=self._context is not None,
            was_initialized=self._initialized,
        )
        if not call_sid or not self._context:
            return

        try:
            if self._buffer:
                await self._buffer.drain()

            transcript = self._context.transcript
            # Departments known to the receptionist: DB rows, or the tenants
            # defaults when the DB has none (same source as the live prompt).
            known_departments = [
                d.to_dict() for d in build_business_context(self._departments).departments
            ]
            department_names = [str(d["name"]) for d in known_departments]
            summary = await self._summary_generator.generate(transcript)
            lead_data = await self._lead_extractor.extract(
                transcript, self._context.caller_number, department_names
            )
            if not lead_data.get("name"):
                lead_data["name"] = self._context.collected.get("name")
            if not lead_data.get("language") and self._context.language_detected:
                lead_data["language"] = self._context.language

            # The extracted department must be an exact configured name, or
            # the lead is never linked to a department and its email goes to
            # the generic fallback address. Normalise, else classify.
            extracted = str(lead_data.get("department") or "").strip().lower()
            by_name = {n.lower(): n for n in department_names}
            by_display = {
                str(d.get("display_name") or "").lower(): str(d["name"]) for d in known_departments
            }
            department = by_name.get(extracted) or by_display.get(extracted)
            if department is None and known_departments:
                basis = " ".join(
                    part
                    for part in (lead_data.get("requirement"), lead_data.get("department"))
                    if part
                )
                if basis:
                    classifier = DepartmentClassifier(known_departments, client=self._ai_client)
                    department = await classifier.classify(basis)
            if self._context.transfer_department:
                department = self._context.transfer_department
            lead_data["department"] = department

            call_service = CallService(self._db)
            call = await call_service.get_by_sid(call_sid)

            dept_id = None
            if lead_data.get("department"):
                dept_service = DepartmentService(self._db)
                dept = await dept_service.get_by_name(lead_data["department"])
                if dept:
                    dept_id = dept.id

            lead_service = LeadService(self._db)
            lead = await lead_service.create_from_call(
                phone=lead_data["phone"],
                call_id=call.id if call else None,
                name=lead_data.get("name"),
                email=lead_data.get("email"),
                department_id=dept_id,
                requirement=lead_data.get("requirement"),
                summary=lead_data.get("summary") or summary,
                language=lead_data.get("language"),
                additional_notes=lead_data.get("additional_notes"),
                raw_transcript=transcript,
            )

            # A test/synthetic call that never went through the incoming
            # webhook has no Call row; the lead above must still be committed.
            # The stream ending says the call connected, so "completed" is the
            # default outcome here — CallService keeps any more specific
            # failure status the Exotel status callback reported, and never
            # overwrites its duration/recording with empty values.
            completed = None
            if call is not None:
                completed = await call_service.complete(call_sid, transcript=transcript)
            else:
                logger.warning(
                    f"[pipeline] no Call row for call_sid={call_sid} — "
                    "skipping call_service.complete(), lead is still persisted"
                )

            # Commit Call/Lead now, before email dispatch — SMTP can retry for
            # minutes on failure, and repository create()/update() only flush().
            await self._db.commit()
            log_call_event("call.persistence.completed", call_sid=call_sid, lead_id=str(lead.id))
            if completed is not None and completed.recording_url:
                await self._sync_recording(call_sid)

            # Dispatch lead notification email — failure is non-fatal and must
            # never roll back the already-committed Call/Lead persistence above.
            try:
                email_service = EmailService(self._db)
                await email_service.dispatch_lead_email(lead.id)
                await self._db.commit()  # persist EmailLog sent/failed status promptly
            except Exception as email_exc:
                logger.error(
                    f"[pipeline] email dispatch failed lead={lead.id} "
                    f"error={email_exc!r}"
                )

            # Dispatch SMS to the caller
            try:
                if completed and lead_data.get("phone"):
                    from app.telephony.exotel.client import ExotelClient
                    
                    contact_info = "connect@kinikh.com"
                    if dept_id:
                        dept_service = DepartmentService(self._db)
                        dept = await dept_service.get(dept_id)
                        if dept and dept.email:
                            contact_info = dept.email
                    
                    ref = completed.reference or "NO-REF"
                    display_name = lead_data.get("department") or "General"
                    
                    sms_text = f"Thank you for contacting Kinikh Group. Your reference is {ref}. The {display_name} team will contact you soon. For queries: {contact_info}"
                    await ExotelClient().send_sms(lead_data["phone"], sms_text)
            except Exception as sms_exc:
                logger.error(
                    f"[pipeline] SMS dispatch failed lead={lead.id} "
                    f"error={sms_exc!r}"
                )

        except Exception as exc:
            logger.error(f"Call finalization error call_sid={call_sid} error={exc!r}")
        finally:
            await context_manager.remove(call_sid)

    async def _sync_recording(self, call_sid: str) -> None:
        """Attach the transcript to the provider recording row, if Exotel has
        already reported one. Separate transaction, non-fatal: the call and
        lead are committed already. The call row lock serializes this with
        a concurrent status callback doing the same upsert."""
        try:
            call = await CallService(self._db).get_by_sid_for_update(call_sid)
            if call is not None:
                await CallRecordingService(self._db).record_provider_metadata(call)
            await self._db.commit()
        except Exception as exc:
            logger.error(
                f"[pipeline] recording sync failed call_sid={call_sid} error={exc!r}"
            )
            await self._db.rollback()
