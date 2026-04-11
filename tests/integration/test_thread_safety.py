"""Thread safety tests — catches callback blocking and thread leaks.

These tests prevent the class of bugs where blocking operations (sleep,
I/O, model loading) happen inside event listener callbacks, causing
event queues to back up and create runaway loops.
"""

import inspect

import pytest


class TestNoSleepInCallbacks:
    """Source inspection tests: callbacks must never block."""

    def test_no_sleep_in_voice_press_callback(self):
        """Sleeping in _on_press_inner blocks the pynput listener thread,
        causing key events to queue and replay in a burst."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_press_inner)
        assert "time.sleep" not in source, (
            "VoiceModule._on_press_inner must not call time.sleep() — "
            "blocking the pynput listener causes runaway recording loops. "
            "Move blocking work to a daemon thread."
        )

    def test_no_sleep_in_voice_release_callback(self):
        """Sleeping in _on_release_inner blocks the pynput listener thread."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_release_inner)
        assert "time.sleep" not in source, (
            "VoiceModule._on_release_inner must not call time.sleep() — "
            "blocking the pynput listener causes runaway recording loops."
        )

    def test_no_sleep_in_recorder_stop(self):
        """Recorder.stop() is called from the pynput listener via
        _stop_and_transcribe — but that's in a bg thread now. Verify
        stop() itself doesn't sleep (it used to, causing the runaway bug)."""
        from contextpulse_voice.recorder import Recorder

        source = inspect.getsource(Recorder.stop)
        assert "sleep" not in source, (
            "Recorder.stop() must not sleep — it may be called from contexts "
            "where sleeping blocks event processing."
        )

    def test_no_sleep_in_voice_on_press_wrapper(self):
        """The outer _on_press wrapper that catches exceptions."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_press)
        assert "time.sleep" not in source

    def test_no_sleep_in_voice_on_release_wrapper(self):
        """The outer _on_release wrapper that catches exceptions."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_release)
        assert "time.sleep" not in source


class TestCallbackExceptionSurvival:
    """Listener callbacks must swallow exceptions to stay alive."""

    def test_voice_press_handler_catches_exceptions(self):
        """_on_press wraps _on_press_inner in try/except."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_press)
        assert "except" in source, (
            "_on_press must catch exceptions to keep the pynput listener alive"
        )

    def test_voice_release_handler_catches_exceptions(self):
        """_on_release wraps _on_release_inner in try/except."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule._on_release)
        assert "except" in source, (
            "_on_release must catch exceptions to keep the pynput listener alive"
        )


class TestTailBufferLocation:
    """The trailing audio buffer must be in the transcription thread, not the listener."""

    def test_tail_buffer_constant_exists(self):
        from contextpulse_voice.voice_module import VoiceModule

        assert hasattr(VoiceModule, "_TAIL_BUFFER_MS"), (
            "VoiceModule must define _TAIL_BUFFER_MS for trailing audio capture"
        )

    def test_tail_buffer_in_reasonable_range(self):
        from contextpulse_voice.voice_module import VoiceModule

        ms = VoiceModule._TAIL_BUFFER_MS
        assert 200 <= ms <= 1000, (
            f"Tail buffer {ms}ms out of range — "
            f"<200ms misses trailing speech, >1000ms adds noticeable latency"
        )

    def test_stop_and_transcribe_exists(self):
        """The tail buffer must be applied in _stop_and_transcribe, not in
        recorder.stop() or the pynput callback."""
        from contextpulse_voice.voice_module import VoiceModule

        assert hasattr(VoiceModule, "_stop_and_transcribe"), (
            "VoiceModule must have _stop_and_transcribe method for "
            "background tail buffer + transcription"
        )
        source = inspect.getsource(VoiceModule._stop_and_transcribe)
        assert "sleep" in source or "_TAIL_BUFFER_MS" in source, (
            "_stop_and_transcribe must apply the tail buffer delay"
        )
