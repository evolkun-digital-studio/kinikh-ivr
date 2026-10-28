"""Unit tests for app/voice/vad.py — the PCM16 energy-based voice-activity
gate that stops silence/line-noise/echo from ever reaching Whisper (see
SpeechHandler._on_audio_flushed)."""

from __future__ import annotations

import struct

from app.voice.vad import (
    BargeInDetector,
    UtteranceSegmenter,
    compute_pcm16_rms,
    detect_voice_activity,
    extract_wav_pcm_payload,
)


def _tone(amplitude: int, num_samples: int = 400) -> bytes:
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude) for i in range(num_samples)
    )


def _frame(active: bool, amplitude: int = 8000) -> bytes:
    """One 320-byte (20ms @ 8kHz) PCM16 frame — 160 samples."""
    return _tone(amplitude if active else 0, num_samples=160)


def _segmenter(**overrides) -> UtteranceSegmenter:
    params = {
        "frame_bytes": 320,
        "min_rms": 150.0,
        "speech_start_ms": 200,
        "end_of_speech_ms": 700,
        "pre_roll_ms": 250,
        "max_utterance_bytes": 320_000,
        "sample_rate": 8000,
    }
    params.update(overrides)
    return UtteranceSegmenter(**params)


class TestComputePcm16Rms:
    def test_silence_has_zero_rms(self):
        assert compute_pcm16_rms(b"\x00" * 800) == 0.0

    def test_constant_amplitude_tone_has_matching_rms(self):
        assert compute_pcm16_rms(_tone(1000)) == 1000.0

    def test_empty_bytes_has_zero_rms(self):
        assert compute_pcm16_rms(b"") == 0.0

    def test_odd_trailing_byte_is_ignored_not_an_error(self):
        # 5 bytes = 2 full samples + 1 stray byte; must not raise.
        compute_pcm16_rms(b"\x00\x00\x00\x00\xff")


class TestDetectVoiceActivity:
    def test_silence_is_not_speech(self):
        result = detect_voice_activity(b"\x00" * 800, min_rms=150.0)
        assert result.is_speech is False
        assert result.reason == "below_energy_threshold"
        assert result.rms == 0.0

    def test_low_energy_audio_is_not_speech(self):
        result = detect_voice_activity(_tone(20), min_rms=150.0)
        assert result.is_speech is False
        assert result.reason == "below_energy_threshold"

    def test_loud_audio_is_speech(self):
        result = detect_voice_activity(_tone(8000), min_rms=150.0)
        assert result.is_speech is True
        assert result.reason == "speech"
        assert result.rms == 8000.0

    def test_audio_exactly_at_threshold_counts_as_speech(self):
        """rms == min_rms is not "below" the threshold, so it passes — only
        rms strictly less than min_rms is discarded."""
        result = detect_voice_activity(_tone(150), min_rms=150.0)
        assert result.is_speech is True

    def test_audio_just_below_threshold_is_not_speech(self):
        result = detect_voice_activity(_tone(149), min_rms=150.0)
        assert result.is_speech is False

    def test_too_short_fragment_is_not_speech(self):
        result = detect_voice_activity(b"\x00\x01", min_rms=150.0)
        assert result.is_speech is False
        assert result.reason == "too_short"

    def test_custom_threshold_is_respected(self):
        quiet = _tone(50)
        assert detect_voice_activity(quiet, min_rms=150.0).is_speech is False
        assert detect_voice_activity(quiet, min_rms=10.0).is_speech is True


class TestExtractWavPcmPayload:
    def test_extracts_payload_from_real_wav_container(self):
        from app.voice.audio_stream import _wrap_pcm16_wav

        raw = _tone(8000)
        wav_bytes = _wrap_pcm16_wav(raw, sample_rate=8000)

        assert extract_wav_pcm_payload(wav_bytes) == raw

    def test_non_wav_bytes_returned_unchanged(self):
        raw = b"\x00" * 100
        assert extract_wav_pcm_payload(raw) == raw

    def test_empty_bytes_returned_unchanged(self):
        assert extract_wav_pcm_payload(b"") == b""

    def test_truncated_riff_header_is_treated_as_raw(self):
        # Starts like a WAV file but is too short to actually be one.
        assert extract_wav_pcm_payload(b"RIFF") == b"RIFF"


class TestUtteranceSegmenter:
    """Real-time frame-level turn detector — the primary replacement for
    the old fixed-buffer/silence-timer turn boundary. Covers natural
    telephone speech: brief onset dropouts must not lose the first
    syllable, and short natural pauses must not split one utterance into
    two, while a real, sustained silence must still end it promptly."""

    def test_confirms_speech_after_sustained_active_frames(self):
        seg = _segmenter()
        event = None
        for _ in range(10):  # speech_start_frames at 200ms/20ms
            event = seg.process(_frame(active=True))
        assert event.speech_started is True
        assert seg.is_speech_active is True

    def test_single_frame_dropout_during_onset_does_not_lose_first_word(self):
        """Regression: a real caller's first syllable was sometimes lost
        because one quiet ~20ms frame (a stop-consonant, a breath) reset
        the whole speech-start confirmation from zero. A single dip must
        not restart the count — the confirmed utterance must also still
        contain that dip's audio (nothing is dropped)."""
        seg = _segmenter()
        events = [seg.process(_frame(active=True)) for _ in range(5)]
        events.append(seg.process(_frame(active=False)))  # one brief dip
        events += [seg.process(_frame(active=True)) for _ in range(5)]

        assert not any(e.speech_started for e in events[:-1])
        assert events[-1].speech_started is True  # confirmed on the 10th active frame, not reset
        assert seg.is_speech_active is True

    def test_two_consecutive_dropouts_during_onset_resets_confirmation(self):
        """Tolerance is for brief blips only — a genuine gap (2+ frames,
        40ms+) must still restart confirmation, or truly separate sounds
        would get spliced together as one "utterance"."""
        seg = _segmenter()
        for _ in range(5):
            seg.process(_frame(active=True))
        seg.process(_frame(active=False))
        seg.process(_frame(active=False))  # second consecutive dip -> reset
        events = [seg.process(_frame(active=True)) for _ in range(5)]

        # Only 5 active frames accumulated since the reset — not enough
        # (10 required) to confirm yet.
        assert not any(e.speech_started for e in events)
        assert seg.is_speech_active is False

    def test_pre_roll_audio_is_included_in_confirmed_utterance(self):
        """The lead-in audio right before speech is confirmed must be kept
        — otherwise the first word would be clipped even though the caller
        started talking before the confirmation threshold was reached."""
        seg = _segmenter()
        lead_in = _frame(active=False, amplitude=0)
        seg.process(lead_in)
        seg.process(lead_in)
        for _ in range(10):
            seg.process(_frame(active=True))

        assert seg.is_speech_active is True
        # force_finalize returns everything captured so far, including pre-roll.
        captured = seg.force_finalize()
        assert lead_in in captured[:640]  # the two lead-in frames appear first

    def test_short_pause_mid_utterance_does_not_end_it(self):
        """ "मुझे... [short pause] ...billing department से बात करनी है" must
        remain ONE utterance — a pause well under end_of_speech_ms (700ms
        => 35 frames) must not finalize it."""
        seg = _segmenter()
        for _ in range(10):
            seg.process(_frame(active=True))
        assert seg.is_speech_active is True

        for _ in range(20):  # 400ms of pause — under the 700ms floor
            event = seg.process(_frame(active=False))
            assert not event.utterances

        for _ in range(10):
            event = seg.process(_frame(active=True))
            assert not event.utterances
        assert seg.is_speech_active is True  # still one continuous utterance

    def test_pause_longer_than_threshold_ends_utterance(self):
        seg = _segmenter()
        for _ in range(10):
            seg.process(_frame(active=True))

        finalized = []
        for _ in range(36):  # over the 35-frame/700ms floor
            finalized += seg.process(_frame(active=False)).utterances
        assert len(finalized) == 1
        assert seg.is_speech_active is False

    def test_max_utterance_bytes_forces_finalize(self):
        """Safety cap only — must still fire if speech genuinely never
        stops, to bound memory use."""
        seg = _segmenter(max_utterance_bytes=3200)
        finalized = []
        for _ in range(20):  # far more than fits in 3200 bytes
            finalized += seg.process(_frame(active=True)).utterances
        assert len(finalized) == 1

    def test_force_finalize_returns_none_when_not_active(self):
        seg = _segmenter()
        assert seg.force_finalize() is None

    def test_background_noise_never_confirms_speech(self):
        """Quiet, continuous line noise below the RMS floor must never
        confirm an utterance, no matter how long it goes on."""
        seg = _segmenter()
        quiet_frame = _tone(20, num_samples=160)  # well under min_rms=150
        for _ in range(200):
            event = seg.process(quiet_frame)
            assert not event.speech_started
        assert seg.is_speech_active is False


class TestBargeInDetectorSustainedActivity:
    """BargeInDetector already has integration-level coverage via
    SpeechHandler tests — these are the missing direct unit tests for its
    core sustained-activity confirmation."""

    def _detector(self, **overrides) -> BargeInDetector:
        params = {
            "frame_bytes": 320,
            "min_rms": 300.0,
            "min_active_ms": 300,
            "sample_rate": 8000,
        }
        params.update(overrides)
        return BargeInDetector(**params)

    def test_one_loud_frame_does_not_confirm_barge_in(self):
        det = self._detector()
        assert det.process(_frame(active=True)) is None

    def test_sustained_loud_frames_confirm_barge_in(self):
        det = self._detector()
        confirmed = None
        for _ in range(15):  # 300ms / 20ms
            confirmed = det.process(_frame(active=True))
        assert confirmed is not None
        assert len(confirmed) == 15 * 320

    def test_short_complete_caller_chunk_keeps_audio_after_confirmation(self):
        det = BargeInDetector(frame_bytes=320, min_rms=300, min_active_ms=200)
        speech = _frame(active=True) * 12
        silence = _frame(active=False) * 35
        confirmed = det.process(speech + silence)
        assert confirmed == speech + silence

    def test_silence_resets_the_streak(self):
        det = self._detector()
        for _ in range(10):
            det.process(_frame(active=True))
        det.process(_frame(active=False))
        confirmed = None
        for _ in range(10):  # only 10 more — not enough after the reset
            confirmed = det.process(_frame(active=True))
        assert confirmed is None


class TestBargeInDetectorSpeechLikeInput:
    def _detector(self, **overrides) -> BargeInDetector:
        params = {"frame_bytes": 320, "min_rms": 300.0, "min_active_ms": 200}
        params.update(overrides)
        return BargeInDetector(**params)

    def _syllables(self, count: int, on_frames: int = 6, off_frames: int = 1) -> bytes:
        return (_frame(active=True) * on_frames + _frame(active=False) * off_frames) * count

    def test_syllabic_speech_confirms_with_gap_tolerance(self):
        det = self._detector(gap_tolerance_ms=60)
        assert det.detect(self._syllables(3)) is not None

    def test_syllabic_speech_never_confirms_without_tolerance(self):
        """The pre-fix behaviour: one quiet frame between syllables reset the
        streak, so normal speech during playback was silently discarded."""
        det = self._detector()
        assert det.detect(self._syllables(10)) is None

    def test_sustained_silence_still_resets_despite_tolerance(self):
        det = self._detector(gap_tolerance_ms=60)
        audio = _frame(active=True) * 6 + _frame(active=False) * 5 + _frame(active=True) * 6
        assert det.detect(audio) is None

    def test_pre_roll_and_remainder_are_kept_separately(self):
        det = self._detector(pre_roll_ms=60)
        lead_in = _frame(active=False) * 5
        speech = _frame(active=True) * 10
        tail = _frame(active=True) * 2 + _frame(active=False) * 3
        result = det.detect(lead_in + speech + tail)
        assert result is not None
        assert result.speech == _frame(active=False) * 3 + speech  # 60ms pre-roll
        assert result.remainder == tail


class TestUtteranceSegmenterSeeding:
    def test_seeded_utterance_finalizes_on_following_silence(self):
        seg = _segmenter()
        seeded = _frame(active=True) * 12
        seg.begin_utterance(seeded)
        assert seg.is_speech_active
        event = seg.process(_frame(active=False) * 40)
        assert event.utterances and event.utterances[0].startswith(seeded)

    def test_discard_partial_drops_open_utterance(self):
        seg = _segmenter()
        seg.process(_frame(active=True) * 15)
        assert seg.is_speech_active
        seg.discard_partial()
        assert not seg.is_speech_active
        assert seg.force_finalize() is None
