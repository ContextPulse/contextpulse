"""Tests for the recorder module — audio capture."""

import io
import wave
from unittest.mock import MagicMock

import numpy as np
from contextpulse_voice.recorder import CHANNELS, SAMPLE_RATE, Recorder


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

    def test_warm_start_opens_and_closes_stream(self, monkeypatch):
        """warm_start must open + close a stream so the FIRST start() is fast.

        Regression for Bug: first-press hotkey delay (overlay didn't appear
        until key release because PortAudio device init blocked the keyboard
        hook thread on first use).
        """
        import contextpulse_voice.recorder as rec_mod

        mock_stream = MagicMock()
        mock_input_stream = MagicMock(return_value=mock_stream)
        monkeypatch.setattr(rec_mod.sd, "InputStream", mock_input_stream)

        r = Recorder()
        r.warm_start()

        # Stream was created, started, stopped, closed
        assert mock_input_stream.called
        mock_stream.start.assert_called_once()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        # Frames discarded so warm-up audio doesn't pollute first real recording
        assert r._frames == []
        # _stream attribute is NOT left set — start() will create a fresh one
        assert r._stream is None

    def test_warm_start_swallows_exceptions(self, monkeypatch):
        """warm_start must never raise — failure is logged + ignored."""
        import contextpulse_voice.recorder as rec_mod

        def _boom(*args, **kwargs):
            raise OSError("no audio device")
        monkeypatch.setattr(rec_mod.sd, "InputStream", _boom)

        r = Recorder()
        r.warm_start()  # must not raise
