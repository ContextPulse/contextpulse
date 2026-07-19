"""Tests for the paster module — clipboard paste and hash tracking."""

import threading
from unittest.mock import MagicMock

import contextpulse_voice.paster as paster_module
import pytest
from contextpulse_voice.paster import get_last_paste_info, paste_text


class TestPasteText:
    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset module-level state between tests."""
        paster_module._last_paste_time = 0.0
        paster_module._last_paste_hash = ""
        paster_module._paste_lock = threading.Lock()
        yield

    def test_paste_returns_hash(self):
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        ts, h = paste_text("hello world")
        assert ts > 0
        assert len(h) == 16  # SHA-256 prefix

    def test_empty_text_skipped(self):
        ts, h = paste_text("")
        assert ts == 0.0
        assert h == ""

    def test_whitespace_only_skipped(self):
        ts, h = paste_text("   ")
        assert ts == 0.0
        assert h == ""

    def test_get_last_paste_info(self):
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paste_text("test text")
        ts, h = get_last_paste_info()
        assert ts > 0
        assert len(h) == 16

    def test_rapid_paste_blocked(self):
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paste_text("first paste")
        # Immediately try again
        ts, h = paste_text("second paste")
        assert ts == 0.0  # blocked

    def test_consistent_hash(self):
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paster_module._last_paste_time = 0.0  # reset
        _, h1 = paste_text("same text")
        paster_module._last_paste_time = 0.0  # reset
        _, h2 = paste_text("same text")
        assert h1 == h2

    def test_terminal_focus_uses_ctrl_shift_v(self, monkeypatch):
        """Dictating into a terminal must send the terminal paste chord."""
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        hotkey = MagicMock()
        monkeypatch.setattr(pyautogui, "hotkey", hotkey)
        monkeypatch.setattr(paster_module, "_focused_is_terminal", lambda: True)

        ts, _ = paste_text("into terminal")
        assert ts > 0
        hotkey.assert_called_once_with("ctrl", "shift", "v")

    def test_non_terminal_focus_uses_ctrl_v(self, monkeypatch):
        """Normal apps keep the plain Ctrl+V paste chord."""
        import pyautogui
        import pyperclip
        pyperclip.copy = MagicMock()
        hotkey = MagicMock()
        monkeypatch.setattr(pyautogui, "hotkey", hotkey)
        monkeypatch.setattr(paster_module, "_focused_is_terminal", lambda: False)

        ts, _ = paste_text("into editor")
        assert ts > 0
        hotkey.assert_called_once_with("ctrl", "v")

    def test_terminal_class_detection(self, monkeypatch):
        """Known terminal window classes are recognized; others are not."""
        monkeypatch.setattr(
            paster_module, "_foreground_window_class",
            lambda: "CASCADIA_HOSTING_WINDOW_CLASS",
        )
        assert paster_module._focused_is_terminal() is True

        monkeypatch.setattr(
            paster_module, "_foreground_window_class", lambda: "Chrome_WidgetWin_1"
        )
        assert paster_module._focused_is_terminal() is False

        monkeypatch.setattr(paster_module, "_foreground_window_class", lambda: None)
        assert paster_module._focused_is_terminal() is False
