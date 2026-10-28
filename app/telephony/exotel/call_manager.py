"""CallManager — in-memory registry of active call sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class CallState:
    """Mutable state snapshot for a single in-progress call."""

    call_sid: str
    caller_number: str
    status: str = "initiated"
    language: str = "en"
    started_at: datetime = field(default_factory=_utcnow)
    transcripts: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class CallManager:
    """Thread-safe in-memory store for active call sessions.

    Use one shared instance per process (typically as a module-level singleton
    or FastAPI dependency). All mutation is guarded by an asyncio.Lock.
    """

    def __init__(self) -> None:
        self._calls: dict[str, CallState] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def register(
        self,
        call_sid: str,
        caller_number: str,
        language: str = "en",
    ) -> CallState:
        """Create and store a new CallState. Returns the new state."""
        async with self._lock:
            state = CallState(
                call_sid=call_sid,
                caller_number=caller_number,
                language=language,
            )
            self._calls[call_sid] = state
            return state

    async def get(self, call_sid: str) -> CallState | None:
        """Return the CallState for call_sid, or None if not found."""
        async with self._lock:
            return self._calls.get(call_sid)

    async def update_status(self, call_sid: str, status: str) -> bool:
        """Update call status. Returns True if the call exists."""
        async with self._lock:
            if call_sid in self._calls:
                self._calls[call_sid].status = status
                return True
            return False

    async def set_language(self, call_sid: str, language: str) -> bool:
        """Update the detected language for a call. Returns True if call exists."""
        async with self._lock:
            if call_sid in self._calls:
                self._calls[call_sid].language = language
                return True
            return False

    async def add_transcript(self, call_sid: str, text: str) -> None:
        """Append a transcript segment to the call's history."""
        async with self._lock:
            if call_sid in self._calls:
                self._calls[call_sid].transcripts.append(text)

    async def remove(self, call_sid: str) -> CallState | None:
        """Remove and return the CallState, or None if not found."""
        async with self._lock:
            return self._calls.pop(call_sid, None)

    async def list_active(self) -> list[CallState]:
        """Return a snapshot of all active call states."""
        async with self._lock:
            return list(self._calls.values())

    async def count(self) -> int:
        """Return the number of active calls."""
        async with self._lock:
            return len(self._calls)
