"""Tests for app/core/media.py — safe resolution of stored recording paths.

`CallRecording.audio_file_path` is untrusted, DB-stored input. These tests
cover the security properties the recording-audio endpoint depends on: no
absolute paths, no traversal outside the recordings directory, no
unexpected file types, and no silently-served missing files.
"""

from __future__ import annotations

from pathlib import Path

from app.core.media import content_type_for, resolve_audio_path


class TestResolveAudioPath:
    def test_resolves_valid_relative_path(self, tmp_path: Path):
        (tmp_path / "call123.wav").write_bytes(b"RIFF....WAVEfmt ")

        resolved = resolve_audio_path(str(tmp_path), "call123.wav")

        assert resolved is not None
        assert resolved == (tmp_path / "call123.wav").resolve()

    def test_resolves_nested_relative_path(self, tmp_path: Path):
        nested = tmp_path / "2026" / "09"
        nested.mkdir(parents=True)
        (nested / "call456.mp3").write_bytes(b"ID3")

        resolved = resolve_audio_path(str(tmp_path), "2026/09/call456.mp3")

        assert resolved == (nested / "call456.mp3").resolve()

    def test_rejects_absolute_path(self, tmp_path: Path):
        outside = tmp_path.parent / "secret.wav"
        outside.write_bytes(b"data")

        assert resolve_audio_path(str(tmp_path), str(outside)) is None

    def test_rejects_path_traversal(self, tmp_path: Path):
        secret = tmp_path.parent / "outside_dir_secret.wav"
        secret.write_bytes(b"data")

        assert resolve_audio_path(str(tmp_path), f"../{secret.name}") is None

    def test_rejects_dotdot_traversal_into_system_files(self, tmp_path: Path):
        assert resolve_audio_path(str(tmp_path), "../../../../../../etc/passwd") is None

    def test_rejects_disallowed_extension(self, tmp_path: Path):
        (tmp_path / "call.exe").write_bytes(b"MZ")

        assert resolve_audio_path(str(tmp_path), "call.exe") is None

    def test_rejects_no_extension(self, tmp_path: Path):
        (tmp_path / "call_no_ext").write_bytes(b"data")

        assert resolve_audio_path(str(tmp_path), "call_no_ext") is None

    def test_rejects_missing_file(self, tmp_path: Path):
        assert resolve_audio_path(str(tmp_path), "does_not_exist.wav") is None

    def test_rejects_empty_stored_path(self, tmp_path: Path):
        assert resolve_audio_path(str(tmp_path), "") is None

    def test_rejects_directory_masquerading_as_file(self, tmp_path: Path):
        (tmp_path / "call.wav").mkdir()

        assert resolve_audio_path(str(tmp_path), "call.wav") is None


class TestContentTypeFor:
    def test_known_extensions(self, tmp_path: Path):
        assert content_type_for(tmp_path / "a.wav") == "audio/wav"
        assert content_type_for(tmp_path / "a.mp3") == "audio/mpeg"
        assert content_type_for(tmp_path / "a.ogg") == "audio/ogg"
        assert content_type_for(tmp_path / "a.m4a") == "audio/mp4"

    def test_unknown_extension_falls_back(self, tmp_path: Path):
        assert content_type_for(tmp_path / "a.bin") == "application/octet-stream"
