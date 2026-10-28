"""Event-order and retry invariants for the two call completion paths."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.call_recording_service import CallRecordingService
from app.services.call_service import CallService


class _CallRepo:
    def __init__(self, call):
        self.call = call

    async def get_by_sid_for_update(self, call_sid):
        assert call_sid == self.call.call_sid
        return self.call

    async def update(self, call, **values):
        for key, value in values.items():
            setattr(call, key, value)
        return call


def _service(status="initiated"):
    call = SimpleNamespace(
        call_sid="CA_ORDER",
        caller_number="+919876543210",
        status=status,
        ended_at=None,
        started_at=datetime.now(UTC),
        duration_seconds=None,
        transcript=None,
        recording_url=None,
    )
    service = CallService(AsyncMock())
    service._repo = _CallRepo(call)
    return service, call


@pytest.mark.asyncio
@pytest.mark.parametrize("status_first", [True, False])
async def test_completion_order_preserves_all_fields(status_first):
    service, call = _service()

    async def status_update():
        await service.complete(
            "CA_ORDER", duration_seconds=42, recording_url="https://recordings.example/call"
        )

    async def stream_update():
        await service.complete("CA_ORDER", transcript="Caller asked for architecture")

    if status_first:
        await status_update()
        await stream_update()
    else:
        await stream_update()
        await status_update()
    assert (call.status, call.duration_seconds, call.transcript, call.recording_url) == (
        "completed",
        42,
        "Caller asked for architecture",
        "https://recordings.example/call",
    )
    assert call.ended_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["failed", "busy", "no-answer", "canceled"])
@pytest.mark.parametrize("failure_first", [True, False])
async def test_failure_state_wins_in_either_order(terminal, failure_first):
    service, call = _service()

    async def failure():
        await service.complete("CA_ORDER", status=terminal, duration_seconds=3)

    async def stream():
        await service.complete("CA_ORDER", transcript="Partial call")

    if failure_first:
        await failure()
        await stream()
    else:
        await stream()
        await failure()
    assert call.status == terminal
    assert call.duration_seconds == 3
    assert call.transcript == "Partial call"


@pytest.mark.asyncio
async def test_duplicate_status_callback_keeps_first_end_time():
    service, call = _service()
    await service.complete("CA_ORDER", status="busy")
    first_ended_at = call.ended_at
    await service.complete("CA_ORDER", status="busy")
    assert call.ended_at == first_ended_at
    assert call.status == "busy"


@pytest.mark.asyncio
async def test_provider_recording_callback_is_idempotent():
    _, call = _service()
    call.recording_url = "https://recordings.example/call"
    service = CallRecordingService(AsyncMock())
    stored = None

    async def create(**fields):
        nonlocal stored
        stored = SimpleNamespace(**fields)
        return stored

    async def update(row, **fields):
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    service._repo.get_by_call_sid = AsyncMock(side_effect=lambda _: stored)
    service._repo.create = AsyncMock(side_effect=create)
    service._repo.update = AsyncMock(side_effect=update)

    await service.record_provider_metadata(call)
    await service.record_provider_metadata(call)
    service._repo.create.assert_awaited_once()
    assert stored.provider_url == call.recording_url


@pytest.mark.asyncio
async def test_empty_values_never_erase_stored_fields():
    service, call = _service()
    await service.complete(
        "CA_ORDER", duration_seconds=42, transcript="Real transcript", recording_url="https://r/1"
    )
    await service.complete("CA_ORDER", transcript="", recording_url="")
    assert (call.transcript, call.recording_url, call.duration_seconds) == (
        "Real transcript",
        "https://r/1",
        42,
    )


@pytest.mark.asyncio
async def test_active_status_never_regresses_and_terminal_is_final():
    service, call = _service()
    await service.update_status("CA_ORDER", "in-progress")
    await service.update_status("CA_ORDER", "ringing")  # late, out-of-order delivery
    assert call.status == "in-progress"
    await service.complete("CA_ORDER", status="completed")
    await service.update_status("CA_ORDER", "in-progress")
    assert call.status == "completed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("completed", "completed"),
        ("No-Answer", "no-answer"),
        ("no_answer", "no-answer"),
        ("cancelled", "canceled"),
        ("in_progress", "in-progress"),
        ("busy", "busy"),
        ("hacked", None),
        ("", None),
        (None, None),
    ],
)
def test_status_normalization(raw, expected):
    from app.services.call_service import normalize_call_status

    assert normalize_call_status(raw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status_first", [True, False])
async def test_recording_row_converges_in_either_completion_order(status_first):
    """Status callback (recording URL) and stream finalizer (transcript) in
    either order: one recording row carrying both."""
    service, call = _service()
    recordings = CallRecordingService(AsyncMock())
    rows: list[SimpleNamespace] = []

    async def create(**fields):
        rows.append(SimpleNamespace(**fields))
        return rows[-1]

    async def update(row, **fields):
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    recordings._repo.get_by_call_sid = AsyncMock(side_effect=lambda _: rows[0] if rows else None)
    recordings._repo.create = AsyncMock(side_effect=create)
    recordings._repo.update = AsyncMock(side_effect=update)

    async def status_callback():
        updated = await service.complete(
            "CA_ORDER", duration_seconds=30, recording_url="https://recordings.example/call"
        )
        await recordings.record_provider_metadata(updated)

    async def stream_finalizer():
        updated = await service.complete("CA_ORDER", transcript="Caller wants a renovation")
        await recordings.record_provider_metadata(updated)

    for step in (
        (status_callback, stream_finalizer)
        if status_first
        else (
            stream_finalizer,
            status_callback,
        )
    ):
        await step()
    await status_callback()  # Exotel retry

    assert len(rows) == 1
    assert rows[0].provider_url == "https://recordings.example/call"
    assert rows[0].transcript == "Caller wants a renovation"
    assert rows[0].duration == 30
