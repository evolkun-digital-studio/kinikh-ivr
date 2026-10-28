"""CallCost model — per-call total cost breakdown."""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class CallCost(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "call_costs"

    call_sid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id"), nullable=True
    )
    telephony_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    openai_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    voice_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    email_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<CallCost sid={self.call_sid} total=${self.total_cost_usd:.4f}>"
