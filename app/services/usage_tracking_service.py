"""UsageTrackingService — accumulates API usage per call and persists cost reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.api_usage_repository import ApiUsageRepository
from app.repositories.call_cost_repository import CallCostRepository

# ── Pricing constants (USD) ──────────────────────────────────────────────────
_GPT4O_INPUT_PER_TOKEN: float = 0.0000025  # $2.50 / 1M input tokens
_GPT4O_OUTPUT_PER_TOKEN: float = 0.000010  # $10.00 / 1M output tokens
_WHISPER_PER_SECOND: float = 0.0001  # ~$0.006 / min
_OPENAI_TTS_PER_CHAR: float = 0.000015  # $15 / 1M chars (tts-1)
_ELEVENLABS_PER_CHAR: float = 0.0003  # ~$0.30 / 1000 chars
_EXOTEL_PER_SECOND: float = 0.0001  # ~$0.006 / min
_EMAIL_FLAT: float = 0.0001  # per email (SES estimate)


@dataclass
class _TokenRecord:
    operation: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int


@dataclass
class _TtsRecord:
    provider: str
    characters: int
    latency_ms: int


@dataclass
class _SttRecord:
    audio_bytes: int
    latency_ms: int


@dataclass
class _EmailRecord:
    delivery_ms: int = 0
    retry_count: int = 0


@dataclass
class UsageAccumulator:
    """In-memory accumulator for one call's API usage."""

    call_sid: str
    tokens: list[_TokenRecord] = field(default_factory=list)
    tts_records: list[_TtsRecord] = field(default_factory=list)
    stt_records: list[_SttRecord] = field(default_factory=list)
    email_records: list[_EmailRecord] = field(default_factory=list)


class UsageTrackingService:
    """Records API usage per call and generates per-call cost report JSON."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._usage_repo = ApiUsageRepository(db)
        self._cost_repo = CallCostRepository(db)
        self._accumulators: dict[str, UsageAccumulator] = {}

    def init_call(self, call_sid: str) -> UsageAccumulator:
        acc = UsageAccumulator(call_sid=call_sid)
        self._accumulators[call_sid] = acc
        return acc

    def record_openai(
        self,
        call_sid: str,
        operation: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: int,
    ) -> None:
        acc = self._accumulators.get(call_sid)
        if acc:
            acc.tokens.append(
                _TokenRecord(
                    operation=operation,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                )
            )

    def record_tts(
        self, call_sid: str, provider: str, characters: int, latency_ms: int = 0
    ) -> None:
        acc = self._accumulators.get(call_sid)
        if acc:
            acc.tts_records.append(
                _TtsRecord(provider=provider, characters=characters, latency_ms=latency_ms)
            )

    def record_stt(self, call_sid: str, audio_bytes: int, latency_ms: int = 0) -> None:
        acc = self._accumulators.get(call_sid)
        if acc:
            acc.stt_records.append(_SttRecord(audio_bytes=audio_bytes, latency_ms=latency_ms))

    def record_email(self, call_sid: str, delivery_ms: int = 0, retry_count: int = 0) -> None:
        acc = self._accumulators.get(call_sid)
        if acc:
            acc.email_records.append(_EmailRecord(delivery_ms=delivery_ms, retry_count=retry_count))

    async def finalize(self, call_sid: str, duration_seconds: int) -> dict[str, Any]:
        """Persist usage records to DB and return the cost report."""
        acc = self._accumulators.pop(call_sid, None)
        if not acc:
            logger.warning(f"[usage] no accumulator found for call_sid={call_sid}")
            return {}

        openai_cost = 0.0
        for t in acc.tokens:
            cost = (
                t.prompt_tokens * _GPT4O_INPUT_PER_TOKEN
                + t.completion_tokens * _GPT4O_OUTPUT_PER_TOKEN
            )
            openai_cost += cost
            try:
                await self._usage_repo.create(
                    call_sid=call_sid,
                    provider="openai",
                    model=t.model,
                    operation=t.operation,
                    prompt_tokens=t.prompt_tokens,
                    completion_tokens=t.completion_tokens,
                    total_tokens=t.total_tokens,
                    latency_ms=t.latency_ms,
                    estimated_cost_usd=round(cost, 8),
                )
            except Exception as exc:
                logger.error(f"[usage] failed to persist openai record: {exc}")

        stt_cost = 0.0
        for r in acc.stt_records:
            est_seconds = r.audio_bytes / 16000
            cost = est_seconds * _WHISPER_PER_SECOND
            stt_cost += cost
            try:
                await self._usage_repo.create(
                    call_sid=call_sid,
                    provider="openai",
                    model="whisper-1",
                    operation="transcribe",
                    duration_seconds=int(est_seconds),
                    latency_ms=r.latency_ms,
                    estimated_cost_usd=round(cost, 8),
                )
            except Exception as exc:
                logger.error(f"[usage] failed to persist stt record: {exc}")

        voice_cost = 0.0
        for r in acc.tts_records:
            per_char = _ELEVENLABS_PER_CHAR if r.provider == "elevenlabs" else _OPENAI_TTS_PER_CHAR
            cost = r.characters * per_char
            voice_cost += cost
            try:
                await self._usage_repo.create(
                    call_sid=call_sid,
                    provider=r.provider,
                    operation="tts",
                    characters=r.characters,
                    latency_ms=r.latency_ms,
                    estimated_cost_usd=round(cost, 8),
                )
            except Exception as exc:
                logger.error(f"[usage] failed to persist tts record: {exc}")

        telephony_cost = duration_seconds * _EXOTEL_PER_SECOND

        email_cost = 0.0
        for r in acc.email_records:
            email_cost += _EMAIL_FLAT
            try:
                await self._usage_repo.create(
                    call_sid=call_sid,
                    provider="smtp",
                    operation="email",
                    latency_ms=r.delivery_ms,
                    estimated_cost_usd=_EMAIL_FLAT,
                )
            except Exception as exc:
                logger.error(f"[usage] failed to persist email record: {exc}")

        total_cost = openai_cost + stt_cost + voice_cost + telephony_cost + email_cost

        report: dict[str, Any] = {
            "call_sid": call_sid,
            "duration_seconds": duration_seconds,
            "costs": {
                "openai_usd": round(openai_cost, 6),
                "stt_usd": round(stt_cost, 6),
                "voice_usd": round(voice_cost, 6),
                "telephony_usd": round(telephony_cost, 6),
                "email_usd": round(email_cost, 6),
                "total_usd": round(total_cost, 6),
            },
            "usage": {
                "openai_calls": len(acc.tokens),
                "total_tokens": sum(t.total_tokens for t in acc.tokens),
                "tts_characters": sum(r.characters for r in acc.tts_records),
                "emails_sent": len(acc.email_records),
            },
        }

        try:
            await self._cost_repo.create(
                call_sid=call_sid,
                telephony_cost_usd=round(telephony_cost, 6),
                openai_cost_usd=round(openai_cost + stt_cost, 6),
                voice_cost_usd=round(voice_cost, 6),
                email_cost_usd=round(email_cost, 6),
                total_cost_usd=round(total_cost, 6),
                duration_seconds=duration_seconds,
                report=report,
            )
        except Exception as exc:
            logger.error(f"[usage] failed to persist call cost: {exc}")

        logger.info(
            f"[usage] finalized call_sid={call_sid}"
            f" total=${total_cost:.6f}"
            f" tokens={sum(t.total_tokens for t in acc.tokens)}"
        )
        return report

    async def cost_summary(self) -> dict[str, Any]:
        """Aggregate cost/usage totals across all calls for the Usage & Cost page."""
        cost_totals = await self._cost_repo.totals()
        provider_rows = await self._usage_repo.totals_by_provider()
        by_provider = [
            {
                "provider": provider,
                "requests": requests,
                "total_tokens": tokens,
                "total_characters": characters,
                "estimated_cost_usd": round(cost, 6),
            }
            for provider, requests, tokens, characters, cost in provider_rows
        ]
        return {
            "totals": {
                "telephony_usd": round(cost_totals["telephony_usd"], 6),
                "openai_usd": round(cost_totals["openai_usd"], 6),
                "voice_usd": round(cost_totals["voice_usd"], 6),
                "email_usd": round(cost_totals["email_usd"], 6),
                "total_usd": round(cost_totals["total_usd"], 6),
                "call_count": cost_totals["call_count"],
                "cost_per_call_usd": (
                    round(cost_totals["total_usd"] / cost_totals["call_count"], 6)
                    if cost_totals["call_count"]
                    else 0.0
                ),
            },
            "by_provider": by_provider,
        }

    async def cost_timeseries(self, days: int = 30) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = await self._cost_repo.cost_over_time(since)
        return [{"date": str(day), "cost_usd": round(cost, 6)} for day, cost in rows]

    async def cost_for_call(self, call_sid: str) -> dict[str, Any] | None:
        cost = await self._cost_repo.get_by_call_sid(call_sid)
        if cost is None:
            return None
        return {
            "telephony_cost_usd": cost.telephony_cost_usd,
            "openai_cost_usd": cost.openai_cost_usd,
            "voice_cost_usd": cost.voice_cost_usd,
            "email_cost_usd": cost.email_cost_usd,
            "total_cost_usd": cost.total_cost_usd,
            "duration_seconds": cost.duration_seconds,
        }
