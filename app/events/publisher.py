"""EventPublisher mixin — gives any class a publish() backed by an EventBus."""

from __future__ import annotations

from app.events.base import BaseEvent
from app.events.bus import EventBus


class EventPublisher:
    """Mixin that provides publish() via an injected or default EventBus."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus or EventBus()

    async def publish(self, event: BaseEvent) -> None:
        await self._event_bus.publish(event)
