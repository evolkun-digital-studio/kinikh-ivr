import asyncio
from unittest.mock import AsyncMock, patch

from app.core.config import Settings
from app.voice.speech_handler import SpeechHandler
import struct

def _loud_audio(num_samples: int = 2000) -> bytes:
    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(num_samples))

async def main():
    settings = Settings()
    mock_db = AsyncMock()
    mock_voice = AsyncMock()
    mock_voice.transcribe = AsyncMock(return_value="Hi")
    mock_voice.synthesize = AsyncMock(return_value=b"FAKE_AUDIO")
    mock_ai = AsyncMock()
    mock_ai.chat = AsyncMock(return_value="Reply")
    
    handler = SpeechHandler(db=mock_db, voice=mock_voice, ai_client=mock_ai)
    handler.set_audio_sender(AsyncMock())
    
    from app.services.department_service import DepartmentService
    with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
        await handler._ensure_initialized("CA_TEST", "+91000")
        
    await asyncio.sleep(0.3)
    
    seg = handler._buffer._segmenter
    print("Pre-push states:")
    print("is_active:", seg._active)
    print("active_streak:", seg._active_streak)
    print("active_gap:", seg._active_gap)
    print("silence_streak:", seg._silence_streak)
    print("start_frames:", seg._speech_start_frames)
    
    original_process = seg.process
    def new_process(chunk):
        print("Calling process with", len(chunk), "bytes")
        res = original_process(chunk)
        print("After process: is_active=", seg._active, "speech_started=", res.speech_started)
        return res
    seg.process = new_process
    
    print(f"Pushing {len(_loud_audio())} bytes...")
    await handler.handle_audio(_loud_audio())
    await handler._buffer.drain()
    
    print("Transcribe calls:", mock_voice.transcribe.call_count)

if __name__ == "__main__":
    asyncio.run(main())
