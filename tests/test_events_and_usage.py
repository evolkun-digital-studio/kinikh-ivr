"""Tests for the event bus, subscribers, usage tracking, and call recording service.

These subsystems are fully implemented but not yet wired into the live call
pipeline (see docs/pre_api_audit.md) — these tests verify their standalone
behavior ahead of that integration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.base import BaseEvent
from app.events.bus import EventBus
from app.events.events import (
    CallCompletedEvent,
    CallFailedEvent,
    CallStartedEvent,
    EmailFailedEvent,
    EmailSentEvent,
    LeadCreatedEvent,
    LeadSavedEvent,
    TranscriptReceivedEvent,
)
from app.events.publisher import EventPublisher
from app.events.subscriber import EventSubscriber
from app.events.subscribers.analytics_subscriber import AnalyticsSubscriber
from app.events.subscribers.logging_subscriber import LoggingSubscriber


class TestEvents:
    def test_base_event_has_id_and_timestamp(self):
        event = BaseEvent()
        assert event.event_id is not None
        assert event.timestamp is not None

    def test_concrete_events_carry_payload(self):
        started = CallStartedEvent(call_sid="SID1", caller_number="+91000")
        assert started.call_sid == "SID1"
        assert started.caller_number == "+91000"

        transcript = TranscriptReceivedEvent(call_sid="SID1", transcript="hello", language="en")
        assert transcript.transcript == "hello"

        lead_created = LeadCreatedEvent(call_sid="SID1", phone="+91000", department="rural")
        assert lead_created.department == "rural"

        lead_saved = LeadSavedEvent(call_sid="SID1")
        assert lead_saved.call_sid == "SID1"

        email_sent = EmailSentEvent(call_sid="SID1", recipient="rural@test.com")
        assert email_sent.recipient == "rural@test.com"

        email_failed = EmailFailedEvent(call_sid="SID1", error="smtp down")
        assert email_failed.error == "smtp down"

        completed = CallCompletedEvent(call_sid="SID1", duration_seconds=42)
        assert completed.duration_seconds == 42

        failed = CallFailedEvent(call_sid="SID1", reason="timeout", phase="listening")
        assert failed.reason == "timeout"


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscribed_handler(self):
        bus = EventBus()
        received: list[BaseEvent] = []

        class Recorder(EventSubscriber):
            async def handle(self, event: BaseEvent) -> None:
                received.append(event)

        bus.subscribe(CallStartedEvent, Recorder())
        event = CallStartedEvent(call_sid="SID1")
        await bus.publish(event)

        assert received == [event]

    @pytest.mark.asyncio
    async def test_publish_ignores_unsubscribed_event_types(self):
        bus = EventBus()
        await bus.publish(CallStartedEvent(call_sid="SID1"))  # no subscribers, no error

    def test_subscriber_count(self):
        bus = EventBus()
        sub = LoggingSubscriber()
        assert bus.subscriber_count(CallStartedEvent) == 0
        bus.subscribe(CallStartedEvent, sub)
        assert bus.subscriber_count(CallStartedEvent) == 1

    def test_unsubscribe_removes_handler(self):
        bus = EventBus()
        sub = LoggingSubscriber()
        bus.subscribe(CallStartedEvent, sub)
        bus.unsubscribe(CallStartedEvent, sub)
        assert bus.subscriber_count(CallStartedEvent) == 0

    def test_unsubscribe_unknown_subscriber_is_noop(self):
        bus = EventBus()
        bus.unsubscribe(CallStartedEvent, LoggingSubscriber())  # should not raise

    @pytest.mark.asyncio
    async def test_one_failing_subscriber_does_not_block_others(self):
        bus = EventBus()
        received: list[BaseEvent] = []

        class Failing(EventSubscriber):
            async def handle(self, event: BaseEvent) -> None:
                raise RuntimeError("boom")

        class Recorder(EventSubscriber):
            async def handle(self, event: BaseEvent) -> None:
                received.append(event)

        bus.subscribe(CallStartedEvent, Failing())
        bus.subscribe(CallStartedEvent, Recorder())

        event = CallStartedEvent(call_sid="SID1")
        await bus.publish(event)  # must not raise

        assert received == [event]


class TestEventPublisher:
    @pytest.mark.asyncio
    async def test_publish_uses_injected_bus(self):
        bus = EventBus()
        bus.publish = AsyncMock()
        publisher = EventPublisher(event_bus=bus)

        event = CallStartedEvent(call_sid="SID1")
        await publisher.publish(event)

        bus.publish.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_publish_creates_default_bus_when_none_given(self):
        publisher = EventPublisher()
        await publisher.publish(CallStartedEvent(call_sid="SID1"))  # no subscribers, no error


class TestLoggingSubscriber:
    @pytest.mark.asyncio
    async def test_handle_does_not_raise(self):
        sub = LoggingSubscriber()
        await sub.handle(CallStartedEvent(call_sid="SID1"))


class TestAnalyticsSubscriber:
    @pytest.mark.asyncio
    async def test_tracks_calls_completions_and_failures(self):
        sub = AnalyticsSubscriber()

        await sub.handle(CallStartedEvent(call_sid="SID1"))
        await sub.handle(CallStartedEvent(call_sid="SID2"))
        await sub.handle(CallCompletedEvent(call_sid="SID1", department="rural"))
        await sub.handle(CallFailedEvent(call_sid="SID2", reason="timeout"))

        assert sub.total_calls == 2
        assert sub.total_failures == 1
        assert sub.calls_by_department() == {"rural": 1}

    @pytest.mark.asyncio
    async def test_unknown_department_bucketed_as_unknown(self):
        sub = AnalyticsSubscriber()
        await sub.handle(CallCompletedEvent(call_sid="SID1", department=None))
        assert sub.calls_by_department() == {"unknown": 1}

    @pytest.mark.asyncio
    async def test_ignores_unrelated_events(self):
        sub = AnalyticsSubscriber()
        await sub.handle(TranscriptReceivedEvent(call_sid="SID1", transcript="hi"))
        assert sub.total_calls == 0
        assert sub.total_failures == 0


class TestUsageTrackingService:
    @pytest.fixture
    def service(self):
        from app.services.usage_tracking_service import UsageTrackingService

        session = AsyncMock(spec=AsyncSession)
        return UsageTrackingService(session)

    def test_init_call_creates_accumulator(self, service):
        acc = service.init_call("SID1")
        assert acc.call_sid == "SID1"
        assert acc.tokens == []

    def test_record_openai_appends_to_existing_accumulator(self, service):
        service.init_call("SID1")
        service.record_openai(
            "SID1",
            operation="chat",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=200,
        )
        assert len(service._accumulators["SID1"].tokens) == 1

    def test_record_calls_without_init_are_silently_ignored(self, service):
        # No init_call() was made for this call_sid — must not raise.
        service.record_openai(
            "UNKNOWN",
            operation="chat",
            model="gpt-4o",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1,
        )
        service.record_tts("UNKNOWN", provider="elevenlabs", characters=10)
        service.record_stt("UNKNOWN", audio_bytes=1000)
        service.record_email("UNKNOWN")

    @pytest.mark.asyncio
    async def test_finalize_missing_accumulator_returns_empty_report(self, service):
        report = await service.finalize("MISSING", duration_seconds=10)
        assert report == {}

    @pytest.mark.asyncio
    async def test_finalize_computes_nonnegative_costs_and_persists(self, service):
        service.init_call("SID1")
        service.record_openai(
            "SID1",
            operation="chat",
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            latency_ms=300,
        )
        service.record_tts("SID1", provider="elevenlabs", characters=200)
        service.record_stt("SID1", audio_bytes=32000)
        service.record_email("SID1")

        with (
            patch.object(service._usage_repo, "create", AsyncMock()),
            patch.object(service._cost_repo, "create", AsyncMock()),
        ):
            report = await service.finalize("SID1", duration_seconds=60)

        assert report["call_sid"] == "SID1"
        for value in report["costs"].values():
            assert value >= 0
        assert report["costs"]["total_usd"] == pytest.approx(
            sum(v for k, v in report["costs"].items() if k != "total_usd"), abs=1e-6
        )
        assert report["usage"]["openai_calls"] == 1
        assert report["usage"]["total_tokens"] == 1500

    @pytest.mark.asyncio
    async def test_finalize_survives_repository_failures(self, service):
        service.init_call("SID1")
        service.record_openai(
            "SID1",
            operation="chat",
            model="gpt-4o",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=10,
        )

        db_error = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch.object(service._usage_repo, "create", db_error),
            patch.object(service._cost_repo, "create", db_error),
        ):
            report = await service.finalize("SID1", duration_seconds=5)

        # Persistence failures are logged, not raised — the cost report is still returned.
        assert report["call_sid"] == "SID1"
        assert report["costs"]["total_usd"] >= 0


class TestCallRecordingService:
    @pytest.fixture
    def service(self):
        from app.services.call_recording_service import CallRecordingService

        session = AsyncMock(spec=AsyncSession)
        return CallRecordingService(session)

    @pytest.mark.asyncio
    async def test_start_recording_creates_record(self, service):
        from datetime import UTC, datetime

        from app.models.call_recording import CallRecording

        mock_recording = CallRecording(
            call_sid="SID1", caller_number="+91000", start_time=datetime.now(UTC)
        )
        with patch.object(service._repo, "create", AsyncMock(return_value=mock_recording)):
            recording = await service.start_recording("SID1", "+91000")

        assert recording.call_sid == "SID1"

    @pytest.mark.asyncio
    async def test_complete_recording_missing_returns_none(self, service):
        with patch.object(service._repo, "get_by_call_sid", AsyncMock(return_value=None)):
            result = await service.complete_recording("MISSING")
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_recording_updates_existing(self, service):
        from datetime import UTC, datetime

        from app.models.call_recording import CallRecording

        existing = CallRecording(
            call_sid="SID1", caller_number="+91000", start_time=datetime.now(UTC)
        )

        async def fake_update(obj, **kwargs):
            for k, v in kwargs.items():
                setattr(obj, k, v)
            return obj

        with (
            patch.object(service._repo, "get_by_call_sid", AsyncMock(return_value=existing)),
            patch.object(service._repo, "update", fake_update),
        ):
            result = await service.complete_recording(
                "SID1", transcript="hello", summary="summary", department="rural", language="en"
            )

        assert result.status == "completed"
        assert result.transcript == "hello"
        assert result.department == "rural"

    @pytest.mark.asyncio
    async def test_mark_failed_updates_status(self, service):
        from datetime import UTC, datetime

        from app.models.call_recording import CallRecording

        existing = CallRecording(
            call_sid="SID1", caller_number="+91000", start_time=datetime.now(UTC)
        )

        async def fake_update(obj, **kwargs):
            for k, v in kwargs.items():
                setattr(obj, k, v)
            return obj

        with (
            patch.object(service._repo, "get_by_call_sid", AsyncMock(return_value=existing)),
            patch.object(service._repo, "update", fake_update),
        ):
            await service.mark_failed("SID1", "websocket disconnected")

        assert existing.status == "failed"
        assert existing.errors == "websocket disconnected"

    @pytest.mark.asyncio
    async def test_mark_failed_missing_recording_is_noop(self, service):
        with patch.object(service._repo, "get_by_call_sid", AsyncMock(return_value=None)):
            await service.mark_failed("MISSING", "error")  # must not raise

    @pytest.mark.asyncio
    async def test_get_delegates_to_repository(self, service):
        get_mock = AsyncMock(return_value=None)
        with patch.object(service._repo, "get_by_call_sid", get_mock) as mock_get:
            result = await service.get("SID1")
        mock_get.assert_awaited_once_with("SID1")
        assert result is None


class TestUsageAndCostRepositories:
    def test_api_usage_repository_init(self):
        from app.models.api_usage import ApiUsage
        from app.repositories.api_usage_repository import ApiUsageRepository

        session = AsyncMock(spec=AsyncSession)
        repo = ApiUsageRepository(session)
        assert repo._model is ApiUsage

    @pytest.mark.asyncio
    async def test_api_usage_repository_list_by_call_sid(self):
        from app.repositories.api_usage_repository import ApiUsageRepository

        session = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        repo = ApiUsageRepository(session)
        records = await repo.list_by_call_sid("SID1")

        assert records == []
        session.execute.assert_awaited_once()

    def test_call_cost_repository_init(self):
        from app.models.call_cost import CallCost
        from app.repositories.call_cost_repository import CallCostRepository

        session = AsyncMock(spec=AsyncSession)
        repo = CallCostRepository(session)
        assert repo._model is CallCost

    @pytest.mark.asyncio
    async def test_call_cost_repository_get_by_call_sid(self):
        from app.repositories.call_cost_repository import CallCostRepository

        session = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        repo = CallCostRepository(session)
        result = await repo.get_by_call_sid("SID1")

        assert result is None

    def test_call_recording_repository_init(self):
        from app.models.call_recording import CallRecording
        from app.repositories.call_recording_repository import CallRecordingRepository

        session = AsyncMock(spec=AsyncSession)
        repo = CallRecordingRepository(session)
        assert repo._model is CallRecording

    @pytest.mark.asyncio
    async def test_call_recording_repository_get_by_call_sid(self):
        from app.repositories.call_recording_repository import CallRecordingRepository

        session = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        repo = CallRecordingRepository(session)
        result = await repo.get_by_call_sid("SID1")

        assert result is None
