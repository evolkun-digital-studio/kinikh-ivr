"""PCM16 helpers for the telephone audio path.

Exotel's Voicebot/AgentStream media stream carries raw signed 16-bit
little-endian mono PCM at 8 kHz. TTS providers return higher sample rates
(ElevenLabs pcm_16000, OpenAI pcm = 24 kHz), so their audio must be
down-sampled before it goes onto the phone line.

The previous implementation down-sampled by *dropping every other sample*
with no low-pass filter. Everything between 4 kHz and 8 kHz in the TTS
audio (sibilants — "s", "sh", "f", breath) then folds back into the audible
band as aliasing: a harsh, hissy, metallic "noise" on every assistant
sentence. `Downsampler` fixes that with a proper windowed-sinc anti-aliasing
low-pass filter (cut-off just under the telephone band's 3.4-4 kHz edge)
before decimation. It is streaming-safe: filter history and decimation
phase are carried across chunks, so chunk boundaries never click.
"""

from __future__ import annotations

import numpy as np

TELEPHONY_SAMPLE_RATE = 8000
_PCM_DTYPE = np.dtype("<i2")
_MP3_SYNC = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def _lowpass_taps(input_rate: int, cutoff_hz: float, num_taps: int) -> np.ndarray:
    """Windowed-sinc (Blackman) FIR low-pass, unity DC gain."""
    n = np.arange(num_taps) - (num_taps - 1) / 2
    fc = cutoff_hz / input_rate  # normalised cut-off (cycles/sample)
    taps = 2 * fc * np.sinc(2 * fc * n) * np.blackman(num_taps)
    return taps / taps.sum()


class Downsampler:
    """Streaming integer-factor PCM16 down-sampler with anti-aliasing.

    Supports any input rate that is an integer multiple of the output rate
    (16000 -> 8000, 24000 -> 8000, 48000 -> 8000). Feed arbitrary-length
    byte chunks to `process()`; an odd trailing byte is held until the next
    chunk so samples are never split. Call `flush()` once at the end.
    """

    def __init__(
        self,
        input_rate: int,
        output_rate: int = TELEPHONY_SAMPLE_RATE,
        num_taps: int = 63,
    ) -> None:
        if input_rate % output_rate:
            raise ValueError(f"unsupported resample {input_rate} -> {output_rate}")
        self._factor = input_rate // output_rate
        self._passthrough = self._factor == 1
        # Cut off at 3.6 kHz (telephone band) — comfortably below the 4 kHz
        # Nyquist limit of the 8 kHz output, so nothing aliases.
        self._taps = _lowpass_taps(input_rate, min(3600.0, output_rate * 0.45), num_taps)
        self._history = np.zeros(num_taps - 1, dtype=np.float64)
        self._phase = 0  # index (into the next filtered block) of the next kept sample
        self._odd_byte = b""

    def process(self, chunk: bytes) -> bytes:
        data = self._odd_byte + chunk
        usable = len(data) - (len(data) % 2)
        self._odd_byte = data[usable:]
        if not usable:
            return b""
        if self._passthrough:
            return data[:usable]
        samples = np.frombuffer(data[:usable], dtype=_PCM_DTYPE).astype(np.float64)
        padded = np.concatenate([self._history, samples])
        filtered = np.convolve(padded, self._taps, mode="valid")  # len == len(samples)
        self._history = padded[-(len(self._taps) - 1) :]
        kept = filtered[self._phase :: self._factor]
        self._phase = (self._phase - len(samples)) % self._factor
        return _to_pcm16(kept)

    def flush(self) -> bytes:
        """Push the filter's tail through so the last few ms are not lost."""
        if self._passthrough:
            self._odd_byte = b""
            return b""
        tail = self.process(b"\x00\x00" * ((len(self._taps) - 1) // 2))
        self._odd_byte = b""
        return tail


def _to_pcm16(samples: np.ndarray) -> bytes:
    return np.clip(np.round(samples), -32768, 32767).astype(_PCM_DTYPE).tobytes()


def downsample(pcm: bytes, input_rate: int, output_rate: int = TELEPHONY_SAMPLE_RATE) -> bytes:
    """One-shot convenience wrapper around Downsampler."""
    resampler = Downsampler(input_rate, output_rate)
    return resampler.process(pcm) + resampler.flush()


def apply_fade(pcm: bytes, *, fade_in: bool, fade_out: bool, sample_rate: int = 8000) -> bytes:
    """Short (8 ms) linear fade at the edges of a TTS segment.

    Each sentence is a separate TTS request; if one starts or ends on a
    non-zero sample the listener hears a click/pop at the boundary. A tiny
    fade removes it without being audible as a fade.
    """
    if not pcm or not (fade_in or fade_out):
        return pcm
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=_PCM_DTYPE).astype(np.float64)
    n = min(len(samples), int(sample_rate * 0.008))
    if n == 0:
        return pcm
    ramp = np.linspace(0.0, 1.0, n, endpoint=False)
    if fade_in:
        samples[:n] *= ramp
    if fade_out:
        samples[-n:] *= ramp[::-1]
    return _to_pcm16(samples)


def sanitize_pcm16(audio: bytes) -> bytes:
    """Make a chunk safe to hand to Exotel: whole samples only, never a
    container/compressed header. Returns b"" for audio that is clearly not
    raw PCM (e.g. MP3/WAV bytes) instead of streaming it as static."""
    if not audio:
        return b""
    head = audio[:4]
    if head[:3] == b"ID3" or head in (b"RIFF", b"WAVE") or head[:2] in _MP3_SYNC:
        return b""
    return audio[: len(audio) - (len(audio) % 2)]
