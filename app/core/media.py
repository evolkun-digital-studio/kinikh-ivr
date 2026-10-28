"""Safe resolution of stored media paths to real files on disk.

`CallRecording.audio_file_path` is always treated as a path *relative to*
`settings.recordings_dir` — never as an absolute filesystem path, and never
trusted directly. This module is the single place that turns that stored
value into a real path, rejecting anything that would escape the recordings
directory (path traversal), point outside it (absolute paths, symlinks that
resolve elsewhere), or name a file type we don't intend to serve.
"""

from __future__ import annotations

from pathlib import Path

# Only formats the voice pipeline could plausibly produce. Anything else
# (including files with no extension) is refused — this is a serving
# allowlist, not a format validator.
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".m4a", ".webm"}

AUDIO_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


def resolve_audio_path(base_dir: str, stored_path: str) -> Path | None:
    """Resolve `stored_path` (relative, from the DB) under `base_dir`.

    Returns None — never raises — for any input that is unsafe or doesn't
    point at a real, allowed audio file, so callers can uniformly turn that
    into a 404 without distinguishing "traversal attempt" from "file
    missing" in the response (both must look identical to a client).
    """
    if not stored_path:
        return None

    candidate = Path(stored_path)
    if candidate.is_absolute():
        return None
    if candidate.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return None

    base = Path(base_dir).resolve()
    try:
        resolved = (base / candidate).resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    if not resolved.is_relative_to(base):
        return None
    if not resolved.is_file():
        return None

    return resolved


def content_type_for(path: Path) -> str:
    return AUDIO_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
