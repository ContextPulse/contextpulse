"""Tests for the recorder module — audio capture."""

import io
import threading
import time
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest
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


class _FakeInputStream:
    """Test double for sd.InputStream that records open/close pairs and
    lets a test manually fire the audio callback, simulating PortAudio's
    native audio thread.

    Regression fixture for the overlapping-PortAudio-stream crash: a
    re-entrant Recorder.start() used to overwrite self._stream without
    closing the prior one, leaving its native callback thread alive to
    keep appending into self._frames concurrently with the reassignment.
    """

    instances: list["_FakeInputStream"] = []

    def __init__(self, *, samplerate, channels, dtype, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.callback = callback
        self.started = 0
        self.stopped = 0
        self.closed = 0
        _FakeInputStream.instances.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed += 1

    def fire(self, marker: int) -> None:
        """Simulate the native audio thread invoking sounddevice's callback
        with a frame whose value encodes ``marker`` so tests can tell which
        stream produced which frame."""
        frame = np.full((1, 1), marker, dtype=np.int16)
        self.callback(frame, 1, None, False)


class TestRecorderOverlappingStreams:
    """Regression tests for the overlapping-PortAudio-stream crash.

    Evidence (2026-08-21): two hard native crashes (0xc0000005 access
    violation in python314.dll) both preceded by a duplicate-stop-recording
    warning within <0.5s — a re-entrant start() opened a second stream
    while the first was still being torn down by the background
    stop/transcribe thread.
    """

    @pytest.fixture(autouse=True)
    def _reset_instances(self):
        _FakeInputStream.instances.clear()
        yield
        _FakeInputStream.instances.clear()

    def _install_fake(self, monkeypatch):
        import contextpulse_voice.recorder as rec_mod
        monkeypatch.setattr(rec_mod.sd, "InputStream", _FakeInputStream)

    def test_reentrant_start_closes_prior_stream_exactly_once(self, monkeypatch):
        self._install_fake(monkeypatch)
        r = Recorder()

        r.start()
        stream1 = _FakeInputStream.instances[-1]
        assert stream1.stopped == 0
        assert stream1.closed == 0

        # Re-entrant start() WITHOUT an intervening stop() — simulates a
        # hotkey re-press racing the background stop/transcribe thread.
        r.start()
        stream2 = _FakeInputStream.instances[-1]

        assert stream1 is not stream2
        assert stream1.stopped == 1, "orphaned stream must be stopped exactly once"
        assert stream1.closed == 1, "orphaned stream must be closed exactly once"
        # The new stream must still be open (not closed by its own start()).
        assert stream2.stopped == 0
        assert stream2.closed == 0

        r.stop()
        assert stream2.stopped == 1
        assert stream2.closed == 1

    def test_many_reentrant_starts_every_stream_closed_exactly_once(self, monkeypatch):
        self._install_fake(monkeypatch)
        r = Recorder()

        for _ in range(10):
            r.start()

        streams = list(_FakeInputStream.instances)
        r.stop()

        for s in streams:
            assert s.stopped == 1, f"stream stopped {s.stopped} times, expected 1"
            assert s.closed == 1, f"stream closed {s.closed} times, expected 1"

    def test_orphaned_stream_frames_do_not_leak_into_new_buffer(self, monkeypatch):
        """No frames from an orphaned stream may leak into the next
        recording's buffer — even if its native callback fires AFTER a
        newer start() has already reassigned self._frames."""
        self._install_fake(monkeypatch)
        r = Recorder()

        r.start()
        stream1 = _FakeInputStream.instances[-1]
        stream1.fire(marker=1)
        assert [int(f[0, 0]) for f in r._frames] == [1]

        r.start()  # orphans stream1 (closes it, but its callback can still fire)
        stream2 = _FakeInputStream.instances[-1]

        # Simulate PortAudio's native thread delivering one more callback
        # from the now-orphaned stream1 before its teardown is observed.
        stream1.fire(marker=1)
        assert all(int(f[0, 0]) != 1 for f in r._frames), (
            "orphaned stream's frame leaked into the new recording's buffer"
        )

        stream2.fire(marker=2)
        assert [int(f[0, 0]) for f in r._frames] == [2]

        wav = r.stop()
        assert len(wav) > 0

    def test_concurrent_start_races_background_stop_after_silence(self, monkeypatch):
        """Real-thread regression: a background stop/transcribe thread is
        still inside stop_after_silence() (per VoiceModule's tail-buffer
        delay) when a hotkey re-press calls start() again. No stream may be
        orphaned and no exception may propagate."""
        self._install_fake(monkeypatch)
        r = Recorder()

        r.start()
        stream1 = _FakeInputStream.instances[-1]
        # Silent frame so stop_after_silence's loop can exit on its own.
        stream1.fire(marker=1)

        errors: list[BaseException] = []
        stop_result: list[bytes] = []

        def _background_stop():
            try:
                stop_result.append(r.stop_after_silence())
            except BaseException as exc:  # noqa: BLE001 — test must see any failure
                errors.append(exc)

        t = threading.Thread(target=_background_stop, daemon=True)
        t.start()
        time.sleep(0.02)  # let the background thread enter its polling loop
        r.start()  # re-entrant start() racing the background stop
        stream2 = _FakeInputStream.instances[-1]
        t.join(timeout=5)

        assert not t.is_alive(), "background stop_after_silence() thread hung"
        assert not errors, f"stop_after_silence raised: {errors}"

        # Exactly one of the two streams was torn down by the background
        # thread's own stop_after_silence() call, and stream1 was also
        # defensively torn down by the re-entrant start() -- either way,
        # nothing is left open twice-orphaned or double-closed.
        assert stream1.stopped >= 1
        assert stream1.closed >= 1
        assert stream1.stopped == stream1.closed, "stream1 stop/close call count mismatch"

        r.stop()
        assert stream2.stopped == 1
        assert stream2.closed == 1
