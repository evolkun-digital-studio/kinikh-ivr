import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.voice.speech_handler import SpeechHandler, TurnState
from app.voice.turn_metrics import TurnMetrics
from app.ai.context_manager import CallContext

@pytest.fixture
def mock_speech_handler():
    db = AsyncMock()
    handler = SpeechHandler(db=db)
    handler._context = CallContext(call_sid="TEST", caller_number="123")
    handler._send_audio = AsyncMock()
    handler._turn_state = TurnState.PROCESSING
    
    # Needs a dummy response
    from app.voice.speech_handler import _Response
    handler._response = _Response(turn_id=1, kind="caller_turn", metrics=TurnMetrics(call_sid="TEST", turn_id=1))
    handler._response.committed = True
    
    handler._initialized = True
    handler._outbound_queue = asyncio.Queue()
    handler._playback_until = 0.0
    return handler

async def _run_writer_briefly(handler, seconds: float = 0.05) -> asyncio.Task:
    task = asyncio.create_task(handler._outbound_writer())
    await asyncio.sleep(seconds)
    return task


@pytest.mark.asyncio
async def test_outbound_writer_drops_mp3_and_keeps_running(mock_speech_handler):
    """A non-PCM chunk is dropped (never streamed as static) and — unlike the
    old `assert` — the writer survives, so the call does not go silent."""
    mp3_header = b"\xff\xfb\x90\x44" + b"\x00" * 3196
    clean_pcm = b"\x01\x00" * 1600
    mock_speech_handler._outbound_queue.put_nowait((mp3_header, mock_speech_handler._response))
    mock_speech_handler._outbound_queue.put_nowait((clean_pcm, mock_speech_handler._response))

    task = await _run_writer_briefly(mock_speech_handler)
    assert not task.done()  # still alive
    task.cancel()
    mock_speech_handler._send_audio.assert_called_once_with(clean_pcm)


@pytest.mark.asyncio
async def test_outbound_writer_drops_wav(mock_speech_handler):
    wav_header = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 3180
    mock_speech_handler._outbound_queue.put_nowait((wav_header, mock_speech_handler._response))

    task = await _run_writer_briefly(mock_speech_handler)
    assert not task.done()
    task.cancel()
    mock_speech_handler._send_audio.assert_not_called()


@pytest.mark.asyncio
async def test_outbound_writer_trims_odd_bytes(mock_speech_handler):
    odd_bytes = b"\x00" * 3199
    mock_speech_handler._outbound_queue.put_nowait((odd_bytes, mock_speech_handler._response))

    task = await _run_writer_briefly(mock_speech_handler)
    task.cancel()
    mock_speech_handler._send_audio.assert_called_once_with(b"\x00" * 3198)


@pytest.mark.asyncio
async def test_outbound_writer_ignores_stale_response(mock_speech_handler):
    # Stale response has a different identity than mock_speech_handler._response
    from app.voice.speech_handler import _Response
    stale = _Response(turn_id=0, kind="greeting", metrics=TurnMetrics(call_sid="TEST", turn_id=0))
    stale.committed = True
    
    clean_pcm = b"\x00" * 3200
    mock_speech_handler._outbound_queue.put_nowait((clean_pcm, stale))
    
    # Cancel the writer so it doesn't wait forever on the next get
    task = asyncio.create_task(mock_speech_handler._outbound_writer())
    await asyncio.sleep(0.01)
    task.cancel()
    
    # send_audio should NOT be called for stale turn
    mock_speech_handler._send_audio.assert_not_called()

@pytest.mark.asyncio
async def test_outbound_writer_sends_clean_pcm(mock_speech_handler):
    clean_pcm = b"\x00" * 3200 # 200ms
    mock_speech_handler._outbound_queue.put_nowait((clean_pcm, mock_speech_handler._response))
    
    task = asyncio.create_task(mock_speech_handler._outbound_writer())
    await asyncio.sleep(0.05)
    task.cancel()
    
    mock_speech_handler._send_audio.assert_called_once_with(clean_pcm)
    assert mock_speech_handler._playback_until > 0.0
