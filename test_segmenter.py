import struct
import math
from app.voice.vad import UtteranceSegmenter, NoiseFloor

def _loud_audio(num_samples: int = 2000) -> bytes:
    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(num_samples))

segmenter = UtteranceSegmenter(
    frame_bytes=320,
    speech_start_ms=200,
    end_of_speech_ms=700,
    pre_roll_ms=250,
    max_utterance_bytes=320000,
    min_rms=150.0,
    noise_floor=NoiseFloor(multiplier=3.0, max_threshold=2000.0)
)
print("speech_start_frames:", segmenter._speech_start_frames)

event = segmenter.process(_loud_audio())
print("is_active:", segmenter.is_speech_active)
print("event.speech_started:", event.speech_started)
