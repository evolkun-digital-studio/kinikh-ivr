"""Call lifecycle finite state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from loguru import logger


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CallPhase(str, Enum):
    INITIALIZING = "initializing"
    CONNECTED = "connected"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    GENERATING_RESPONSE = "generating_response"
    SPEAKING = "speaking"
    COLLECTING_INFORMATION = "collecting_information"
    LEAD_EXTRACTION = "lead_extraction"
    DATABASE_SAVE = "database_save"
    EMAIL_DISPATCH = "email_dispatch"
    COMPLETED = "completed"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


_TRANSITIONS: dict[CallPhase, frozenset[CallPhase]] = {
    CallPhase.INITIALIZING: frozenset(
        {CallPhase.CONNECTED, CallPhase.FAILED, CallPhase.DISCONNECTED}
    ),
    CallPhase.CONNECTED: frozenset({CallPhase.LISTENING, CallPhase.FAILED, CallPhase.DISCONNECTED}),
    CallPhase.LISTENING: frozenset(
        {
            CallPhase.TRANSCRIBING,
            CallPhase.LEAD_EXTRACTION,
            CallPhase.FAILED,
            CallPhase.DISCONNECTED,
        }
    ),
    CallPhase.TRANSCRIBING: frozenset(
        {CallPhase.PROCESSING, CallPhase.LISTENING, CallPhase.FAILED}
    ),
    CallPhase.PROCESSING: frozenset(
        {
            CallPhase.GENERATING_RESPONSE,
            CallPhase.COLLECTING_INFORMATION,
            CallPhase.LEAD_EXTRACTION,
            CallPhase.FAILED,
        }
    ),
    CallPhase.GENERATING_RESPONSE: frozenset({CallPhase.SPEAKING, CallPhase.FAILED}),
    CallPhase.SPEAKING: frozenset(
        {
            CallPhase.LISTENING,
            CallPhase.COLLECTING_INFORMATION,
            CallPhase.LEAD_EXTRACTION,
            CallPhase.FAILED,
            CallPhase.DISCONNECTED,
        }
    ),
    CallPhase.COLLECTING_INFORMATION: frozenset(
        {CallPhase.LISTENING, CallPhase.LEAD_EXTRACTION, CallPhase.FAILED}
    ),
    CallPhase.LEAD_EXTRACTION: frozenset(
        {CallPhase.DATABASE_SAVE, CallPhase.COMPLETED, CallPhase.FAILED}
    ),
    CallPhase.DATABASE_SAVE: frozenset(
        {CallPhase.EMAIL_DISPATCH, CallPhase.COMPLETED, CallPhase.FAILED}
    ),
    CallPhase.EMAIL_DISPATCH: frozenset({CallPhase.COMPLETED, CallPhase.FAILED}),
    CallPhase.COMPLETED: frozenset(),
    CallPhase.FAILED: frozenset({CallPhase.DISCONNECTED}),
    CallPhase.DISCONNECTED: frozenset(),
}

_TERMINAL_PHASES: frozenset[CallPhase] = frozenset({CallPhase.COMPLETED, CallPhase.DISCONNECTED})


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, current: CallPhase, target: CallPhase) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.value} -> {target.value}")


@dataclass
class PhaseRecord:
    phase: CallPhase
    entered_at: datetime = field(default_factory=_utcnow)


class CallStateMachine:
    """Finite state machine for a single in-progress call.

    All transitions are logged. Invalid transitions raise InvalidTransitionError.
    Use try_transition() for fire-and-forget transitions that may be invalid.
    """

    def __init__(self, call_sid: str) -> None:
        self._call_sid = call_sid
        self._phase = CallPhase.INITIALIZING
        self._history: list[PhaseRecord] = [PhaseRecord(CallPhase.INITIALIZING)]

    @property
    def phase(self) -> CallPhase:
        return self._phase

    @property
    def history(self) -> list[PhaseRecord]:
        return list(self._history)

    @property
    def is_terminal(self) -> bool:
        return self._phase in _TERMINAL_PHASES

    def can_transition(self, target: CallPhase) -> bool:
        return target in _TRANSITIONS.get(self._phase, frozenset())

    def transition(self, target: CallPhase) -> None:
        """Transition to target phase. Raises InvalidTransitionError if not allowed."""
        allowed = _TRANSITIONS.get(self._phase, frozenset())
        if target not in allowed:
            raise InvalidTransitionError(self._phase, target)
        old = self._phase
        self._phase = target
        self._history.append(PhaseRecord(target))
        logger.info(f"[call_state] sid={self._call_sid} {old.value} -> {target.value}")

    def try_transition(self, target: CallPhase) -> bool:
        """Attempt a transition. Returns True if successful, False if invalid."""
        if self.can_transition(target):
            self.transition(target)
            return True
        return False

    def force_failed(self, reason: str = "") -> None:
        """Force FAILED from any non-terminal phase."""
        if self.is_terminal:
            return
        if not self.can_transition(CallPhase.FAILED):
            return
        old = self._phase
        self._phase = CallPhase.FAILED
        self._history.append(PhaseRecord(CallPhase.FAILED))
        logger.error(
            f"[call_state] sid={self._call_sid} FORCED FAIL" f" from={old.value} reason={reason!r}"
        )

    def elapsed_in_phase(self) -> float:
        """Seconds elapsed in the current phase."""
        if not self._history:
            return 0.0
        return (datetime.now(UTC) - self._history[-1].entered_at).total_seconds()
