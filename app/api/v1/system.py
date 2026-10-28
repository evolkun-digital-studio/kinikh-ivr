"""System status + integration connection-testing endpoints (protected)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PROVIDER_FIELDS, Settings, get_settings, is_provider_configured
from app.core.dependencies import get_current_user_id, get_db
from app.services.integration_test_service import IntegrationConnectionService, get_last_result

router = APIRouter(prefix="/system", tags=["System"])


class IntegrationProvider(str, Enum):
    openai = "openai"
    elevenlabs = "elevenlabs"
    exotel = "exotel"
    email = "email"


class ProviderStatus(BaseModel):
    provider: str
    configured: bool
    status: str  # "connected" | "failed" | "not_configured" | "not_verified"
    detail: str
    last_tested_at: str | None = None


def _provider_statuses(settings: Settings) -> list[ProviderStatus]:
    """Live per-provider status.

    `configured` is cheap and always current (a local string comparison
    against known dev-mock placeholders — no network I/O, safe to compute on
    every call). Actual connectivity (`connected`/`failed`) is never probed
    here — only the most recent explicit "Test Connection" result is
    reflected, per the last-tested cache in `integration_test_service`. A
    configured-but-never-tested provider is reported "not_verified", never
    guessed as "connected".
    """
    statuses = []
    for provider in PROVIDER_FIELDS:
        configured = is_provider_configured(provider, settings)
        cached = get_last_result(provider)

        if not configured:
            status = "not_configured"
            detail = "Configuration pending — using development mock credentials."
            last_tested_at = None
        elif cached is not None and cached.status in ("connected", "failed"):
            status = cached.status
            detail = cached.message
            last_tested_at = cached.tested_at
        else:
            status = "not_verified"
            detail = "Configured but not yet tested. Click Test Connection to verify."
            last_tested_at = None

        statuses.append(
            ProviderStatus(
                provider=provider,
                configured=configured,
                status=status,
                detail=detail,
                last_tested_at=last_tested_at,
            )
        )
    return statuses


class SystemStatusResponse(BaseModel):
    environment: str
    app_version: str
    timestamp: str
    database: str
    providers: list[ProviderStatus]


@router.get("/status", response_model=SystemStatusResponse)
async def system_status(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> SystemStatusResponse:
    settings = get_settings()
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return SystemStatusResponse(
        environment=settings.app_env,
        app_version="1.0.0",
        timestamp=datetime.now(UTC).isoformat(),
        database=db_status,
        providers=_provider_statuses(settings),
    )


class IntegrationTestResponse(BaseModel):
    provider: str
    status: str  # "connected" | "failed" | "not_configured"
    message: str
    tested_at: str


@router.post("/integrations/{provider}/test", response_model=IntegrationTestResponse)
async def test_integration(
    provider: IntegrationProvider,
    _: str = Depends(get_current_user_id),
) -> IntegrationTestResponse:
    """Run a real, lightweight, non-billable connection test against one
    provider and record it as that provider's latest result (reflected by
    `/system/status` afterwards). Never returns credentials or raw
    provider exceptions — see `IntegrationConnectionService` for the
    per-provider safe-error mapping."""
    service = IntegrationConnectionService()
    result = await service.test(provider.value)
    return IntegrationTestResponse(
        provider=result.provider,
        status=result.status,
        message=result.message,
        tested_at=result.tested_at,
    )

class ReindexResponse(BaseModel):
    mode: str
    chunks: int

@router.post("/rag/reindex", response_model=ReindexResponse)
async def reindex_rag(
    _: str = Depends(get_current_user_id),
) -> ReindexResponse:
    from app.rag.index import get_knowledge_index
    index = get_knowledge_index()
    await index.build()
    return ReindexResponse(mode=index.mode, chunks=len(index.chunks))
