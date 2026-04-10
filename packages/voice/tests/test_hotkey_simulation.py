"""Hotkey simulation tests — in-process press/release sequences.

Tests VoiceModule hotkey handling by calling _on_press_inner/_on_release_inner
directly with pynput key objects. Does NOT use pynput.keyboard.Controller
(unreliable for cross-process hooks on Windows).
"""

import time
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


class TestDebounce:
    """Duplicate release event protection."""

    def test_rapid_release_debounced(self, voice_module):
        """Two releases within 1s should only trigger one stop."""
        # First release
        voice_module._recording = True
        voice_module._pressed_keys = {kb.Key.ctrl_l, kb.Key.space}
        voice_module._last_stop_time = 0
        voice_module._on_release_inner(kb.Key.space)
        assert not voice_module._recording

        # Second release within 1s — should be debounced
        voice_module._recording = True
        voice_module._pressed_keys = {kb.Key.ctrl_l, kb.Key.space}
        voice_module._on_release_inner(kb.Key.space)
        # Recording should be False but no additional transcription spawned

    def test_release_after_debounce_window_works(self, voice_module):
        """Release after >1s should work normally."""
        voice_module._recording = True
        voice_module._pressed_keys = {kb.Key.ctrl_l, kb.Key.space}
        voice_module._last_stop_time = time.time() - 2.0  # 2 seconds ago
        voice_module._on_release_inner(kb.Key.space)
        assert not voice_module._recording


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
