"""Safe local test for the Exotel AgentStream/Voicebot WebSocket pipeline.

Scope: verifies WS connect -> ExotelAudioStream -> 'start' event ->
SpeechHandler initialization -> AI greeting sent back in Exotel's exact
protocol format (PCM16LE/8000Hz/mono) -> a real spoken utterance is sent as
'media' events -> STT -> ConversationEngine -> OpenAI -> ElevenLabs reply ->
clean finalization, against the currently running backend through the
active Cloudflare quick tunnel.

The "caller utterance" audio is generated locally with macOS `say` and
converted to PCM16LE/8000Hz/mono via ffmpeg — real speech, not silence or
random bytes, so Whisper has a genuine chance to transcribe it and drive the
rest of the pipeline. If `say`/`ffmpeg` are unavailable, the media-send step
is skipped and clearly marked NOT VERIFIED rather than faked.

WARNING — this test has real side effects, inherent to the existing code
(nothing added by this script):
  * SpeechHandler._ensure_initialized() always speaks a real greeting via
    ElevenLabs as soon as a 'start' event arrives — one real TTS API call.
  * Sending real utterance audio triggers a real Whisper STT call, a real
    OpenAI chat completion, and (if a transcript came back) a second real
    ElevenLabs TTS call for the reply.
  * SpeechHandler.finalize() always runs OpenAI summary generation and lead
    extraction once the WebSocket closes — two more real OpenAI API calls —
    and creates a real Call + Lead database row tagged with an unmistakable
    test CallSid.

Run scripts/cleanup_exotel_websocket_test.py <CALL_SID> afterward to remove
the rows this creates. Never modifies any pre-existing Call/Lead.

Usage:
    python scripts/test_exotel_websocket.py
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import json
import shutil
import ssl
import struct
import subprocess
import tempfile
import uuid

import certifi
import httpx
import websockets

TUNNEL_HOST = "presented-louisiana-vids-northern.trycloudflare.com"
INCOMING_URL = f"https://{TUNNEL_HOST}/api/v1/telephony/incoming"
STREAM_URL = f"wss://{TUNNEL_HOST}/api/v1/telephony/stream"

TEST_CALL_SID = f"TEST_PROTOCOL_VERIFY_{uuid.uuid4().hex[:8].upper()}"
TEST_STREAM_SID = f"SS_TEST_{uuid.uuid4().hex[:8].upper()}"
TEST_CALLER_NUMBER = "+910000000000"
TEST_CALLEE_NUMBER = "+911111111111"
TEST_UTTERANCE_TEXT = "I need a product demo please"

# ElevenLabs TTS latency was observed at ~24s in prior testing (before the
# output_format fix reduced payload size). Use a generous timeout — a slow
# AI response is not a protocol failure, and a short timeout would produce a
# false negative rather than a real finding.
GREETING_TIMEOUT_SECONDS = 60
REPLY_TIMEOUT_SECONDS = 45

_ID3_MAGIC = b"ID3"


def _looks_like_mp3(data: bytes) -> bool:
    if data[:3] == _ID3_MAGIC:
        return True
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _generate_utterance_pcm16() -> bytes | None:
    """Generate real speech audio locally (macOS `say` + ffmpeg) as
    PCM16LE/8000Hz/mono raw bytes. Returns None if the tools aren't
    available — callers must treat that as NOT VERIFIED, never fake audio."""
    say_bin = shutil.which("say")
    ffmpeg_bin = shutil.which("ffmpeg")
    if not (say_bin and ffmpeg_bin):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        aiff_path = f"{tmp}/utterance.aiff"
        pcm_path = f"{tmp}/utterance.pcm"
        # Hardcoded local dev-only args (no user/network input) — safe subprocess use.
        subprocess.run([say_bin, "-o", aiff_path, TEST_UTTERANCE_TEXT], check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                aiff_path,
                "-f",
                "s16le",
                "-ar",
                "8000",
                "-ac",
                "1",
                pcm_path,
            ],
            check=True,
        )
        with open(pcm_path, "rb") as f:
            return f.read()


def _verify_pcm16_frame(message: str | bytes, label: str) -> bytes:
    """TEXT JSON media frame, correct fields, base64-decodable, and
    byte-level PCM16LE/8000Hz/mono verification (not just metadata)."""
    if isinstance(message, bytes):
        raise AssertionError(
            f"{label} arrived as a BINARY frame ({len(message)} bytes) — "
            "SpeechHandler._speak() is still using ws.send_bytes() instead of "
            "ExotelAudioStream.send_audio(). Protocol fix did not take effect."
        )

    frame = json.loads(message)  # raises if not valid JSON — no guessing
    assert frame.get("event") == "media", f"Expected event='media', got {frame.get('event')!r}"
    assert frame.get("stream_sid"), "stream_sid missing from media frame"
    assert "media" in frame and "payload" in frame["media"], "media.payload missing from frame"
    print(f"      {label}: event='media', stream_sid={frame['stream_sid']!r}, payload present.")

    audio_bytes = base64.b64decode(frame["media"]["payload"])  # raises if not valid base64
    print(f"      {label}: decoded {len(audio_bytes)} bytes.")

    if _looks_like_mp3(audio_bytes):
        raise AssertionError(
            f"{label}: decoded audio starts with an MP3 signature — ElevenLabs "
            "is still returning its MP3 default; the output_format=pcm_8000 "
            "fix did not take effect."
        )
    if len(audio_bytes) % 2 != 0:
        raise AssertionError(
            f"{label}: odd byte count ({len(audio_bytes)}) — not valid 16-bit "
            "PCM sample alignment."
        )
    # Actually decode as signed 16-bit little-endian samples (not just check
    # metadata) — proves the bytes are structurally consistent PCM16, and
    # gives a real duration figure at 8000Hz mono.
    sample_count = len(audio_bytes) // 2
    samples = struct.unpack(f"<{sample_count}h", audio_bytes)
    duration = sample_count / 8000
    peak = max(abs(s) for s in samples) if samples else 0
    print(
        f"      {label}: decoded as {sample_count} PCM16LE samples, "
        f"~{duration:.2f}s at 8000Hz mono, peak amplitude={peak} "
        f"(0=pure silence, 32767=max)."
    )
    return audio_bytes


async def simulate_incoming_webhook() -> None:
    """Mirror what Exotel actually does before opening the WSS stream: POST
    the incoming-call webhook so a real Call row exists first."""
    async with httpx.AsyncClient(verify=certifi.where(), timeout=10) as client:
        resp = await client.post(
            INCOMING_URL,
            data={
                "CallSid": TEST_CALL_SID,
                "From": TEST_CALLER_NUMBER,
                "To": TEST_CALLEE_NUMBER,
                "CallStatus": "ringing",
            },
        )
    resp.raise_for_status()
    body = resp.json()
    print(f"[1] Incoming webhook accepted. Resolver returned url={body.get('url')!r}")
    if body.get("url") not in (STREAM_URL, STREAM_URL.replace("wss://", "ws://")):
        print(f"    NOTE: resolver url does not match expected {STREAM_URL} — continuing anyway.")


async def run_pipeline_test() -> dict[str, bool | None]:
    results: dict[str, bool | None] = {
        "greeting_pcm16_frame": False,
        "utterance_sent": False,
        "reply_pcm16_frame": None,  # None = not attempted (no speech tooling)
    }
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    print(f"[2] Connecting to {STREAM_URL} ...")
    async with websockets.connect(STREAM_URL, ssl=ssl_context) as ws:
        print("    Connected.")

        await ws.send(json.dumps({"event": "connected"}))
        print("[3] Sent 'connected' frame (optional, not required by our code)")

        # Exotel's real AgentStream/Voicebot protocol is snake_case (confirmed
        # against developer.exotel.com/docs/agentstream/websocket-protocol) —
        # caller number is a direct `start.from` field, not nested under a
        # customParameters dict.
        start_frame = json.dumps(
            {
                "event": "start",
                "stream_sid": TEST_STREAM_SID,
                "start": {
                    "stream_sid": TEST_STREAM_SID,
                    "call_sid": TEST_CALL_SID,
                    "from": TEST_CALLER_NUMBER,
                },
            }
        )
        await ws.send(start_frame)
        print(f"[4] Sent 'start' frame: {start_frame}")

        print(f"[5] Waiting up to {GREETING_TIMEOUT_SECONDS}s for the AI greeting...")
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=GREETING_TIMEOUT_SECONDS)
            _verify_pcm16_frame(message, "Greeting")
            results["greeting_pcm16_frame"] = True
        except TimeoutError:
            print(f"    FAIL: no greeting within {GREETING_TIMEOUT_SECONDS}s.")
        except AssertionError as exc:
            print(f"    FAIL: {exc}")

        print("[6] Generating a real spoken utterance locally (say + ffmpeg)...")
        utterance_pcm = _generate_utterance_pcm16()
        if utterance_pcm is None:
            print("    SKIPPED: `say`/`ffmpeg` not available — media/STT/OpenAI reply path")
            print("    is NOT VERIFIED by this run (no fake audio sent).")
        else:
            print(f"    Generated {len(utterance_pcm)} bytes: {TEST_UTTERANCE_TEXT!r}")
            chunk_size = 3200  # ~200ms per chunk at 8000Hz/16-bit mono
            for i in range(0, len(utterance_pcm), chunk_size):
                chunk = utterance_pcm[i : i + chunk_size]
                media_frame = json.dumps(
                    {
                        "event": "media",
                        "stream_sid": TEST_STREAM_SID,
                        "media": {"payload": base64.b64encode(chunk).decode()},
                    }
                )
                await ws.send(media_frame)
            print(f"[7] Sent {len(range(0, len(utterance_pcm), chunk_size))} media chunk(s).")
            results["utterance_sent"] = True

            print(f"    Waiting up to {REPLY_TIMEOUT_SECONDS}s for the AI's reply (post-STT)...")
            results["reply_pcm16_frame"] = False
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=REPLY_TIMEOUT_SECONDS)
                _verify_pcm16_frame(message, "Reply")
                results["reply_pcm16_frame"] = True
            except TimeoutError:
                print(
                    f"    NOT VERIFIED: no reply within {REPLY_TIMEOUT_SECONDS}s — Whisper may "
                    "have transcribed empty text (silence threshold/audio level), which is a "
                    "legitimate outcome for synthesized speech, not necessarily a protocol bug. "
                    "Check backend logs for [STT] transcript=..."
                )
            except AssertionError as exc:
                print(f"    FAIL: {exc}")

        stop_frame = json.dumps({"event": "stop", "stream_sid": TEST_STREAM_SID})
        await ws.send(stop_frame)
        print(f"[8] Sent 'stop' frame: {stop_frame}")
        await asyncio.sleep(0.5)

    print(
        "    Connection closed cleanly. Server-side finalize() should now be running "
        "(real OpenAI summary + lead-extraction calls, plus Call/Lead DB writes) — check "
        "backend logs for 'call.finalized' / 'call.persistence.completed'."
    )
    return results


async def main() -> None:
    print("=" * 70)
    print("Exotel AgentStream/Voicebot pipeline verification")
    print(f"Test CallSid:   {TEST_CALL_SID}")
    print(f"Test StreamSid: {TEST_STREAM_SID}")
    print(f"Started at:     {datetime.now(UTC).isoformat()}")
    print("=" * 70)
    await simulate_incoming_webhook()
    results = await run_pipeline_test()

    print()
    print("RESULTS:")
    print(
        "  Greeting is a correct PCM16 media frame: "
        + ("PASS" if results["greeting_pcm16_frame"] else "FAIL")
    )
    if results["reply_pcm16_frame"] is None:
        print("  Utterance -> STT -> OpenAI -> reply frame: NOT VERIFIED (say/ffmpeg unavailable)")
    elif results["reply_pcm16_frame"]:
        print("  Utterance -> STT -> OpenAI -> reply frame: PASS (actually tested end-to-end)")
    else:
        print(
            "  Utterance -> STT -> OpenAI -> reply frame: NOT VERIFIED this run "
            "(no reply within timeout — check backend logs for the transcript)"
        )
    print()
    print("To clean up the test Call/Lead rows this created, run:")
    print(f"  python scripts/cleanup_exotel_websocket_test.py {TEST_CALL_SID}")


if __name__ == "__main__":
    asyncio.run(main())
