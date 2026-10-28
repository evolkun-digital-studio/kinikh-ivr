"""Call recording listing + authenticated audio playback endpoints."""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user_id, get_db
from app.core.exceptions import NotFoundError
from app.core.media import content_type_for, resolve_audio_path
from app.models.call_recording import CallRecording
from app.services.call_recording_service import CallRecordingService

router = APIRouter(prefix="/recordings", tags=["Recordings"])


class RecordingResponse(BaseModel):
    id: uuid.UUID
    call_sid: str
    caller_number: str
    department: str | None
    language: str
    start_time: str
    end_time: str | None
    duration: int | None
    status: str
    has_audio: bool

    model_config = {"from_attributes": True}

    @classmethod
    def from_recording(cls, r: CallRecording) -> RecordingResponse:
        return cls(
            id=r.id,
            call_sid=r.call_sid,
            caller_number=r.caller_number,
            department=r.department,
            language=r.language,
            start_time=r.start_time.isoformat(),
            end_time=r.end_time.isoformat() if r.end_time else None,
            duration=r.duration,
            status=r.status,
            has_audio=bool(r.audio_file_path),
        )


class RecordingDetailResponse(RecordingResponse):
    transcript: str | None
    summary: str | None
    errors: str | None


class PaginatedRecordingsResponse(BaseModel):
    items: list[RecordingResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=PaginatedRecordingsResponse)
async def list_recordings(
    status: str | None = None,
    department: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> PaginatedRecordingsResponse:
    service = CallRecordingService(db)
    items, total = await service.list_filtered(
        status=status,
        department=department,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return PaginatedRecordingsResponse(
        items=[RecordingResponse.from_recording(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{recording_id}", response_model=RecordingDetailResponse)
async def get_recording(
    recording_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> RecordingDetailResponse:
    service = CallRecordingService(db)
    recording = await service.get_or_raise(recording_id)
    base = RecordingResponse.from_recording(recording)
    return RecordingDetailResponse(
        **base.model_dump(),
        transcript=recording.transcript,
        summary=recording.summary,
        errors=recording.errors,
    )


@router.get("/{recording_id}/audio", response_class=FileResponse)
async def get_recording_audio(
    recording_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> FileResponse:
    """Stream a recording's audio file. Never reveals the underlying path —
    a missing recording, a missing/invalid file, and a rejected path
    traversal attempt all surface identically as 404."""
    service = CallRecordingService(db)
    recording = await service.get_or_raise(recording_id)

    if not recording.audio_file_path:
        raise NotFoundError("Recording audio")

    settings = get_settings()
    resolved = resolve_audio_path(settings.recordings_dir, recording.audio_file_path)
    if resolved is None:
        raise NotFoundError("Recording audio")

    return FileResponse(
        path=resolved,
        media_type=content_type_for(resolved),
        headers={"Cache-Control": "private, max-age=3600"},
    )
