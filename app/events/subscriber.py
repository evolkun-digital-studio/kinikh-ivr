"""Abstract event subscriber."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.events.base import BaseEvent


class EventSubscriber(ABC):
    """Contract for all event handlers. Exceptions are caught by EventBus."""

    @abstractmethod
    async def handle(self, event: BaseEvent) -> None:
        """Process the event."""
