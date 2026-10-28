"""CallRecordingService — manages call recording metadata lifecycle."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.call_recording import CallRecording
from app.repositories.call_recording_repository import CallRecordingRepository


class CallRecordingService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = CallRecordingRepository(db)

    async def record_provider_metadata(self, call: Call) -> CallRecording | None:
        """Record a provider-hosted recording once per call; caller audio stays remote.

        Called from both completion paths (status callback and stream
        finalizer) with the already row-locked Call, so retries and either
        arrival order converge on one row; call_recordings.call_sid is unique
        as the final guard. Only fields that carry data are written, so a
        later call can never blank out an earlier one's values.
        """
        if not call.recording_url:
            return None
        existing = await self._repo.get_by_call_sid(call.call_sid)
        values = {
            "provider_url": call.recording_url,
            "status": "completed",
            "duration": call.duration_seconds,
            "end_time": call.ended_at,
            "transcript": call.transcript or None,
        }
        if existing is not None:
            return await self._repo.update(
                existing, **{key: value for key, value in values.items() if value is not None}
            )
        return await self._repo.create(
            call_sid=call.call_sid,
            caller_number=call.caller_number,
            start_time=call.started_at or datetime.now(UTC),
            language="en",
            **values,
        )

    async def start_recording(self, call_sid: str, caller_number: str) -> CallRecording:
        recording = await self._repo.create(
            call_sid=call_sid,
            caller_number=caller_number,
            start_time=datetime.now(UTC),
            status="recording",
        )
        logger.info(f"[recording] started call_sid={call_sid}")
        return recording

    async def complete_recording(
        self,
        call_sid: str,
        *,
        transcript: str | None = None,
        summary: str | None = None,
        department: str | None = None,
        language: str | None = None,
        lead_id: Any | None = None,
    ) -> CallRecording | None:
        recording = await self._repo.get_by_call_sid(call_sid)
        if not recording:
            logger.warning(f"[recording] not found for call_sid={call_sid}")
            return None

        end_time = datetime.now(UTC)
        duration = int((end_time - recording.start_time).total_seconds())
        kwargs: dict[str, Any] = {"end_time": end_time, "duration": duration, "status": "completed"}
        if transcript is not None:
            kwargs["transcript"] = transcript
        if summary is not None:
            kwargs["summary"] = summary
        if department is not None:
            kwargs["department"] = department
        if language is not None:
            kwargs["language"] = language
        if lead_id is not None:
            kwargs["lead_id"] = lead_id

        updated = await self._repo.update(recording, **kwargs)
        logger.info(f"[recording] completed call_sid={call_sid} duration={duration}s")
        return updated

    async def mark_failed(self, call_sid: str, error: str) -> None:
        recording = await self._repo.get_by_call_sid(call_sid)
        if recording:
            end_time = datetime.now(UTC)
            duration = int((end_time - recording.start_time).total_seconds())
            await self._repo.update(
                recording,
                status="failed",
                errors=error,
                end_time=end_time,
                duration=duration,
            )

    async def get(self, call_sid: str) -> CallRecording | None:
        return await self._repo.get_by_call_sid(call_sid)

    async def get_or_raise(self, recording_id: Any) -> CallRecording:
        return await self._repo.get_or_raise(recording_id)

    async def list_filtered(
        self,
        *,
        status: str | None = None,
        department: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[CallRecording], int]:
        items = await self._repo.list_filtered(
            status=status,
            department=department,
            search=search,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_filtered(
            status=status,
            department=department,
            search=search,
            date_from=date_from,
            date_to=date_to,
        )
        return items, total
