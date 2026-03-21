"""Tests for events.py — event-driven capture detection."""

import time
from unittest.mock import patch

from contextpulse_sight.events import EventDetector


class TestEventDetector:
    """Test event detection logic."""

    def test_window_focus_change_fires_event(self):
        titles = iter(["App A", "App B"])
        detector = EventDetector(
            get_cursor_pos=lambda: (100, 100),
            find_monitor_index=lambda cx, cy: 0,
        )
        with patch("contextpulse_sight.events.get_foreground_window_title", side_effect=lambda: next(titles, "App B")):
            # First poll — sets initial title
            detector._check_window_change()
            assert not detector.has_pending_event()

            # Second poll — title changed
            detector._check_window_change()
            assert detector.has_pending_event()
            assert "window_focus" in detector.get_pending_reason()

    def test_same_window_no_event(self):
        detector = EventDetector(
            get_cursor_pos=lambda: (100, 100),
            find_monitor_index=lambda cx, cy: 0,
        )
        with patch("contextpulse_sight.events.get_foreground_window_title", return_value="Same App"):
            detector._check_window_change()
            detector._check_window_change()
            assert not detector.has_pending_event()

    def test_idle_then_active_fires_event(self):
        detector = EventDetector(
            get_cursor_pos=lambda: (200, 200),
            find_monitor_index=lambda cx, cy: 0,
        )
        # Simulate idle by setting last activity far in the past
        detector._last_activity_time = time.time() - 60
        detector._last_cursor = (100, 100)

        detector._check_cursor_activity()
        assert detector.has_pending_event()
        assert "idle_wake" in detector.get_pending_reason()

    def test_no_movement_no_event(self):
        detector = EventDetector(
            get_cursor_pos=lambda: (100, 100),
            find_monitor_index=lambda cx, cy: 0,
        )
        detector._last_cursor = (100, 100)
        detector._last_activity_time = time.time()

        detector._check_cursor_activity()
        assert not detector.has_pending_event()

    def test_clear_pending_resets(self):
        detector = EventDetector(
            get_cursor_pos=lambda: (100, 100),
            find_monitor_index=lambda cx, cy: 0,
        )
        detector._trigger("test_reason")
        assert detector.has_pending_event()

        detector.clear_pending()
        assert not detector.has_pending_event()
        assert detector.get_pending_reason() == ""

    def test_monitor_cross_fires_event(self):
        detector = EventDetector(
            get_cursor_pos=lambda: (2000, 500),
            find_monitor_index=lambda cx, cy: 1,  # cursor is now on monitor 1
        )
        detector._last_cursor = (100, 500)  # was at (100, 500) - movement > 200px
        detector._last_monitor_index = 0  # was on monitor 0
        detector._last_activity_time = time.time()

        detector._check_cursor_activity()
        assert detector.has_pending_event()
        assert "monitor_cross" in detector.get_pending_reason()
