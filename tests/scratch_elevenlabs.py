import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.voice.elevenlabs import ElevenLabsClient

async def check():
    client = ElevenLabsClient()
    print(f"Model: {client._model_id}")
    print(f"Output Format: {client._OUTPUT_FORMAT}")
    
    try:
        iterator = client.synthesize_stream("Hello world, this is a test.", "en")
        first_chunk = await iterator.__anext__()
        print(f"Received chunk of type {type(first_chunk)} with size {len(first_chunk)}")
        print(f"First 10 bytes: {first_chunk[:10].hex()}")
        
        if first_chunk[:4] == b'\xff\xfb' or first_chunk[:3] == b'ID3':
            print("WARNING: This is an MP3 file!")
        elif first_chunk[:4] == b'RIFF':
            print("WARNING: This is a WAV file!")
        else:
            print("Looks like raw PCM.")
            
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(check())
