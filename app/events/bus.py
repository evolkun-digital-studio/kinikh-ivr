"""Async in-process event bus."""

from __future__ import annotations

from loguru import logger

from app.events.base import BaseEvent
from app.events.subscriber import EventSubscriber


class EventBus:
    """Async publish/subscribe bus.

    Subscriber failures are caught and logged — they never crash the publisher.
    Delivery is sequential (no concurrency between subscribers).
    """

    def __init__(self) -> None:
        self._subscribers: dict[type[BaseEvent], list[EventSubscriber]] = {}

    def subscribe(self, event_type: type[BaseEvent], subscriber: EventSubscriber) -> None:
        """Register subscriber for a specific event type."""
        self._subscribers.setdefault(event_type, []).append(subscriber)

    def unsubscribe(self, event_type: type[BaseEvent], subscriber: EventSubscriber) -> None:
        """Remove a subscriber. No-op if not registered."""
        subs = self._subscribers.get(event_type, [])
        try:
            subs.remove(subscriber)
        except ValueError:
            pass

    async def publish(self, event: BaseEvent) -> None:
        """Deliver event to all subscribers of its type."""
        for sub in self._subscribers.get(type(event), []):
            try:
                await sub.handle(event)
            except Exception as exc:
                logger.error(
                    f"[event_bus] subscriber={type(sub).__name__}"
                    f" event={type(event).__name__} error={exc}"
                )

    def subscriber_count(self, event_type: type[BaseEvent]) -> int:
        """Return number of registered subscribers for a given event type."""
        return len(self._subscribers.get(event_type, []))
