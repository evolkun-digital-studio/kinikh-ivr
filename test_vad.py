import asyncio
from app.core.config import Settings
from app.voice.audio_stream import AudioBuffer

async def main():
    settings = Settings()
    buffer = AudioBuffer("CA_TEST", settings=settings)
    print("speech_start_ms:", settings.speech_start_ms)
    print("_speech_start_frames:", buffer._segmenter._speech_start_frames)

asyncio.run(main())
