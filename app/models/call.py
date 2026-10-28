"""Call model — tracks telephony call lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.lead import Lead


class Call(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "calls"

    call_sid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    caller_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    callee_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="initiated", index=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="inbound")
    reference: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="exotel")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the assistant handed the caller to a person (live transfer);
    # read by the Exotel flow's Passthru/Connect applets.
    transfer_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    transfer_department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transfer_number: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lead: Mapped[Lead | None] = relationship("Lead", back_populates="call", uselist=False)

    def __repr__(self) -> str:
        return f"<Call sid={self.call_sid} status={self.status}>"
