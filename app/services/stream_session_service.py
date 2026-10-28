"""Stream Session Service for one-time WebSocket authentication."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.stream_session import StreamSession


class StreamSessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(self, call_sid: str) -> str:
        """Create a new stream session and return its cryptographically random ID."""
        settings = get_settings()
        
        # Use token_urlsafe for a short, secure string.
        # 32 bytes of randomness gives a 43-character string, safely within Exotel query limits.
        sid = secrets.token_urlsafe(32)
        
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.stream_token_ttl_seconds)
        
        stream_session = StreamSession(
            stream_session_id=sid,
            call_sid=call_sid,
            expires_at=expires_at,
            consumed=False,
        )
        self.session.add(stream_session)
        await self.session.commit()
        return sid

    async def consume_session(self, sid: str) -> str | None:
        """
        Look up a stream session, check its validity, and mark it as consumed atomically.
        Returns the call_sid if valid and successfully consumed, else None.
        """
        now = datetime.now(UTC)
        
        # We do an atomic update to prevent race conditions from concurrent connects
        # Returns the call_sid only if we actually flipped consumed from false to true
        stmt = (
            update(StreamSession)
            .where(
                StreamSession.stream_session_id == sid,
                StreamSession.consumed == False,
                StreamSession.expires_at > now,
            )
            .values(consumed=True)
            .returning(StreamSession.call_sid)
        )
        
        result = await self.session.execute(stmt)
        call_sid = result.scalar_one_or_none()
        
        if call_sid:
            await self.session.commit()
            return call_sid
            
        await self.session.rollback()
        return None
