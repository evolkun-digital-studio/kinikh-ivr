"""Call lifecycle management service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
import uuid
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.repositories.call_repository import CallRepository

# Call lifecycle states (Call.status). Active states only move forward;
# terminal states are final except that a more specific failure outcome
# always beats the stream finalizer's generic "completed" (the stream ending
# says nothing about how the telephony leg ended).
ACTIVE_STATUSES: tuple[str, ...] = ("initiated", "queued", "ringing", "in-progress")
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "no-answer", "busy", "canceled", "failed")

_ACTIVE_ORDER = {status: rank for rank, status in enumerate(ACTIVE_STATUSES)}
_TERMINAL_PRIORITY = {status: rank for rank, status in enumerate(TERMINAL_STATUSES, start=1)}
_STATUS_ALIASES = {
    "cancelled": "canceled",
    "no_answer": "no-answer",
    "noanswer": "no-answer",
    "in_progress": "in-progress",
    "inprogress": "in-progress",
}

_TRANSFER_FALLBACK: dict[str, tuple[str, str, float]] = {}  # call_sid -> (department, numbers, expiry_timestamp)

def record_transfer_fallback(call_sid: str, department: str, number: str) -> None:
    now = time.time()
    # Cleanup old entries
    expired = [sid for sid, data in _TRANSFER_FALLBACK.items() if data[2] < now]
    for sid in expired:
        del _TRANSFER_FALLBACK[sid]
    _TRANSFER_FALLBACK[call_sid] = (department, number, now + 600)

def get_transfer_fallback(call_sid: str) -> tuple[str, str] | None:
    now = time.time()
    if call_sid in _TRANSFER_FALLBACK:
        department, number, expiry = _TRANSFER_FALLBACK[call_sid]
        if expiry > now:
            return department, number
        del _TRANSFER_FALLBACK[call_sid]
    return None


def normalize_call_status(raw: str | None) -> str | None:
    """Map a provider status onto our vocabulary; None if it is not one we
    recognise (the caller rejects it rather than storing arbitrary text)."""
    if not raw:
        return None
    status = raw.strip().lower()
    status = _STATUS_ALIASES.get(status, status)
    return status if status in _ACTIVE_ORDER or status in _TERMINAL_PRIORITY else None


def is_terminal_status(status: str | None) -> bool:
    return status in _TERMINAL_PRIORITY


def _merge_status(current: str, incoming: str) -> str:
    """Deterministic, order-independent status merge.

    - terminal beats active; failure outcomes beat the generic "completed";
    - an active status never regresses (a late "ringing" after "in-progress").
    """
    if current in _TERMINAL_PRIORITY:
        if incoming not in _TERMINAL_PRIORITY:
            return current
        return max((current, incoming), key=_TERMINAL_PRIORITY.__getitem__)
    if incoming in _ACTIVE_ORDER and current in _ACTIVE_ORDER:
        return max((current, incoming), key=_ACTIVE_ORDER.__getitem__)
    return incoming


def _has_value(value: object) -> bool:
    """A field update only applies when it carries real data — None or an
    empty string must never erase what the other completion path stored."""
    return value is not None and value != ""


class CallService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = CallRepository(session)

    async def initiate(
        self, call_sid: str, caller_number: str, callee_number: str | None = None
    ) -> Call:
        """Create the call record, or return the existing one if already registered.

        Exotel (and telephony providers generally) may redeliver the incoming-call
        webhook — this makes the operation idempotent instead of raising a DB
        UniqueViolation on the second delivery.
        """
        existing = await self._repo.get_by_sid(call_sid)
        if existing is not None:
            return existing
            
        today = datetime.now(UTC).date()
        seq = await self._repo.get_daily_sequence(today) + 1
        reference = f"KIN/IVR/{today.strftime('%Y%m%d')}/{seq:04d}"
        
        return await self._repo.create(
            call_sid=call_sid,
            caller_number=caller_number,
            callee_number=callee_number,
            status="initiated",
            started_at=datetime.now(UTC),
            reference=reference,
        )

    async def get_by_sid(self, call_sid: str) -> Call | None:
        return await self._repo.get_by_sid(call_sid)

    async def get_by_sid_for_update(self, call_sid: str) -> Call | None:
        """Row-locked read; serializes with the other completion path."""
        return await self._repo.get_by_sid_for_update(call_sid)

    async def mark_transfer(self, call_sid: str, department: str, number: str) -> Call | None:
        """Record that the caller is being handed to a person. None if the
        call row does not exist."""
        call = await self._repo.get_by_sid_for_update(call_sid)
        if call is None:
            return None
        record_transfer_fallback(call_sid, department, number)
        return await self._repo.update(
            call,
            transfer_requested=True,
            transfer_department=department,
            transfer_number=number,
        )

    async def get_by_sid_or_raise(self, call_sid: str) -> Call:
        from app.core.exceptions import NotFoundError

        call = await self._repo.get_by_sid(call_sid)
        if call is None:
            raise NotFoundError("Call", call_sid)
        return call

    async def get_by_id_or_raise(self, call_id: uuid.UUID) -> Call:
        from app.core.exceptions import NotFoundError

        call = await self._repo.get_by_id_with_lead(call_id)
        if call is None:
            raise NotFoundError("Call", call_id)
        return call

    async def update_status(
        self,
        call_sid: str,
        status: str,
        error_message: str | None = None,
        recording_url: str | None = None,
    ) -> Call:
        from app.core.exceptions import NotFoundError

        call = await self._repo.get_by_sid_for_update(call_sid)
        if call is None:
            raise NotFoundError("Call", call_sid)
        updates: dict = {"status": _merge_status(call.status, status)}
        if error_message:
            updates["error_message"] = error_message
        if _has_value(recording_url):
            updates["recording_url"] = recording_url
        return await self._repo.update(call, **updates)

    async def complete(
        self,
        call_sid: str,
        status: str = "completed",
        duration_seconds: int | None = None,
        transcript: str | None = None,
        recording_url: str | None = None,
    ) -> Call:
        from app.core.exceptions import NotFoundError, ValidationError

        if not is_terminal_status(status):
            raise ValidationError("Unknown terminal call status")
        call = await self._repo.get_by_sid_for_update(call_sid)
        if call is None:
            raise NotFoundError("Call", call_sid)
        updates = {"status": _merge_status(call.status, status)}
        if call.ended_at is None:
            updates["ended_at"] = datetime.now(UTC)
        for field, value in (
            ("duration_seconds", duration_seconds),
            ("transcript", transcript),
            ("recording_url", recording_url),
        ):
            if _has_value(value):
                updates[field] = value
        return await self._repo.update(call, **updates)

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department_id: uuid.UUID | None = None,
        language: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Call], int]:
        items = await self._repo.list_filtered(
            status=status,
            department_id=department_id,
            language=language,
            search=search,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_filtered(
            status=status,
            department_id=department_id,
            language=language,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        return items, total

    async def dashboard_counts(self) -> dict[str, int | float | None]:
        since_today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await self._repo.count()
        completed = await self._repo.count_by_status("completed")
        failed = await self._repo.count_by_status("failed")
        today = await self._repo.count_created_since(since_today)
        avg_duration = await self._repo.average_duration_seconds()
        return {
            "total_calls": total,
            "completed_calls": completed,
            "failed_calls": failed,
            "today_calls": today,
            "average_duration_seconds": avg_duration,
        }

    async def calls_over_time(self, days: int = 30) -> list[tuple[date, int]]:
        since = datetime.now(UTC) - timedelta(days=days)
        return await self._repo.calls_over_time(since)

    async def counts_by_department(self) -> list[tuple[uuid.UUID | None, str | None, int]]:
        return await self._repo.counts_by_department()

    async def counts_by_language(self) -> dict[str, int]:
        return await self._repo.counts_by_language()

    async def completed_vs_failed(self) -> dict[str, int]:
        return await self._repo.completed_vs_failed()
