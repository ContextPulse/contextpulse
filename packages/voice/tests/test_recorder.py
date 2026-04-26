"""Tests for the recorder module — audio capture."""

import io
import wave
from unittest.mock import MagicMock

import numpy as np
from contextpulse_voice.recorder import (
    _MAX_RECORDING_S,
    CHANNELS,
    SAMPLE_RATE,
    Recorder,
)


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


class TestRecordingDurationCap:
    """Audio is truncated to _MAX_RECORDING_S in _to_wav.

    Bounds transcribe time and protects against stuck-hotkey runaway
    recordings that could otherwise hold the GIL long enough to
    starve the pynput keyboard hook.
    """

    def test_short_recording_passes_through(self):
        """A 1-second recording is well under the cap and untouched."""
        r = Recorder()
        # 1 second of audio: 100 callbacks * 160 samples = 16000 samples
        for _ in range(100):
            r._frames.append(np.zeros((160, 1), dtype=np.int16))
        wav_bytes = r._to_wav()
        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        # 16000 samples / 16000 Hz = 1 second
        assert wf.getnframes() == 16000
        wf.close()

    def test_recording_exceeding_cap_is_truncated(self):
        """A 90-second recording is truncated to _MAX_RECORDING_S."""
        r = Recorder()
        # 90 seconds of audio at 16kHz, in 1600-sample chunks
        chunk_size = 1600
        total_chunks = (90 * SAMPLE_RATE) // chunk_size
        for _ in range(total_chunks):
            r._frames.append(np.zeros((chunk_size, 1), dtype=np.int16))
        wav_bytes = r._to_wav()
        wf = wave.open(io.BytesIO(wav_bytes), "rb")
        max_samples = int(_MAX_RECORDING_S * SAMPLE_RATE)
        assert wf.getnframes() == max_samples, (
            f"expected truncation to {max_samples} samples, got {wf.getnframes()}"
        )
        wf.close()

    def test_cap_logs_warning(self, caplog):
        """When truncation triggers, a WARNING is logged."""
        import logging

        r = Recorder()
        chunk_size = 1600
        total_chunks = (90 * SAMPLE_RATE) // chunk_size
        for _ in range(total_chunks):
            r._frames.append(np.zeros((chunk_size, 1), dtype=np.int16))
        with caplog.at_level(logging.WARNING):
            r._to_wav()
        assert any("truncating" in rec.message.lower() for rec in caplog.records), (
            "expected truncation warning"
        )
