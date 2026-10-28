"""Health check endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str
    database: str


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health_check(response: Response, db: AsyncSession = Depends(get_db)) -> HealthResponse:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
        response.status_code = 503

    return HealthResponse(
        status="ok" if db_status == "ok" else "error",
        timestamp=datetime.now(UTC).isoformat(),
        version="1.0.0",
        database=db_status,
    )
