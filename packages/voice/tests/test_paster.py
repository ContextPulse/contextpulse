"""Tests for the paster module — clipboard paste and hash tracking."""

import time
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_voice.paster import paste_text, get_last_paste_info
import contextpulse_voice.paster as paster_module


class TestPasteText:
    @pytest.fixture(autouse=True)
    def reset_state(self):
        """Reset module-level state between tests."""
        paster_module._last_paste_time = 0.0
        paster_module._last_paste_hash = ""
        yield

    def test_paste_returns_hash(self):
        import pyperclip
        import pyautogui
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
        import pyperclip
        import pyautogui
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paste_text("test text")
        ts, h = get_last_paste_info()
        assert ts > 0
        assert len(h) == 16

    def test_rapid_paste_blocked(self):
        import pyperclip
        import pyautogui
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paste_text("first paste")
        # Immediately try again
        ts, h = paste_text("second paste")
        assert ts == 0.0  # blocked

    def test_consistent_hash(self):
        import pyperclip
        import pyautogui
        pyperclip.copy = MagicMock()
        pyautogui.hotkey = MagicMock()

        paster_module._last_paste_time = 0.0  # reset
        _, h1 = paste_text("same text")
        paster_module._last_paste_time = 0.0  # reset
        _, h2 = paste_text("same text")
        assert h1 == h2
