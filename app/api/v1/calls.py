"""Call listing and detail endpoints (protected, read-only)."""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user_id, get_db
from app.models.call import Call
from app.models.lead import Lead
from app.services.call_recording_service import CallRecordingService
from app.services.call_service import CallService
from app.services.usage_tracking_service import UsageTrackingService

router = APIRouter(prefix="/calls", tags=["Calls"])


class LeadBrief(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str
    department_id: uuid.UUID | None
    department_name: str | None = None
    language: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_lead(cls, lead: Lead) -> LeadBrief:
        return cls(
            id=lead.id,
            name=lead.name,
            phone=lead.phone,
            department_id=lead.department_id,
            department_name=lead.department.display_name if lead.department else None,
            language=lead.language,
        )


class CallResponse(BaseModel):
    id: uuid.UUID
    call_sid: str
    caller_number: str
    callee_number: str | None
    status: str
    direction: str
    duration_seconds: int | None
    provider: str
    started_at: str | None
    ended_at: str | None
    created_at: str
    transfer_requested: bool = False
    transfer_department: str | None = None
    lead: LeadBrief | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_call(cls, call: Call) -> CallResponse:
        return cls(
            id=call.id,
            call_sid=call.call_sid,
            caller_number=call.caller_number,
            callee_number=call.callee_number,
            status=call.status,
            direction=call.direction,
            duration_seconds=call.duration_seconds,
            provider=call.provider,
            started_at=call.started_at.isoformat() if call.started_at else None,
            ended_at=call.ended_at.isoformat() if call.ended_at else None,
            created_at=call.created_at.isoformat(),
            transfer_requested=bool(call.transfer_requested),
            transfer_department=call.transfer_department,
            lead=LeadBrief.from_lead(call.lead) if call.lead else None,
        )


class CostBreakdown(BaseModel):
    telephony_cost_usd: float
    openai_cost_usd: float
    voice_cost_usd: float
    email_cost_usd: float
    total_cost_usd: float
    duration_seconds: int | None


class RecordingInfo(BaseModel):
    id: uuid.UUID
    has_audio: bool
    status: str
    duration: int | None


class CallDetailResponse(CallResponse):
    transcript: str | None
    error_message: str | None
    cost: CostBreakdown | None = None
    recording: RecordingInfo | None = None


class PaginatedCallsResponse(BaseModel):
    items: list[CallResponse]
    total: int
    limit: int
    offset: int


@router.get("", response_model=PaginatedCallsResponse)
async def list_calls(
    status: str | None = None,
    department_id: uuid.UUID | None = None,
    language: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = Query("created_at", pattern="^(created_at|duration_seconds|status|caller_number)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> PaginatedCallsResponse:
    service = CallService(db)
    calls, total = await service.list_filtered(
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
    return PaginatedCallsResponse(
        items=[CallResponse.from_call(c) for c in calls], total=total, limit=limit, offset=offset
    )


@router.get("/{call_id}", response_model=CallDetailResponse)
async def get_call(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> CallDetailResponse:
    call_service = CallService(db)
    call = await call_service.get_by_id_or_raise(call_id)

    usage_service = UsageTrackingService(db)
    cost = await usage_service.cost_for_call(call.call_sid)

    recording_service = CallRecordingService(db)
    recording = await recording_service.get(call.call_sid)

    base = CallResponse.from_call(call)
    return CallDetailResponse(
        **base.model_dump(),
        transcript=call.transcript,
        error_message=call.error_message,
        cost=CostBreakdown(**cost) if cost else None,
        recording=(
            RecordingInfo(
                id=recording.id,
                has_audio=bool(recording.audio_file_path),
                status=recording.status,
                duration=recording.duration,
            )
            if recording
            else None
        ),
    )
