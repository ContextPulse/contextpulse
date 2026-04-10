"""Audio pipeline tests — synthetic audio through recorder and WAV validation.

Tests the recorder with deterministic numpy arrays to verify the audio
capture pipeline produces valid WAV data without requiring a real microphone.
"""

import io
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest
from contextpulse_voice.recorder import CHANNELS, SAMPLE_RATE, Recorder


class TestSyntheticAudioCapture:
    """Feed synthetic numpy arrays through the recorder callback."""

    def test_one_second_of_audio_produces_valid_wav(self):
        """100 callbacks of 160 samples = 16000 samples = 1 second at 16kHz."""
        r = Recorder()
        for _ in range(100):
            frame = np.zeros((160, 1), dtype=np.int16)
            r._callback(frame, 160, None, MagicMock(return_value=False))

        wav_bytes = r._to_wav()
        assert len(wav_bytes) > 44, "WAV must be larger than header (44 bytes)"

        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        assert wf.getframerate() == SAMPLE_RATE
        assert wf.getnchannels() == CHANNELS
        assert wf.getsampwidth() == 2  # 16-bit
        total_frames = wf.getnframes()
        assert total_frames == 16000, f"Expected 16000 frames, got {total_frames}"
        wf.close()

    def test_noisy_audio_produces_valid_wav(self):
        """Random noise should produce valid WAV (Whisper handles the content)."""
        r = Recorder()
        rng = np.random.default_rng(42)  # deterministic
        for _ in range(100):
            frame = rng.integers(-32768, 32767, size=(160, 1), dtype=np.int16)
            r._callback(frame, 160, None, MagicMock(return_value=False))

        wav_bytes = r._to_wav()
        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        assert wf.getnframes() == 16000
        wf.close()

    def test_sine_wave_audio(self):
        """A 440Hz sine wave — clean test signal."""
        r = Recorder()
        t = np.arange(16000) / 16000.0
        samples = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        # Feed in chunks of 160
        for i in range(0, 16000, 160):
            chunk = samples[i : i + 160].reshape(-1, 1)
            r._callback(chunk, 160, None, MagicMock(return_value=False))

        wav_bytes = r._to_wav()
        assert len(wav_bytes) > 0
        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        assert wf.getnframes() == 16000
        wf.close()

    def test_empty_recorder_returns_empty(self):
        r = Recorder()
        assert r._to_wav() == b""

    def test_stop_clears_frames(self):
        """After stop(), internal frames must be cleared."""
        r = Recorder()
        r._frames = [np.zeros((160, 1), dtype=np.int16)]
        r._stream = None  # no real stream
        r.stop()
        assert r._frames == [], "Frames must be cleared after stop()"


class TestWavFormat:
    """WAV output format validation."""

    def test_wav_header_fields(self):
        r = Recorder()
        frame = np.zeros((160, 1), dtype=np.int16)
        r._callback(frame, 160, None, MagicMock(return_value=False))
        wav_bytes = r._to_wav()

        # Check RIFF header
        assert wav_bytes[:4] == b"RIFF", "WAV must start with RIFF"
        assert wav_bytes[8:12] == b"WAVE", "WAV must have WAVE format"

    def test_wav_is_mono_16bit_16khz(self):
        """Whisper expects mono, 16-bit, 16kHz."""
        r = Recorder()
        frame = np.zeros((320, 1), dtype=np.int16)
        r._callback(frame, 320, None, MagicMock(return_value=False))
        wav_bytes = r._to_wav()

        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        assert wf.getnchannels() == 1, "Must be mono"
        assert wf.getsampwidth() == 2, "Must be 16-bit (2 bytes)"
        assert wf.getframerate() == 16000, "Must be 16kHz"
        wf.close()


class TestCallbackStatusHandling:
    """Audio callback must handle status flags gracefully."""

    def test_callback_with_overflow_status(self):
        """Input overflow should log warning but still capture audio."""
        r = Recorder()
        status = MagicMock()
        status.__bool__ = lambda self: True  # truthy = there's a status
        frame = np.zeros((160, 1), dtype=np.int16)
        # Should not raise
        r._callback(frame, 160, None, status)
        assert len(r._frames) == 1, "Frame should still be captured on overflow"

    def test_callback_copies_data(self):
        """Callback must copy indata, not store a reference."""
        r = Recorder()
        frame = np.zeros((160, 1), dtype=np.int16)
        r._callback(frame, 160, None, MagicMock(return_value=False))
        # Mutate original
        frame[0] = 9999
        assert r._frames[0][0] == 0, "Stored frame must be a copy"
