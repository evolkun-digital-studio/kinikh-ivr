"""Readiness must fail when the database is unavailable."""

from unittest.mock import AsyncMock

from fastapi import Response
import pytest

from app.api.v1.health import health_check


@pytest.mark.asyncio
async def test_healthy_database_returns_ok():
    response = Response()
    result = await health_check(response, AsyncMock())
    assert response.status_code == 200
    assert result.status == result.database == "ok"


@pytest.mark.asyncio
async def test_failed_database_returns_service_unavailable():
    response = Response()
    db = AsyncMock()
    db.execute.side_effect = ConnectionError("unavailable")
    result = await health_check(response, db)
    assert response.status_code == 503
    assert result.status == result.database == "error"
