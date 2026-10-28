"""CallRecording model — persists full metadata for each completed call."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.lead import Lead


class CallRecording(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "call_recordings"

    call_sid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    caller_number: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="recording", index=True)
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)

    lead: Mapped[Lead | None] = relationship("Lead")

    def __repr__(self) -> str:
        return f"<CallRecording sid={self.call_sid} status={self.status}>"
