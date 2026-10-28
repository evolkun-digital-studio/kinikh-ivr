"""Lead model — captured contact information from each call."""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.department import Department
    from app.models.email_log import EmailLog


class Lead(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "leads"

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    additional_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True, index=True
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id"), nullable=True, index=True
    )

    department: Mapped[Department | None] = relationship("Department", back_populates="leads")
    call: Mapped[Call | None] = relationship("Call", back_populates="lead", uselist=False)
    email_logs: Mapped[list[EmailLog]] = relationship("EmailLog", back_populates="lead")

    def __repr__(self) -> str:
        return f"<Lead phone={self.phone} name={self.name}>"
