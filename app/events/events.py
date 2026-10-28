"""Concrete event types for the call pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid

from app.events.base import BaseEvent


@dataclass
class CallStartedEvent(BaseEvent):
    call_sid: str = ""
    caller_number: str = ""


@dataclass
class CallConnectedEvent(BaseEvent):
    call_sid: str = ""
    caller_number: str = ""


@dataclass
class TranscriptReceivedEvent(BaseEvent):
    call_sid: str = ""
    transcript: str = ""
    language: str = "en"


@dataclass
class AIResponseGeneratedEvent(BaseEvent):
    call_sid: str = ""
    response: str = ""
    language: str = "en"
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class VoiceGeneratedEvent(BaseEvent):
    call_sid: str = ""
    text: str = ""
    audio_bytes: int = 0
    provider: str = "openai"


@dataclass
class LeadCreatedEvent(BaseEvent):
    call_sid: str = ""
    lead_id: uuid.UUID = field(default_factory=uuid.uuid4)
    phone: str = ""
    department: str | None = None


@dataclass
class LeadSavedEvent(BaseEvent):
    call_sid: str = ""
    lead_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class EmailSentEvent(BaseEvent):
    call_sid: str = ""
    lead_id: uuid.UUID = field(default_factory=uuid.uuid4)
    recipient: str = ""


@dataclass
class EmailFailedEvent(BaseEvent):
    call_sid: str = ""
    lead_id: uuid.UUID = field(default_factory=uuid.uuid4)
    error: str = ""


@dataclass
class CallCompletedEvent(BaseEvent):
    call_sid: str = ""
    duration_seconds: int = 0
    language: str = "en"
    department: str | None = None


@dataclass
class CallFailedEvent(BaseEvent):
    call_sid: str = ""
    reason: str = ""
    phase: str = ""
