"""Per-call conversation context — persists in memory during a call."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class CallContext:
    call_sid: str
    caller_number: str
    # Language profile — maintained by app.ai.language_profile on every
    # caller turn. `language` is the long-term session preference (persisted
    # on the call/lead); it is only a real, confirmed value once
    # `language_locked` is True — before that it is just the default.
    language: str = "en"
    language_detected: bool = False
    language_locked: bool = False
    # Set from the first meaningful caller turn (never from the greeting).
    primary_language: str = "unknown"
    # Language of the latest caller turn ("unknown" for acknowledgements).
    current_turn_language: str = "unknown"
    # Language the reply to the latest turn uses — the call language once
    # set; before that, whatever the latest turn showed.
    response_language: str = "unknown"
    language_confidence: str = "low"
    # True once the caller explicitly asked for a language.
    language_explicit: bool = False
    voice_language: str | None = None
    # Speech-style profile — SEPARATE from language: it may only influence
    # how the TTS voice sounds (app/voice/accent_detector.py), never what is
    # said, the language, RAG facts, routing or the LLM prompt. A broad
    # acoustic style, never a claim about where the caller is from.
    speech_style: str = "neutral_indian"
    speech_style_confidence: str = "low"
    # Consecutive qualifying detections agreeing on speech_style_candidate.
    speech_style_candidate: str | None = None
    speech_style_evidence_turns: int = 0
    speech_style_locked: bool = False
    messages: list[Message] = field(default_factory=list)
    # Compact, extractive memory for messages outside the live eight-message
    # window. The full transcript remains in ``messages`` for post-call work.
    running_summary: str = ""
    summarized_message_count: int = 0
    collected: dict[str, str | None] = field(
        default_factory=lambda: {
            "name": None,
            "phone": None,
            "email": None,
            "department": None,
            "requirement": None,
            "additional_notes": None,
        }
    )
    confirmed: bool = False
    ended: bool = False
    # Business area the caller is being transferred to ("general" if none
    # was named); set from the reply's [TRANSFER:...] marker.
    transfer_department: str | None = None
    transcript_parts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))
        self.transcript_parts.append(f"[{role.upper()}] {content}")

    def snapshot(self) -> tuple[int, int]:
        """Opaque marker of the history length, for rollback()."""
        return len(self.messages), len(self.transcript_parts)

    def rollback(self, snapshot: tuple[int, int]) -> None:
        """Drop messages added after `snapshot` — used when a turn is
        abandoned before the caller heard any reply (the caller kept talking
        and the audio is merged into their next turn instead)."""
        message_count, transcript_count = snapshot
        del self.messages[message_count:]
        del self.transcript_parts[transcript_count:]

    async def refresh_running_summary(self, keep_last: int = 8, max_chars: int = 700) -> None:
        """Refresh live-call memory without blocking the response path.

        This intentionally performs local, extractive compaction: it needs no
        provider call, cannot add network latency, and never changes the full
        transcript used by post-call summary and lead extraction.
        """
        await asyncio.sleep(0)
        cutoff = max(0, len(self.messages) - keep_last)
        if cutoff <= self.summarized_message_count:
            return
        older = self.messages[:cutoff]
        parts = [f"{m.role}: {' '.join(m.content.split())}" for m in older]
        compact = " | ".join(parts)
        if len(compact) > max_chars:
            compact = compact[-max_chars:].lstrip()
        self.running_summary = compact
        self.summarized_message_count = cutoff

    def live_history(self, limit: int = 8) -> list[dict[str, str]]:
        """The bounded message window sent to the live reply model."""
        conversational = [m for m in self.messages if m.role in ("user", "assistant")]
        return [{"role": m.role, "content": m.content} for m in conversational[-limit:]]

    @property
    def reply_language(self) -> str:
        """Language the agent is currently speaking: this turn's response
        language once known, else the session preference."""
        if self.response_language in ("en", "hi", "hi-en"):
            return self.response_language
        return self.language

    @property
    def transcript(self) -> str:
        return "\n".join(self.transcript_parts)

    @property
    def openai_messages(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def all_required_collected(self) -> bool:
        required = ["name", "phone", "requirement"]
        return all(self.collected.get(f) for f in required)


class ContextManager:
    """Thread-safe in-memory store for active call contexts."""

    def __init__(self) -> None:
        self._store: dict[str, CallContext] = {}
        self._lock = asyncio.Lock()

    async def create(self, call_sid: str, caller_number: str) -> CallContext:
        async with self._lock:
            ctx = CallContext(call_sid=call_sid, caller_number=caller_number)
            ctx.collected["phone"] = caller_number
            self._store[call_sid] = ctx
            return ctx

    async def get(self, call_sid: str) -> CallContext | None:
        return self._store.get(call_sid)

    async def get_or_create(self, call_sid: str, caller_number: str) -> CallContext:
        ctx = await self.get(call_sid)
        if ctx is None:
            ctx = await self.create(call_sid, caller_number)
        return ctx

    async def remove(self, call_sid: str) -> None:
        async with self._lock:
            self._store.pop(call_sid, None)

    async def count(self) -> int:
        return len(self._store)


# Module-level singleton shared across the application lifetime.
context_manager = ContextManager()
