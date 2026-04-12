"""Tests for BurstTracker — keystroke aggregation."""

import time
from unittest.mock import MagicMock

from contextpulse_touch.burst_tracker import BurstTracker


class TestBurstFormation:
    def test_single_char_below_threshold(self):
        """Single character shouldn't emit a burst (below min_chars)."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        bt.on_key_press("a")
        time.sleep(0.3)
        on_burst.assert_not_called()
        bt.stop()

    def test_burst_emitted_after_timeout(self):
        """Burst emitted after silence exceeds timeout."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        for c in "hello":
            bt.on_key_press(c)
        time.sleep(0.3)
        on_burst.assert_called_once()
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 5
        assert data["backspace_count"] == 0
        bt.stop()

    def test_burst_data_fields(self):
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        for c in "testing":
            bt.on_key_press(c)
        time.sleep(0.3)  # 3x the timeout for CI runner headroom on slow macOS runners
        data = on_burst.call_args[0][0]
        assert "char_count" in data
        assert "word_count" in data
        assert "duration_ms" in data
        assert "wpm" in data
        assert "backspace_count" in data
        assert "has_selection" in data
        bt.stop()

    def test_backspace_counted(self):
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        for c in "helllo":
            bt.on_key_press(c)
        bt.on_key_press(None, is_backspace=True)
        bt.on_key_press(None, is_backspace=True)
        bt.on_key_press("l")
        bt.on_key_press("o")
        time.sleep(0.3)
        data = on_burst.call_args[0][0]
        assert data["backspace_count"] == 2
        bt.stop()

    def test_selection_tracked(self):
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        for c in "test":
            bt.on_key_press(c)
        bt.on_key_press(None, is_selection=True)
        time.sleep(0.3)
        data = on_burst.call_args[0][0]
        assert data["has_selection"] is True
        bt.stop()

    def test_multiple_bursts(self):
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        # First burst
        for c in "hello":
            bt.on_key_press(c)
        time.sleep(0.3)
        # Second burst
        for c in "world":
            bt.on_key_press(c)
        time.sleep(0.3)
        assert on_burst.call_count == 2
        bt.stop()


class TestWatchMode:
    def test_enter_watch_mode(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        assert bt.is_watching
        bt.stop()

    def test_watch_captures_text(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        for c in "kubectl":
            bt.on_key_press(c)
        text = bt.exit_watch_mode()
        assert text == "kubectl"
        assert not bt.is_watching

    def test_watch_handles_backspace(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        for c in "kubectll":
            bt.on_key_press(c)
        # Correct double-l typo: backspace twice, retype 'l'
        bt.on_key_press(None, is_backspace=True)
        bt.on_key_press(None, is_backspace=True)
        bt.on_key_press("l")
        text = bt.exit_watch_mode()
        assert text == "kubectl"

    def test_exit_clears_text(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        bt.on_key_press("a")
        bt.exit_watch_mode()
        bt.enter_watch_mode()
        text = bt.exit_watch_mode()
        assert text == ""

    def test_get_watch_text_peek(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        bt.on_key_press("h")
        bt.on_key_press("i")
        assert bt.get_watch_text() == "hi"
        assert bt.is_watching  # Still in watch mode
        bt.stop()

    def test_normal_mode_no_content(self):
        """In normal mode, characters are NOT captured."""
        bt = BurstTracker()
        for c in "secret":
            bt.on_key_press(c)
        # No way to retrieve text — privacy by design
        assert not bt.is_watching
        bt.stop()


class TestStopCleanup:
    def test_stop_cancels_timer(self):
        bt = BurstTracker(burst_timeout=10.0)  # Long timeout
        bt.on_key_press("a")
        bt.stop()
        # Should not hang or error

    def test_stop_exits_watch_mode(self):
        bt = BurstTracker()
        bt.enter_watch_mode()
        bt.stop()
        assert not bt.is_watching
