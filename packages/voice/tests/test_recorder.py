"""Tests for the recorder module — audio capture."""

import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from contextpulse_voice.recorder import Recorder, SAMPLE_RATE, CHANNELS


class TestRecorder:
    def test_init_defaults(self):
        r = Recorder()
        assert r.sample_rate == SAMPLE_RATE
        assert r.channels == CHANNELS
        assert r._frames == []
        assert r._stream is None

    def test_custom_params(self):
        r = Recorder(sample_rate=44100, channels=2)
        assert r.sample_rate == 44100
        assert r.channels == 2

    def test_to_wav_empty(self):
        r = Recorder()
        result = r._to_wav()
        assert result == b""

    def test_to_wav_with_frames(self):
        r = Recorder()
        # Simulate captured audio frames
        frame1 = np.zeros((160, 1), dtype=np.int16)
        frame2 = np.ones((160, 1), dtype=np.int16) * 100
        r._frames = [frame1, frame2]
        result = r._to_wav()
        assert len(result) > 0
        # Verify it's a valid WAV file
        wav_file = wave.open(io.BytesIO(result), "rb")
        assert wav_file.getnchannels() == CHANNELS
        assert wav_file.getframerate() == SAMPLE_RATE
        assert wav_file.getsampwidth() == 2
        wav_file.close()

    def test_callback_appends_frames(self):
        r = Recorder()
        data = np.zeros((160, 1), dtype=np.int16)
        r._callback(data, 160, None, MagicMock(return_value=False))
        assert len(r._frames) == 1

    def test_callback_copies_data(self):
        r = Recorder()
        data = np.zeros((160, 1), dtype=np.int16)
        r._callback(data, 160, None, MagicMock(return_value=False))
        # Modify original — stored copy should be unaffected
        data[0] = 999
        assert r._frames[0][0] == 0

    def test_stop_returns_wav(self):
        r = Recorder()
        r._frames = [np.zeros((160, 1), dtype=np.int16)]
        r._stream = MagicMock()
        result = r.stop()
        assert len(result) > 0
        assert r._stream is None

    def test_stop_without_start(self):
        r = Recorder()
        result = r.stop()
        assert result == b""
