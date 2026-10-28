"""AnalyticsSubscriber — aggregates per-department call counts in memory."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from app.events.base import BaseEvent
from app.events.events import CallCompletedEvent, CallFailedEvent, CallStartedEvent
from app.events.subscriber import EventSubscriber


class AnalyticsSubscriber(EventSubscriber):
    """Tracks call counts and failure rates. One instance per application."""

    def __init__(self) -> None:
        self._call_count: int = 0
        self._failure_count: int = 0
        self._dept_counts: dict[str, int] = defaultdict(int)
        self._started_at: dict[str, datetime] = {}

    async def handle(self, event: BaseEvent) -> None:
        if isinstance(event, CallStartedEvent):
            self._call_count += 1
            self._started_at[event.call_sid] = datetime.now(UTC)
        elif isinstance(event, CallCompletedEvent):
            self._dept_counts[event.department or "unknown"] += 1
            self._started_at.pop(event.call_sid, None)
        elif isinstance(event, CallFailedEvent):
            self._failure_count += 1
            self._started_at.pop(event.call_sid, None)

    @property
    def total_calls(self) -> int:
        return self._call_count

    @property
    def total_failures(self) -> int:
        return self._failure_count

    def calls_by_department(self) -> dict[str, int]:
        return dict(self._dept_counts)
