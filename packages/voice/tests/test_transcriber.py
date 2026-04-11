"""Tests for the transcriber module — model profiles and quality thresholds.

Ensures that model-specific threshold profiles are correctly applied
and that changing models doesn't silently degrade transcription quality.
"""

from unittest.mock import MagicMock, patch

import pytest

from contextpulse_voice.transcriber import (
    LocalTranscriber,
    _DEFAULT_THRESHOLDS,
    _MODEL_THRESHOLDS,
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
