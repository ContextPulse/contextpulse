"""Tests for VoiceModule — ModalityModule lifecycle and event emission."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from contextpulse_core.spine import ContextEvent, EventType, Modality


class TestVoiceModuleLifecycle:
    @pytest.fixture
    def module(self):
        with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
            mock_cfg.return_value = {
                "hotkey": "ctrl+space",
                "fix_hotkey": "ctrl+shift+space",
                "whisper_model": "base",
                "always_use_llm": False,
                "anthropic_api_key": "",
            }
            from contextpulse_voice.voice_module import VoiceModule
            m = VoiceModule(model_size="base")
            yield m

    def test_get_modality(self, module):
        assert module.get_modality() == Modality.VOICE

    def test_initial_state(self, module):
        assert not module.is_alive()
        status = module.get_status()
        assert status["running"] is False
        assert status["events_emitted"] == 0
        assert status["error"] is None

    def test_register_callback(self, module):
        cb = MagicMock()
        module.register(cb)
        assert module._callback is cb

    def test_stop_without_start(self, module):
        # Should not raise
        module.stop()
        assert not module.is_alive()

    def test_get_config_schema(self, module):
        schema = module.get_config_schema()
        assert "voice_hotkey" in schema
        assert "voice_whisper_model" in schema
        assert schema["voice_hotkey"]["type"] == "string"

    def test_emit_increments_counter(self, module):
        received = []
        module.register(lambda e: received.append(e))
        module._running = True
        event = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.SPEECH_START,
        )
        module._emit(event)
        assert module._events_emitted == 1
        assert len(received) == 1

    def test_emit_without_callback(self, module):
        module._running = True
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        # Should not raise
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_when_not_running(self, module):
        module.register(MagicMock())
        module._running = False
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_callback_error_captured(self, module):
        def bad_callback(event):
            raise ValueError("test error")
        module.register(bad_callback)
        module._running = True
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        module._emit(event)
        assert module._error == "test error"

    def test_status_after_events(self, module):
        module.register(MagicMock())
        module._running = True

        for _ in range(3):
            module._emit(ContextEvent(
                modality=Modality.VOICE,
                event_type=EventType.SPEECH_START,
            ))

        status = module.get_status()
        assert status["events_emitted"] == 3
        assert status["last_event_timestamp"] is not None


class TestVoiceModuleTranscription:
    @pytest.fixture
    def module_with_mocks(self):
        with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
            mock_cfg.return_value = {
                "hotkey": "ctrl+space",
                "fix_hotkey": "ctrl+shift+space",
                "whisper_model": "base",
                "always_use_llm": False,
                "anthropic_api_key": "",
            }
            from contextpulse_voice.voice_module import VoiceModule
            m = VoiceModule(model_size="base")

            # Mock transcriber
            m._transcriber = MagicMock()
            m._transcriber.transcribe.return_value = "hello world"

            received = []
            m.register(lambda e: received.append(e))
            m._running = True

            yield m, received

    def test_transcribe_and_paste_emits_event(self, module_with_mocks):
        module, received = module_with_mocks
        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")

        # Should have emitted a TRANSCRIPTION event
        transcription_events = [
            e for e in received if e.event_type == EventType.TRANSCRIPTION
        ]
        assert len(transcription_events) == 1
        evt = transcription_events[0]
        assert evt.modality == Modality.VOICE
        assert "transcript" in evt.payload
        assert "raw_transcript" in evt.payload
        assert "paste_text_hash" in evt.payload

    def test_transcription_event_emitted_before_paste(self, module_with_mocks):
        """Regression: the TRANSCRIPTION event must be written to the spine
        BEFORE the synthetic Ctrl+V fires. paste_text() triggers the Touch
        CorrectionDetector, which queries activity.db for this event ~0.1s
        later. If we emit after paste_text() returns, the row never exists at
        query time and no voice correction is ever harvested (the corrections
        pipeline read zero rows for 19 days because of this ordering).
        """
        module, received = module_with_mocks
        call_order = []

        original_emit = module._emit

        def tracking_emit(event):
            if event.event_type == EventType.TRANSCRIPTION:
                call_order.append("emit")
            return original_emit(event)

        module._emit = tracking_emit

        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            def record_paste(_text):
                call_order.append("paste")
                return (time.time(), "abc123")

            mock_paste.side_effect = record_paste
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")

        assert call_order == ["emit", "paste"], (
            f"TRANSCRIPTION event must be emitted before paste_text() — got {call_order}"
        )

    def test_empty_transcription_skipped(self, module_with_mocks):
        module, received = module_with_mocks
        module._transcriber.transcribe.return_value = ""
        module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")
        assert len(received) == 0

    def test_short_transcription_skipped(self, module_with_mocks):
        module, received = module_with_mocks
        module._transcriber.transcribe.return_value = "a"
        module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")
        assert len(received) == 0


class TestVoiceModuleOverlappingRecordingGuard:
    """Regression tests for the overlapping-PortAudio-stream crash.

    _recording used to be the ONLY guard against a re-entrant hotkey press
    starting a second recording, and it goes False on key release -- before
    the background stop/transcribe thread has actually closed the
    recorder's stream (tail-buffer sleep + silence detection can hold it
    open for up to ~2.7s more). A hotkey re-press inside that window opened
    a second overlapping stream and crashed the daemon with an access
    violation. _recorder_busy is the fix: it stays True until the stream is
    actually closed.
    """

    @pytest.fixture
    def module(self):
        with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
            mock_cfg.return_value = {
                "hotkey": "ctrl+space",
                "fix_hotkey": "ctrl+shift+space",
                "whisper_model": "base",
                "always_use_llm": False,
                "anthropic_api_key": "",
            }
            from contextpulse_voice.voice_module import VoiceModule
            m = VoiceModule(model_size="base")
            m._recorder = MagicMock()
            m._overlay = None
            m.register(MagicMock())
            m._running = True
            yield m

    def _press_hotkey(self, module):
        from pynput import keyboard as kb
        module._on_press_inner(kb.Key.ctrl_l)
        module._on_press_inner(kb.Key.space)

    def _release_hotkey(self, module):
        from pynput import keyboard as kb
        module._on_release_inner(kb.Key.space)

    def test_press_starts_recorder_and_sets_busy(self, module):
        self._press_hotkey(module)
        module._recorder.start.assert_called_once()
        assert module._recording is True
        assert module._recorder_busy is True

    def test_reentrant_press_after_release_but_before_stream_closed_is_blocked(
        self, module
    ):
        """Key released (recording flag goes False) but the background
        stop/transcribe thread hasn't run yet -- recorder is still open.
        A second press in this window must NOT call recorder.start() again.
        """
        self._press_hotkey(module)
        assert module._recorder.start.call_count == 1

        # Simulate release: _on_release_inner spawns a background thread for
        # the real stop/transcribe, but we don't let it run here -- we want
        # to observe the window BEFORE that thread clears _recorder_busy.
        with patch("contextpulse_voice.voice_module.threading.Thread") as mock_thread:
            self._release_hotkey(module)
            assert mock_thread.called, "release must spawn the stop/transcribe thread"

        assert module._recording is False  # release flag clears immediately
        assert module._recorder_busy is True  # but the stream is still open

        # Re-press while the stream is still open — must be blocked.
        self._press_hotkey(module)
        assert module._recorder.start.call_count == 1, (
            "a re-press before the recorder stream is closed must not open "
            "a second overlapping PortAudio stream"
        )

    def test_recorder_busy_cleared_only_after_stream_actually_closes(self, module):
        """_recorder_busy must go False as soon as stop_after_silence()
        returns (the stream is closed) -- not merely on key release."""
        release_started = threading.Event()
        allow_stop_to_finish = threading.Event()

        def _blocking_stop_after_silence():
            release_started.set()
            allow_stop_to_finish.wait(timeout=5)
            return b"fake_wav_bytes"

        module._recorder.stop_after_silence.side_effect = _blocking_stop_after_silence
        module._transcriber = MagicMock()
        module._transcriber.transcribe.return_value = "hello"

        self._press_hotkey(module)
        self._release_hotkey(module)  # spawns real background thread

        assert release_started.wait(timeout=5), "background thread never called stop_after_silence"
        # Stream teardown is still in flight — busy flag must still be True,
        # and a re-press must still be blocked.
        assert module._recorder_busy is True
        self._press_hotkey(module)
        assert module._recorder.start.call_count == 1, "re-press blocked while stream open"

        # Let the background thread finish tearing down the stream.
        allow_stop_to_finish.set()

        # Poll for the busy flag to clear (bounded wait — no sleep loops).
        deadline = time.time() + 5
        while time.time() < deadline and module._recorder_busy:
            time.sleep(0.01)
        assert module._recorder_busy is False, (
            "_recorder_busy must clear once stop_after_silence() returns"
        )

        # Now a fresh press must be allowed to open a new stream.
        self._press_hotkey(module)
        assert module._recorder.start.call_count == 2

    def test_recorder_start_failure_rolls_back_flags(self, module):
        """A Recorder.start() failure (e.g. audio device disconnected) must
        not permanently strand _recording/_recorder_busy at True — that
        would lock out all future dictation until daemon restart."""
        module._recorder.start.side_effect = OSError("no audio device")
        self._press_hotkey(module)
        assert module._recording is False
        assert module._recorder_busy is False

        # A subsequent press with a healthy recorder must succeed.
        module._recorder.start.side_effect = None
        self._press_hotkey(module)
        assert module._recorder.start.call_count == 2
        assert module._recording is True
        assert module._recorder_busy is True
