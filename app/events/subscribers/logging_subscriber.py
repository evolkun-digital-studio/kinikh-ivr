"""LoggingSubscriber — logs every event at DEBUG level."""

from __future__ import annotations

from loguru import logger

from app.events.base import BaseEvent
from app.events.subscriber import EventSubscriber


class LoggingSubscriber(EventSubscriber):
    async def handle(self, event: BaseEvent) -> None:
        logger.debug(
            f"[event] type={type(event).__name__}"
            f" id={event.event_id}"
            f" ts={event.timestamp.isoformat()}"
        )
