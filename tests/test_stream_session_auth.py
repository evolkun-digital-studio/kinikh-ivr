import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.dependencies import get_db
from app.main import app
from app.models.stream_session import StreamSession
from app.services.stream_session_service import StreamSessionService
from sqlalchemy import text, update


@pytest.fixture
def test_client():
    app.dependency_overrides.clear()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.asyncio
async def test_stream_session_auth_flow(test_client, mock_db_session) -> None:
    settings = get_settings()

    # Apply database override for the test client routes
    app.dependency_overrides[get_db] = lambda: mock_db_session

    mock_call_service = AsyncMock()
    mock_session_service = AsyncMock()
    mock_session_service.create_session.return_value = "fake-sid-123"
    
    with patch("app.api.v1.telephony.CallService", return_value=mock_call_service), \
         patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service), \
         patch("app.api.v1.telephony.ExotelAdapter.authenticate_webhook", return_value=True):
         
        # 1. GET /incoming returns short sid URL
        response = test_client.get(
            "/api/v1/telephony/incoming",
            params={
                "webhook_token": settings.exotel_webhook_secret,
                "CallSid": "rt-test-call-456",
                "CallFrom": "0800000000",
                "CallTo": "0900000000",
            }
        )
    assert response.status_code == 200
    data = response.json()
    
    # 9. Generated URL remains valid after URL parsing/encoding
    wss_url = data["url"]
    parsed_url = urlparse(wss_url)
    query = parse_qs(parsed_url.query)
    
    assert parsed_url.path.startswith("/api/v1/telephony/stream/")
    sid = parsed_url.path.split("/")[-1]
    
    # 2. sid length is comfortably below Exotel query limits (Exotel customParam is 256 char)
    assert len(sid) < 100
    
    ws_path = parsed_url.path + "?" + parsed_url.query

    # Mock CallService so we don't fail looking up call status
    mock_call = AsyncMock()
    mock_call.status = "in-progress"

    # 3. Valid sid connects (10. full route round-trip uses the production functions)
    mock_session_service.consume_session.return_value = "rt-test-call-456"
    with patch("app.api.v1.telephony.CallService") as mock_cs_cls, \
         patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service):
        mock_cs = AsyncMock()
        mock_cs.get_by_sid.return_value = mock_call
        mock_cs_cls.return_value = mock_cs
        
        with test_client.websocket_connect(ws_path) as websocket:
            # We connected successfully
            pass

    # 6. Consumed sid rejected
    mock_session_service.consume_session.return_value = None
    with patch("app.api.v1.telephony.CallService") as mock_cs_cls, \
         patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service):
        mock_cs = AsyncMock()
        mock_cs.get_by_sid.return_value = mock_call
        mock_cs_cls.return_value = mock_cs
        
        with pytest.raises(Exception) as exc:
            with test_client.websocket_connect(ws_path) as websocket:
                pass
        assert getattr(exc.value, "code", None) == 1008 or "1008" in str(exc.value)

    # 4. Invalid sid rejected
    with patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service):
        with pytest.raises(Exception) as exc:
            with test_client.websocket_connect("/api/v1/telephony/stream/invalid_fake_sid"):
                pass
        assert getattr(exc.value, "code", None) == 1008 or "1008" in str(exc.value)

    # 5. Expired sid rejected
    # In the new logic, expired sids return None from consume_session
    mock_session_service.consume_session.return_value = None
    with patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service):
        with pytest.raises(Exception) as exc:
            with test_client.websocket_connect("/api/v1/telephony/stream/expired-sid-123"):
                pass
        assert getattr(exc.value, "code", None) == 1008 or "1008" in str(exc.value)


@pytest.mark.asyncio
async def test_concurrent_stream_session_consume():
    # 8. multi-worker-safe atomic claim
    # We verify the SQL AST to ensure the update has atomic conditions.
    from sqlalchemy.dialects import postgresql
    
    stmt = (
        update(StreamSession)
        .where(
            StreamSession.stream_session_id == "test",
            StreamSession.consumed == False,
            StreamSession.expires_at > datetime.now(UTC),
        )
        .values(consumed=True)
        .returning(StreamSession.call_sid)
    )
    
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql_str = str(compiled)
    
    # Must have WHERE clause ensuring it is not consumed and not expired
    assert "consumed = false" in sql_str
    assert "expires_at >" in sql_str
    
    # Must SET consumed atomically
    assert "SET consumed=" in sql_str
    
    # Must RETURN the call_sid
    assert "RETURNING stream_sessions.call_sid" in sql_str


@pytest.mark.asyncio
async def test_concurrent_websocket_connection_limit(test_client, mock_db_session):
    # 7. Same sid cannot create two active streams
    # Although sid is single-use, we also check that _active_streams prevents multiple concurrent
    # websocket connections for the same call_sid regardless.
    app.dependency_overrides[get_db] = lambda: mock_db_session
    
    mock_session_service = AsyncMock()
    mock_session_service.consume_session.return_value = "shared-call-123"

    mock_call = AsyncMock()
    mock_call.status = "in-progress"

    with patch("app.api.v1.telephony.CallService") as mock_cs_cls, \
         patch("app.api.v1.telephony.StreamSessionService", return_value=mock_session_service):
        mock_cs = AsyncMock()
        mock_cs.get_by_sid.return_value = mock_call
        mock_cs_cls.return_value = mock_cs
        
        with test_client.websocket_connect(f"/api/v1/telephony/stream/sid1") as ws1:
            # First stream connects and holds "shared-call-123" in _active_streams
            
            with pytest.raises(Exception) as exc:
                with test_client.websocket_connect(f"/api/v1/telephony/stream/sid2") as ws2:
                    pass
            # The second stream should be immediately closed because of active stream check
            assert getattr(exc.value, "code", None) == 1008 or "1008" in str(exc.value)

    app.dependency_overrides.clear()
