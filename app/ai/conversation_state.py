"""Conversation state machine — derives call phase from CallContext data."""

from __future__ import annotations

from enum import Enum

from app.ai.context_manager import CallContext

# Ordered fields collected from each caller (email is optional but collected).
COLLECTION_FIELDS: list[str] = ["name", "requirement", "email"]


class ConversationPhase(str, Enum):
    GREETING = "greeting"
    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    FAREWELL = "farewell"
    ENDED = "ended"


class ConversationState:
    """Pure utility: derives conversation phase from CallContext state.

    All methods are static — no per-instance state. Works on any CallContext.
    """

    @staticmethod
    def current_phase(context: CallContext) -> ConversationPhase:
        """Return the current conversation phase derived from context state."""
        if context.ended:
            return ConversationPhase.ENDED
        if context.confirmed:
            return ConversationPhase.FAREWELL
        if all(context.collected.get(f) for f in COLLECTION_FIELDS):
            return ConversationPhase.CONFIRMING
        if any(m.role == "user" for m in context.messages):
            return ConversationPhase.COLLECTING
        return ConversationPhase.GREETING

    @staticmethod
    def next_field(context: CallContext) -> str | None:
        """Return the next field name to collect, or None if all collected."""
        for field in COLLECTION_FIELDS:
            if not context.collected.get(field):
                return field
        return None

    @staticmethod
    def should_confirm(context: CallContext) -> bool:
        """Return True when all required fields are filled but not yet confirmed."""
        return (
            all(context.collected.get(f) for f in COLLECTION_FIELDS)
            and not context.confirmed
            and not context.ended
        )

    @staticmethod
    def is_terminal(context: CallContext) -> bool:
        """Return True when the conversation has ended."""
        return context.ended
