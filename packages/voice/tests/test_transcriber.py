"""Tests for the transcriber module — model profiles and quality thresholds.

Ensures that model-specific threshold profiles are correctly applied
and that changing models doesn't silently degrade transcription quality.
"""

from unittest.mock import MagicMock, patch

from contextpulse_voice.transcriber import (
    _DEFAULT_THRESHOLDS,
    _MODEL_THRESHOLDS,
    LocalTranscriber,
    _segment_is_degenerate,
)


class TestModelThresholdProfiles:
    """Every known model size must have a threshold profile."""

    KNOWN_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

    def test_all_known_models_have_profiles(self):
        for model in self.KNOWN_MODELS:
            assert model in _MODEL_THRESHOLDS, (
                f"Model '{model}' missing from _MODEL_THRESHOLDS — "
                f"add a profile before shipping this model size"
            )

    def test_profiles_have_three_values(self):
        for model, thresholds in _MODEL_THRESHOLDS.items():
            assert len(thresholds) == 3, (
                f"Model '{model}' profile must have exactly 3 values: "
                f"(log_prob, no_speech, compression_ratio)"
            )

    def test_log_prob_thresholds_are_negative(self):
        for model, (log_prob, _, _) in _MODEL_THRESHOLDS.items():
            assert log_prob < 0, (
                f"Model '{model}' log_prob_threshold must be negative, got {log_prob}"
            )

    def test_no_speech_thresholds_in_range(self):
        for model, (_, no_speech, _) in _MODEL_THRESHOLDS.items():
            assert 0.0 < no_speech <= 1.0, (
                f"Model '{model}' no_speech_threshold must be (0, 1], got {no_speech}"
            )

    def test_compression_thresholds_positive(self):
        for model, (_, _, compression) in _MODEL_THRESHOLDS.items():
            assert compression > 1.0, (
                f"Model '{model}' compression_ratio_threshold must be >1.0, got {compression}"
            )

    def test_larger_models_have_wider_thresholds(self):
        """Larger models need more relaxed thresholds due to more variable scores."""
        ordered = ["tiny", "base", "small", "medium", "large-v3"]
        for i in range(len(ordered) - 1):
            smaller = ordered[i]
            larger = ordered[i + 1]
            s_log, s_ns, s_cr = _MODEL_THRESHOLDS[smaller]
            l_log, l_ns, l_cr = _MODEL_THRESHOLDS[larger]
            # log_prob: more negative = more relaxed
            assert l_log <= s_log, (
                f"'{larger}' log_prob ({l_log}) should be <= '{smaller}' ({s_log}) — "
                f"larger models need more relaxed log_prob thresholds"
            )
            # no_speech: higher = more relaxed
            assert l_ns >= s_ns, (
                f"'{larger}' no_speech ({l_ns}) should be >= '{smaller}' ({s_ns}) — "
                f"larger models need more relaxed no_speech thresholds"
            )
            # compression: higher = more relaxed
            assert l_cr >= s_cr, (
                f"'{larger}' compression ({l_cr}) should be >= '{smaller}' ({s_cr}) — "
                f"larger models need more relaxed compression thresholds"
            )

    def test_default_thresholds_exist(self):
        assert len(_DEFAULT_THRESHOLDS) == 3
        assert _DEFAULT_THRESHOLDS[0] < 0  # log_prob
        assert 0.0 < _DEFAULT_THRESHOLDS[1] <= 1.0  # no_speech
        assert _DEFAULT_THRESHOLDS[2] > 1.0  # compression

    def test_unknown_model_uses_default(self):
        """A model not in the profile table should get safe defaults."""
        with patch("faster_whisper.WhisperModel"), \
             patch("contextpulse_voice.model_manager.get_model_path", return_value="fake"), \
             patch("contextpulse_voice.transcriber.sys") as mock_sys:
            mock_sys.platform = "linux"  # avoid mlx_whisper import on macOS CI
            t = LocalTranscriber(model_size="unknown-v99")
            assert t._thresholds == _DEFAULT_THRESHOLDS


class TestLocalTranscriberInit:
    """Transcriber must load the correct threshold profile on init."""

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_base_model_thresholds(self, mock_model, mock_path, mock_sys):
        mock_sys.platform = "linux"  # avoid mlx_whisper import on macOS CI
        t = LocalTranscriber(model_size="base")
        assert t._thresholds == _MODEL_THRESHOLDS["base"]
        assert t._model_size == "base"

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_small_model_thresholds(self, mock_model, mock_path, mock_sys):
        mock_sys.platform = "linux"  # avoid mlx_whisper import on macOS CI
        t = LocalTranscriber(model_size="small")
        assert t._thresholds == _MODEL_THRESHOLDS["small"]

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_medium_model_thresholds(self, mock_model, mock_path, mock_sys):
        mock_sys.platform = "linux"  # avoid mlx_whisper import on macOS CI
        t = LocalTranscriber(model_size="medium")
        assert t._thresholds == _MODEL_THRESHOLDS["medium"]


class TestMlxWhisperMissingOnAppleSilicon:
    """Regression tests for the README's macOS 'Full support' claim: a Mac
    user who installs without the `[macos]` extra must get an actionable
    error, not a bare crash on the first dictation attempt.
    (cp-public-readme-overclaims-macos)"""

    @patch("platform.machine", return_value="arm64")
    @patch("contextpulse_voice.transcriber.sys")
    def test_missing_mlx_whisper_raises_actionable_runtime_error(self, mock_sys, mock_machine):
        """mlx_whisper genuinely is not installed on this (non-Mac) test
        machine, so this exercises the real ImportError path, not a stub."""
        import pytest

        mock_sys.platform = "darwin"
        with pytest.raises(RuntimeError, match=r"mlx-whisper.*not installed.*packages/voice\[macos\]"):
            LocalTranscriber(model_size="base")

    @patch("platform.machine", return_value="arm64")
    @patch("contextpulse_voice.transcriber.sys")
    def test_mlx_whisper_present_selects_mlx_backend(self, mock_sys, mock_machine):
        """When mlx_whisper IS importable, init must succeed and select the
        mlx backend -- proves the try/except doesn't swallow the success path."""
        import sys as real_sys
        import types

        mock_sys.platform = "darwin"
        fake_mlx = types.ModuleType("mlx_whisper")
        real_sys.modules["mlx_whisper"] = fake_mlx
        try:
            t = LocalTranscriber(model_size="base")
            assert t._backend == "mlx"
            assert t._mlx_whisper is fake_mlx
        finally:
            del real_sys.modules["mlx_whisper"]


class TestTranscribeUsesThresholds:
    """The transcribe() call must pass model-specific thresholds to Whisper."""

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_thresholds_passed_to_whisper(self, MockModel, mock_path, mock_sys):
        mock_sys.platform = "linux"  # avoid mlx_whisper import on macOS CI
        mock_instance = MagicMock()
        # Simulate Whisper returning one segment
        mock_seg = MagicMock()
        mock_seg.text = "Hello world"
        mock_seg.compression_ratio = 1.0  # realistic value — see TestDegenerateOutputGuard
        mock_info = MagicMock()
        mock_info.duration = 1.5
        mock_info.language = "en"
        mock_instance.transcribe.return_value = ([mock_seg], mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        expected = _MODEL_THRESHOLDS["small"]

        # Create minimal WAV bytes
        import io
        import wave

        import numpy as np
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(np.zeros(1600, dtype=np.int16).tobytes())
        wav_bytes = buf.getvalue()

        t.transcribe(wav_bytes)

        # Quality filters are disabled for dictation — log_prob and
        # compression_ratio should be None, only no_speech kept.
        call_kwargs = mock_instance.transcribe.call_args
        assert call_kwargs.kwargs["log_prob_threshold"] is None, (
            "log_prob_threshold should be None (disabled) for dictation"
        )
        assert call_kwargs.kwargs["no_speech_threshold"] == 0.95, (
            "no_speech_threshold should be 0.95 (only filter pure silence)"
        )
        assert call_kwargs.kwargs["compression_ratio_threshold"] is None, (
            "compression_ratio_threshold should be None (disabled) for dictation"
        )


class TestTailBuffer:
    """Trailing audio buffer must be applied off the listener thread."""

    def test_voice_module_has_tail_buffer_constant(self):
        from contextpulse_voice.voice_module import VoiceModule
        assert hasattr(VoiceModule, "_TAIL_BUFFER_MS")
        assert VoiceModule._TAIL_BUFFER_MS >= 200, (
            "Tail buffer should be >= 200ms to capture trailing speech"
        )
        assert VoiceModule._TAIL_BUFFER_MS <= 1000, (
            "Tail buffer should be <= 1000ms to avoid noticeable latency"
        )

    def test_recorder_stop_does_not_sleep(self):
        """Recorder.stop() must NOT sleep — tail buffer is in VoiceModule."""
        import inspect
        from contextpulse_voice.recorder import Recorder
        source = inspect.getsource(Recorder.stop)
        assert "sleep" not in source, (
            "Recorder.stop() must not sleep — sleeping in pynput callback "
            "blocks key events and causes runaway recording loops"
        )

    def test_stop_after_silence_exists(self):
        """Recorder must have stop_after_silence for energy-based tail."""
        from contextpulse_voice.recorder import Recorder
        assert hasattr(Recorder, "stop_after_silence"), (
            "Recorder must have stop_after_silence method"
        )

    def test_stop_after_silence_no_stream(self):
        """stop_after_silence with no active stream returns empty WAV."""
        from contextpulse_voice.recorder import Recorder
        r = Recorder()
        result = r.stop_after_silence()
        assert result == b"", "Should return empty bytes when no stream"

    def test_stop_after_silence_constants_reasonable(self):
        """Silence detection constants must be in sane ranges."""
        from contextpulse_voice.recorder import (
            _MAX_TAIL_S,
            _SILENCE_DURATION_S,
            _SILENCE_THRESHOLD_RMS,
        )
        assert 0.3 <= _SILENCE_DURATION_S <= 2.0, (
            f"Silence duration {_SILENCE_DURATION_S}s out of range"
        )
        assert 1.0 <= _MAX_TAIL_S <= 5.0, (
            f"Max tail {_MAX_TAIL_S}s out of range"
        )
        assert 50 <= _SILENCE_THRESHOLD_RMS <= 1000, (
            f"Silence RMS threshold {_SILENCE_THRESHOLD_RMS} out of range"
        )

    def test_stop_after_silence_with_silent_frames(self):
        """stop_after_silence should exit quickly when frames are silent."""
        import time

        import numpy as np
        from contextpulse_voice.recorder import Recorder

        r = Recorder()
        # Simulate: stream is "active" but frames are silent
        r._stream = True  # truthy stub — stop_after_silence checks `is None`
        # Add silent frames (all zeros = RMS 0)
        for _ in range(10):
            r._frames.append(np.zeros(480, dtype=np.int16))

        start = time.monotonic()
        # Monkey-patch stream stop/close to no-op
        class FakeStream:
            def stop(self): pass
            def close(self): pass
        r._stream = FakeStream()
        result = r.stop_after_silence()
        elapsed = time.monotonic() - start

        assert len(result) > 0, "Should return WAV bytes from silent frames"
        assert elapsed < 2.0, (
            f"Should detect silence quickly, took {elapsed:.1f}s"
        )


def _mock_segment(
    text: str,
    start: float = 0.0,
    end: float = 1.0,
    avg_logprob: float = -0.3,
    no_speech_prob: float = 0.05,
    compression_ratio: float = 1.0,
):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.avg_logprob = avg_logprob
    seg.no_speech_prob = no_speech_prob
    seg.compression_ratio = compression_ratio
    return seg


class TestDegenerateOutputGuard:
    """Post-transcription guard that drops individual degenerate
    (repetition-runaway) segments while keeping legitimate ones.

    Whisper's own log_prob/compression_ratio filters stay disabled during
    transcribe() -- this guard runs AFTER each segment is produced and
    filters PER-SEGMENT, not the whole transcript: a live repro showed a
    degenerate segment arriving MIXED WITH legitimate speech in the same
    43.7s clip, so an all-or-nothing reject would have discarded real
    speech -- exactly the truncation failure the disabled Whisper filters
    exist to avoid.

    Real log data (2026-08-21, C:\\Users\\david\\screenshots\\contextpulse.log):
      06:18:43 [0.0-30.0s]  logprob=-0.06 no_speech=0.41 cr=18.6
               'Order, Sales, Shading, Shading, Shading, Shading, ...'
      06:18:44 [30.0-35.0s] logprob=-0.21 no_speech=0.01 cr=1.1
               'exactly where the price currently is.'
      06:18:44 [35.9-40.9s] logprob=-0.21 no_speech=0.01 cr=1.1
               'And then also another one of those for where BWAP is.'
      05:57:23 single 2.5s segment, cr=19.5, "GIF, GIF, GIF, GIF..."
      06:17:46 full-session high-water mark for legitimate speech: cr=1.5

    log_prob is explicitly NOT used as a signal: the 06:18:43 degenerate
    segment had the BEST confidence (-0.06) of the whole session while
    genuinely repeating -- Whisper is highly confident in its own
    repetition loops. compression_ratio is the discriminator, using the
    existing per-model profile threshold directly (no separate constant).
    """

    def test_rejects_degenerate_ratio_from_shading_incident(self):
        assert _segment_is_degenerate(18.6, 5.0) is True

    def test_rejects_degenerate_ratio_from_gif_incident(self):
        assert _segment_is_degenerate(19.5, 5.0) is True

    def test_accepts_session_high_water_mark_for_legitimate_speech(self):
        assert _segment_is_degenerate(1.5, 5.0) is False

    def test_boundary_exactly_at_threshold_is_not_rejected(self):
        """The bar is a strict '>', not '>=' -- landing exactly on the
        profile threshold must not discard real speech."""
        assert _segment_is_degenerate(5.0, 5.0) is False
        assert _segment_is_degenerate(5.01, 5.0) is True

    def test_zero_ratio_never_rejected(self):
        assert _segment_is_degenerate(0.0, 5.0) is False

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_mixed_clip_drops_only_the_degenerate_segment(
        self, MockModel, mock_path, mock_sys
    ):
        """Real repro shape (2026-08-21 06:18:43): one degenerate segment
        followed by two legitimate ones in the SAME 43.7s clip. Only the
        degenerate segment is dropped; the legitimate two survive, joined
        in order."""
        mock_sys.platform = "linux"
        mock_instance = MagicMock()
        segs = [
            _mock_segment(
                "Order, Sales, Shading, Shading, Shading, Shading, Shading, S",
                start=0.0, end=30.0, avg_logprob=-0.06, no_speech_prob=0.41,
                compression_ratio=18.6,
            ),
            _mock_segment(
                "exactly where the price currently is.",
                start=30.0, end=35.0, avg_logprob=-0.21, no_speech_prob=0.01,
                compression_ratio=1.1,
            ),
            _mock_segment(
                "And then also another one of those for where BWAP is.",
                start=35.9, end=40.9, avg_logprob=-0.21, no_speech_prob=0.01,
                compression_ratio=1.1,
            ),
        ]
        mock_info = MagicMock(duration=43.7, language="en")
        mock_instance.transcribe.return_value = (segs, mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        result = t.transcribe(b"fake_wav_bytes")

        assert "Shading" not in result, "degenerate segment must be dropped"
        assert result == (
            "exactly where the price currently is. "
            "And then also another one of those for where BWAP is."
        ), "legitimate segments must survive and stay in order"

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_all_degenerate_clip_returns_empty_nothing_to_paste(
        self, MockModel, mock_path, mock_sys
    ):
        """Real repro shape (2026-08-21 05:57:23): a single 2.5s segment,
        entirely degenerate. Result must be empty so VoiceModule's existing
        empty-transcript check (raw_text or len < 2) short-circuits before
        paste_text() is ever called -- nothing reaches the paster."""
        mock_sys.platform = "linux"
        mock_instance = MagicMock()
        seg = _mock_segment(
            "GIF, GIF, GIF, GIF, GIF, GIF.",
            start=0.0, end=2.5, avg_logprob=-0.4, no_speech_prob=0.52,
            compression_ratio=19.5,
        )
        mock_info = MagicMock(duration=2.5, language="nn")
        mock_instance.transcribe.return_value = ([seg], mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        result = t.transcribe(b"fake_wav_bytes")

        assert result == "", "all-degenerate clip must return empty, not the repetition"

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_ordinary_multi_sentence_dictation_passes_through_untouched(
        self, MockModel, mock_path, mock_sys
    ):
        """No degenerate segments at all -- every segment must reach the
        caller byte-for-byte, in order."""
        mock_sys.platform = "linux"
        mock_instance = MagicMock()
        segs = [
            _mock_segment(
                "I'm looking at the futures this morning.", compression_ratio=1.3
            ),
            _mock_segment(
                "Price action is choppy heading into the open.",
                compression_ratio=1.4,
            ),
        ]
        mock_info = MagicMock(duration=31.5, language="en")
        mock_instance.transcribe.return_value = (segs, mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        result = t.transcribe(b"fake_wav_bytes")

        assert result == (
            "I'm looking at the futures this morning. "
            "Price action is choppy heading into the open."
        )

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_empty_segments_never_rejected(self, MockModel, mock_path, mock_sys):
        """No segments (e.g. pure silence) must not trip the guard."""
        mock_sys.platform = "linux"
        mock_instance = MagicMock()
        mock_info = MagicMock(duration=0.5, language="en")
        mock_instance.transcribe.return_value = ([], mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        result = t.transcribe(b"fake_wav_bytes")

        assert result == ""

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_dropped_segment_is_logged_loudly_not_swallowed(
        self, MockModel, mock_path, mock_sys, caplog
    ):
        """A dropped segment must be visible in the log at WARNING with its
        compression ratio, timespan, and a preview -- never a silent drop."""
        import logging

        mock_sys.platform = "linux"
        mock_instance = MagicMock()
        seg = _mock_segment(
            "Shading, Shading, Shading.",
            start=0.0, end=30.0, compression_ratio=18.6,
        )
        mock_info = MagicMock(duration=30.0, language="en")
        mock_instance.transcribe.return_value = ([seg], mock_info)
        MockModel.return_value = mock_instance

        t = LocalTranscriber(model_size="small")
        with caplog.at_level(logging.WARNING, logger="contextpulse_voice.transcriber"):
            t.transcribe(b"fake_wav_bytes")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "a dropped segment must emit a WARNING log record"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "18.6" in combined
        assert "Shading" in combined

    @patch("contextpulse_voice.transcriber.sys")
    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_startup_log_does_not_advertise_unenforced_log_prob_filter(
        self, MockModel, mock_path, mock_sys, caplog
    ):
        """log_prob is not passed to Whisper's own filter and is not used
        by the post-hoc guard either (it is not a usable signal — see
        class docstring) -- the startup log must not claim it is enforced
        at the literal profile value."""
        import logging

        mock_sys.platform = "linux"
        MockModel.return_value = MagicMock()

        with caplog.at_level(logging.INFO, logger="contextpulse_voice.transcriber"):
            LocalTranscriber(model_size="small")

        messages = " ".join(r.message for r in caplog.records)
        assert "log_prob=-3.0" not in messages
        assert "disabled" in messages.lower()
        assert "compression_ratio_threshold=5.0" in messages
        assert "per-segment" in messages.lower()
