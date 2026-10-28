"""Stream Session model for one-time WebSocket authentication."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StreamSession(Base):
    __tablename__ = "stream_sessions"

    stream_session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    call_sid: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
