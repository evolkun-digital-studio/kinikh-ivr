"""HTTP-level tests for GET /api/v1/recordings/{id}/audio.

Covers the security requirements from the recording-playback fix: auth
required, no raw filesystem path ever reaches the client, path traversal
and missing-file cases both collapse to a plain 404, and Range requests
(needed for browser seeking) work via Starlette's native FileResponse
support.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi.testclient import TestClient
import pytest

from app.core.dependencies import get_current_user_id, get_db
from app.main import app
from app.models.call_recording import CallRecording
from app.services.call_recording_service import CallRecordingService


def _client() -> TestClient:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user_id] = lambda: "dev-admin@example.com"
    return TestClient(app, raise_server_exceptions=False)


def _recording(**overrides) -> CallRecording:
    from datetime import UTC, datetime

    defaults = {
        "id": uuid.uuid4(),
        "call_sid": "CA_TEST",
        "caller_number": "+91000",
        "language": "en",
        "start_time": datetime.now(UTC),
        "status": "completed",
        "audio_file_path": "call.wav",
    }
    defaults.update(overrides)
    return CallRecording(**defaults)


class TestRecordingAudioEndpoint:
    def teardown_method(self) -> None:
        app.dependency_overrides.clear()

    def test_authorized_access_returns_audio_with_content_type(self, tmp_path, monkeypatch):
        audio_file = tmp_path / "call.wav"
        audio_file.write_bytes(b"RIFF0000WAVEfmt " + b"\x00" * 100)

        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )

        client = _client()
        recording = _recording()
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers.get("accept-ranges") == "bytes"
        assert response.content == audio_file.read_bytes()
        # The raw filesystem path must never appear anywhere in the response.
        assert str(tmp_path) not in str(response.headers)

    def test_missing_auth_header_is_rejected(self):
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(f"/api/v1/recordings/{uuid.uuid4()}/audio")

        assert response.status_code in (401, 422)

    def test_missing_recording_returns_404(self):
        from app.core.exceptions import NotFoundError

        client = _client()
        with patch.object(
            CallRecordingService,
            "get_or_raise",
            AsyncMock(side_effect=NotFoundError("CallRecording")),
        ):
            response = client.get(f"/api/v1/recordings/{uuid.uuid4()}/audio")

        assert response.status_code == 404
        assert "path" not in response.json()["error"]["message"].lower()

    def test_recording_without_audio_file_path_returns_404(self):
        client = _client()
        recording = _recording(audio_file_path=None)
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 404

    def test_missing_audio_file_on_disk_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )
        client = _client()
        recording = _recording(audio_file_path="never_written.wav")
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 404

    def test_path_traversal_attempt_returns_404_not_the_file(self, tmp_path, monkeypatch):
        secret = tmp_path.parent / "traversal_secret.wav"
        secret.write_bytes(b"top-secret-audio")
        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )
        client = _client()
        recording = _recording(audio_file_path=f"../{secret.name}")
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 404

    def test_absolute_path_attempt_returns_404(self, tmp_path, monkeypatch):
        absolute_secret = tmp_path.parent / "abs_secret.wav"
        absolute_secret.write_bytes(b"top-secret-audio")
        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )
        client = _client()
        recording = _recording(audio_file_path=str(absolute_secret))
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 404

    def test_range_request_returns_partial_content(self, tmp_path, monkeypatch):
        content = bytes(range(256)) * 4  # 1024 bytes
        audio_file = tmp_path / "call.mp3"
        audio_file.write_bytes(content)
        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )
        client = _client()
        recording = _recording(audio_file_path="call.mp3")
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(
                f"/api/v1/recordings/{recording.id}/audio", headers={"Range": "bytes=0-99"}
            )

        assert response.status_code == 206
        assert response.headers["content-range"] == f"bytes 0-99/{len(content)}"
        assert len(response.content) == 100
        assert response.content == content[:100]

    @pytest.mark.parametrize("bad_ext", ["exe", "sh", "php"])
    def test_disallowed_extension_returns_404(self, tmp_path, monkeypatch, bad_ext):
        (tmp_path / f"call.{bad_ext}").write_bytes(b"data")
        monkeypatch.setattr(
            "app.api.v1.recordings.get_settings",
            lambda: SimpleNamespace(recordings_dir=str(tmp_path)),
        )
        client = _client()
        recording = _recording(audio_file_path=f"call.{bad_ext}")
        with patch.object(CallRecordingService, "get_or_raise", AsyncMock(return_value=recording)):
            response = client.get(f"/api/v1/recordings/{recording.id}/audio")

        assert response.status_code == 404
