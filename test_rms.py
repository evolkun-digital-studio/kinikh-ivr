import sys
import struct
import math
from app.voice.vad import compute_pcm16_rms

def _loud_audio(num_samples: int = 2000) -> bytes:
    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(num_samples))

audio = _loud_audio()
frame = audio[:320]
print(f"RMS: {compute_pcm16_rms(frame)}")
