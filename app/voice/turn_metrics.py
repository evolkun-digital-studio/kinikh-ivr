"""Per-turn latency timeline for the live voice pipeline.

Each caller turn records monotonic timestamps for its stages and emits ONE
structured `voice.latency.breakdown` log line when it ends, with the derived
latencies. Stages that never happened are reported as null — never guessed —
so an interrupted turn does not look like a fast one.

`speech_end` is when end-of-speech was *detected*, i.e. after
END_OF_SPEECH_MS of trailing silence; add that setting to get latency from
the moment the caller actually stopped talking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from app.core.call_logging import log_call_event


def _ms(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start) * 1000, 1)


@dataclass
class TurnMetrics:
    call_sid: str | None
    turn_id: int
    kind: str = "caller_turn"  # or "greeting"
    # Full timeline timestamps (monotonic seconds)
    speech_start: float | None = None
    speech_end: float | None = None
    vad_finalize: float | None = None
    stt_start: float | None = None
    stt_final: float | None = None
    knowledge_start: float | None = None
    knowledge_end: float | None = None
    llm_request_start: float | None = None
    llm_first_token: float | None = None
    first_phrase_ready: float | None = None
    tts_request_start: float | None = None
    tts_first_chunk: float | None = None
    first_audio_queued: float | None = None
    first_audio_output: float | None = None
    response_complete: float | None = None
    merged_segments: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def now() -> float:
        return time.monotonic()

    def derived(self) -> dict[str, float | None]:
        return {
            "vad_ms": _ms(self.speech_end, self.vad_finalize or self.stt_start),
            "stt_ms": _ms(self.stt_start, self.stt_final),
            "knowledge_ms": _ms(self.knowledge_start, self.knowledge_end),
            "llm_ttft_ms": _ms(self.llm_request_start, self.llm_first_token),
            "phrase_buffer_ms": _ms(self.llm_first_token, self.first_phrase_ready),
            "tts_startup_ms": _ms(self.tts_request_start, self.tts_first_chunk or self.first_audio_output),
            "audio_queue_ms": _ms(self.tts_first_chunk or self.first_audio_output, self.first_audio_output),
            "end_of_turn_to_first_audio_ms": _ms(self.speech_end, self.first_audio_output),
            "complete_turn_ms": _ms(self.speech_end, self.response_complete),
            # Legacy aliases for backward compatibility with existing tests
            "end_of_turn_to_stt_final_ms": _ms(self.speech_end, self.stt_final),
            "stt_final_to_llm_first_token_ms": _ms(self.stt_final, self.llm_first_token),
            "llm_first_token_to_first_audio_ms": _ms(self.llm_first_token, self.first_audio_output),
        }

    def emit(self, outcome: str) -> None:
        d = self.derived()
        # Emit the concise structured latency breakdown log
        log_call_event(
            "voice.latency.breakdown",
            call_sid=self.call_sid,
            turn_id=self.turn_id,
            kind=self.kind,
            outcome=outcome,
            merged_segments=self.merged_segments,
            **{k: v for k, v in d.items() if v is not None},
            **self.extra,
        )
        # Also emit backward-compatible event for existing log consumers
        log_call_event(
            "voice.turn.metrics",
            call_sid=self.call_sid,
            turn_id=self.turn_id,
            kind=self.kind,
            outcome=outcome,
            merged_segments=self.merged_segments,
            **d,
            **self.extra,
        )
