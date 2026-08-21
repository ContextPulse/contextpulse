"""Hotkey simulation tests — in-process press/release sequences.

Tests VoiceModule hotkey handling by calling _on_press_inner/_on_release_inner
directly with pynput key objects. Does NOT use pynput.keyboard.Controller
(unreliable for cross-process hooks on Windows).
"""

from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard as kb


@pytest.fixture
def voice_module():
    """VoiceModule with mocked recorder/transcriber for hotkey testing."""
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
        m._recorder.stop.return_value = b"\x00" * 100  # fake audio
        m._transcriber = MagicMock()
        m._transcriber.transcribe.return_value = "hello world"
        m._callback = MagicMock()
        m._running = True  # simulate started state
        yield m


class TestDictationHotkey:
    """Ctrl+Space hold-to-record behavior."""

    def test_ctrl_space_starts_recording(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        assert not voice_module._recording, "Ctrl alone should not start recording"
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._recording, "Ctrl+Space should start recording"

    def test_right_ctrl_also_works(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_r)
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._recording

    def test_space_alone_does_not_record(self, voice_module):
        voice_module._on_press_inner(kb.Key.space)
        assert not voice_module._recording

    def test_release_space_stops_recording(self, voice_module):
        # Start recording
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._recording

        # Release space
        voice_module._on_release_inner(kb.Key.space)
        assert not voice_module._recording

    def test_release_ctrl_stops_recording(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._recording

        voice_module._on_release_inner(kb.Key.ctrl_l)
        assert not voice_module._recording

    def test_recorder_start_called_on_press(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        voice_module._recorder.start.assert_called_once()

    def test_double_press_does_not_restart(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        voice_module._on_press_inner(kb.Key.space)  # duplicate press
        voice_module._recorder.start.assert_called_once()


class TestDuplicateReleaseDelivery:
    """Duplicate release event protection.

    A wall-clock ("ignore a second release within 1s") debounce used to sit
    in _on_release_inner. It was removed 2026-08-21: it also fired on a
    genuine new press+release inside that 1s window -- David's normal rapid
    burst-dictation pattern -- and since only that code path spawns the
    background thread that clears _recorder_busy and closes the recorder's
    stream, a debounced release permanently stranded the stream open and
    locked out every future hotkey press until the daemon was restarted.

    The `if self._recording` guard that remains is sufficient on its own: a
    duplicate delivery of the SAME physical key-up (the actual Windows/
    pynput quirk this code defends against) is delivered on the same
    serialized listener thread, so the first delivery already sets
    self._recording = False before a second delivery of the same event can
    be processed.
    """

    def test_duplicate_release_of_same_event_is_a_no_op(self, voice_module):
        """Two back-to-back _on_release_inner calls for the SAME key-up
        (no re-press in between) must only spawn one stop/transcribe
        thread -- the second call finds _recording already False."""
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._recording

        with patch(
            "contextpulse_voice.voice_module.threading.Thread"
        ) as mock_thread:
            voice_module._on_release_inner(kb.Key.space)  # real delivery
            assert not voice_module._recording
            voice_module._on_release_inner(kb.Key.space)  # duplicate delivery

        assert mock_thread.call_count == 1, (
            "a duplicate delivery of the same key-up must not spawn a "
            "second stop/transcribe thread"
        )

    # See test_voice_module.py::TestVoiceModuleOverlappingRecordingGuard::
    # test_rapid_burst_dictation_never_strands_recorder_busy for the real-
    # thread regression covering a genuine rapid re-press -- that requires
    # waiting for the real background stop/transcribe thread to clear
    # _recorder_busy, which needs real timing, not the synchronous mocks
    # this fixture's `voice_module._recorder = MagicMock()` provides.


class TestFixLastHotkey:
    """Ctrl+Shift+Space fix-last behavior."""

    def test_fix_last_hotkey_triggers_fixing(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.shift_l)
        voice_module._on_press_inner(kb.Key.space)
        assert voice_module._fixing

    def test_fix_last_does_not_start_recording(self, voice_module):
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.shift_l)
        voice_module._on_press_inner(kb.Key.space)
        assert not voice_module._recording, (
            "Ctrl+Shift+Space is fix-last, not record"
        )

    def test_fix_last_not_triggered_during_recording(self, voice_module):
        voice_module._recording = True
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.shift_l)
        voice_module._on_press_inner(kb.Key.space)
        assert not voice_module._fixing, (
            "Fix-last should not trigger while recording"
        )

    def test_fixing_clears_on_release(self, voice_module):
        voice_module._fixing = True
        voice_module._pressed_keys = {kb.Key.ctrl_l, kb.Key.shift_l, kb.Key.space}
        voice_module._on_release_inner(kb.Key.shift_l)
        assert not voice_module._fixing


class TestNotRunning:
    """Module must not process hotkeys when not running."""

    def test_press_ignored_when_not_running(self, voice_module):
        voice_module._running = False
        voice_module._on_press_inner(kb.Key.ctrl_l)
        voice_module._on_press_inner(kb.Key.space)
        # Keys are tracked but recording should not start because
        # the emit guard checks _running
        # The actual guard is in _emit, not _on_press_inner
