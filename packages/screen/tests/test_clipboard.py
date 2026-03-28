"""Tests for clipboard.py — clipboard monitoring logic."""

import time
from unittest.mock import patch

from contextpulse_sight.activity import ActivityDB


class TestClipboardMonitor:
    """Test clipboard monitoring and filtering logic."""

    def test_captures_new_text(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        # Simulate a clipboard change
        monitor._last_text = ""
        monitor._last_capture_time = 0.0
        monitor._sequence_number = 0

        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1), \
             patch("contextpulse_sight.clipboard._get_clipboard_text",
                   return_value="ERROR: connection refused on port 8080"):
            monitor._check_clipboard()

        history = db.get_clipboard_history(count=5)
        assert len(history) == 1
        assert "connection refused" in history[0]["text"]
        db.close()

    def test_skips_short_text(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        monitor._sequence_number = 0

        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1), \
             patch("contextpulse_sight.clipboard._get_clipboard_text", return_value="hi"):
            monitor._check_clipboard()

        history = db.get_clipboard_history(count=5)
        assert len(history) == 0
        db.close()

    def test_skips_duplicate_consecutive(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        monitor._sequence_number = 0
        monitor._last_capture_time = 0.0

        text = "some error message here"

        # First capture
        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1), \
             patch("contextpulse_sight.clipboard._get_clipboard_text", return_value=text):
            monitor._check_clipboard()

        # Same text again (different sequence number, but same content)
        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=2), \
             patch("contextpulse_sight.clipboard._get_clipboard_text", return_value=text):
            monitor._check_clipboard()

        history = db.get_clipboard_history(count=5)
        assert len(history) == 1  # only captured once
        db.close()

    def test_skips_when_sequence_unchanged(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        monitor._sequence_number = 5  # already seen sequence 5

        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=5), \
             patch("contextpulse_sight.clipboard._get_clipboard_text") as mock_get:
            monitor._check_clipboard()
            mock_get.assert_not_called()  # should not even read clipboard

        db.close()

    def test_truncates_very_long_text(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        monitor._sequence_number = 0
        monitor._last_capture_time = 0.0

        long_text = "x" * 20_000

        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1), \
             patch("contextpulse_sight.clipboard._get_clipboard_text", return_value=long_text):
            monitor._check_clipboard()

        history = db.get_clipboard_history(count=5)
        assert len(history) == 1
        assert len(history[0]["text"]) < 20_000
        assert "truncated" in history[0]["text"]
        db.close()

    def test_debounce_rapid_copies(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        from contextpulse_sight.clipboard import ClipboardMonitor

        monitor = ClipboardMonitor(db)
        monitor._sequence_number = 0
        monitor._last_capture_time = time.time()  # just captured

        with patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1), \
             patch("contextpulse_sight.clipboard._get_clipboard_text",
                   return_value="should be debounced"):
            monitor._check_clipboard()

        history = db.get_clipboard_history(count=5)
        assert len(history) == 0  # debounced
        db.close()
