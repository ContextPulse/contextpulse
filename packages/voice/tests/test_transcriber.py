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
        with patch("faster_whisper.WhisperModel"):
            with patch("contextpulse_voice.model_manager.get_model_path", return_value="fake"):
                t = LocalTranscriber(model_size="unknown-v99")
                assert t._thresholds == _DEFAULT_THRESHOLDS


class TestLocalTranscriberInit:
    """Transcriber must load the correct threshold profile on init."""

    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_base_model_thresholds(self, mock_model, mock_path):
        t = LocalTranscriber(model_size="base")
        assert t._thresholds == _MODEL_THRESHOLDS["base"]
        assert t._model_size == "base"

    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_small_model_thresholds(self, mock_model, mock_path):
        t = LocalTranscriber(model_size="small")
        assert t._thresholds == _MODEL_THRESHOLDS["small"]

    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_medium_model_thresholds(self, mock_model, mock_path):
        t = LocalTranscriber(model_size="medium")
        assert t._thresholds == _MODEL_THRESHOLDS["medium"]


class TestTranscribeUsesThresholds:
    """The transcribe() call must pass model-specific thresholds to Whisper."""

    @patch("contextpulse_voice.model_manager.get_model_path", return_value="fake")
    @patch("faster_whisper.WhisperModel")
    def test_thresholds_passed_to_whisper(self, MockModel, mock_path):
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

        # Verify the thresholds were passed to the Whisper model
        call_kwargs = mock_instance.transcribe.call_args
        assert call_kwargs.kwargs["log_prob_threshold"] == expected[0], (
            f"log_prob_threshold mismatch: expected {expected[0]}"
        )
        assert call_kwargs.kwargs["no_speech_threshold"] == expected[1], (
            f"no_speech_threshold mismatch: expected {expected[1]}"
        )
        assert call_kwargs.kwargs["compression_ratio_threshold"] == expected[2], (
            f"compression_ratio_threshold mismatch: expected {expected[2]}"
        )


class TestTailBuffer:
    """Trailing audio buffer must be applied off the listener thread."""

    def test_voice_module_has_tail_buffer_constant(self):
        from contextpulse_voice.voice_module import VoiceModule
        assert hasattr(VoiceModule, "_TAIL_BUFFER_MS")
        assert VoiceModule._TAIL_BUFFER_MS >= 200, (
            "Tail buffer should be >= 200ms to capture trailing speech"
        )
        assert VoiceModule._TAIL_BUFFER_MS <= 500, (
            "Tail buffer should be <= 500ms to avoid noticeable latency"
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
