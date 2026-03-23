"""Tests for activity.py — SQLite activity database with FTS5 search."""

import time
from pathlib import Path
from unittest.mock import patch

from contextpulse_sight.activity import ActivityDB


class TestActivityDB:
    """Test activity recording, querying, and search."""

    def test_record_and_count(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record(
            timestamp=time.time(),
            window_title="Visual Studio Code",
            app_name="Code.exe",
            monitor_index=0,
        )
        assert row_id > 0
        assert db.count() == 1
        db.close()

    def test_update_ocr(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record(
            timestamp=time.time(),
            window_title="Test",
            app_name="test.exe",
        )
        db.update_ocr(row_id, "Hello World\nLine 2", 0.95)

        results = db.search("Hello", minutes_ago=5)
        assert len(results) >= 1
        assert "Hello World" in results[0]["ocr_text"]
        db.close()

    def test_get_summary_groups_by_app(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        for i in range(5):
            db.record(now - i, "Chrome Tab", "chrome.exe")
        for i in range(3):
            db.record(now - i, "VS Code", "Code.exe")

        summary = db.get_summary(hours=1)
        assert summary["total_captures"] == 8
        assert summary["apps"]["chrome.exe"] == 5
        assert summary["apps"]["Code.exe"] == 3
        db.close()

    def test_get_summary_empty(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        summary = db.get_summary(hours=1)
        assert summary["total_captures"] == 0
        db.close()

    def test_search_fts_window_title(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        db.record(time.time(), "AWS Console - EC2 Instances", "chrome.exe")
        db.record(time.time(), "Visual Studio Code", "Code.exe")

        results = db.search("AWS", minutes_ago=5)
        assert len(results) >= 1
        assert "AWS" in results[0]["window_title"]
        db.close()

    def test_search_fts_app_name(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        db.record(time.time(), "Some Window", "chrome.exe")

        results = db.search("chrome", minutes_ago=5)
        assert len(results) >= 1
        db.close()

    def test_get_context_at(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        target_time = time.time() - 300  # 5 minutes ago
        db.record(target_time, "Error Window", "app.exe", frame_path="/tmp/frame.jpg")

        result = db.get_context_at(minutes_ago=5)
        assert result is not None
        assert result["window_title"] == "Error Window"
        db.close()

    def test_get_context_at_empty(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        result = db.get_context_at(minutes_ago=5)
        assert result is None
        db.close()

    def test_prune_old_records(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        old_time = time.time() - 100
        recent_time = time.time()

        db.record(old_time, "Old Window", "old.exe")
        db.record(recent_time, "New Window", "new.exe")
        assert db.count() == 2

        db.prune(max_age_seconds=50)
        assert db.count() == 1
        db.close()

    def test_record_with_diff_score(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record(
            timestamp=time.time(),
            window_title="VS Code",
            app_name="Code.exe",
            diff_score=85.3,
        )
        result = db.get_context_at(minutes_ago=1)
        assert result is not None
        assert result["diff_score"] == 85.3
        db.close()

    def test_search_by_frame(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        db.record(
            timestamp=time.time(),
            window_title="Test",
            app_name="test.exe",
            frame_path="/tmp/frame_123.jpg",
            diff_score=42.0,
        )
        result = db.search_by_frame("/tmp/frame_123.jpg")
        assert result is not None
        assert result["diff_score"] == 42.0

        assert db.search_by_frame("/nonexistent.jpg") is None
        db.close()

    def test_record_mcp_call(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record_mcp_call("get_screenshot", "claude-code")
        assert row_id > 0
        db.record_mcp_call("get_screen_text", "claude-code")
        db.record_mcp_call("get_screenshot", "cursor")

        stats = db.get_agent_stats(hours=1)
        assert stats["total_calls"] == 3
        assert "claude-code" in stats["clients"]
        assert stats["clients"]["claude-code"]["get_screenshot"] == 1
        assert stats["clients"]["claude-code"]["get_screen_text"] == 1
        assert stats["clients"]["cursor"]["get_screenshot"] == 1
        db.close()

    def test_get_agent_stats_empty(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        stats = db.get_agent_stats(hours=1)
        assert stats["total_calls"] == 0
        assert stats["clients"] == {}
        db.close()

    def test_record_clipboard(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record_clipboard(time.time(), "ERROR: connection refused on port 8080")
        assert row_id > 0
        history = db.get_clipboard_history(count=5)
        assert len(history) == 1
        assert "connection refused" in history[0]["text"]
        db.close()

    def test_clipboard_history_order(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        db.record_clipboard(now - 10, "first clip")
        db.record_clipboard(now - 5, "second clip")
        db.record_clipboard(now, "third clip")
        history = db.get_clipboard_history(count=2)
        assert len(history) == 2
        assert history[0]["text"] == "third clip"  # most recent first
        assert history[1]["text"] == "second clip"
        db.close()

    def test_search_clipboard(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        db.record_clipboard(now, "Traceback: ValueError at line 42")
        db.record_clipboard(now, "https://github.com/some/repo")
        results = db.search_clipboard("ValueError", minutes_ago=5)
        assert len(results) == 1
        assert "ValueError" in results[0]["text"]
        db.close()

    def test_search_clipboard_empty(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        results = db.search_clipboard("nonexistent", minutes_ago=5)
        assert results == []
        db.close()

    def test_multiple_records_and_search(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        db.record(now - 10, "Google Chrome - GitHub", "chrome.exe")
        db.record(now - 5, "Google Chrome - Gmail", "chrome.exe")
        db.record(now, "VS Code - main.py", "Code.exe")

        # Search for GitHub
        results = db.search("GitHub", minutes_ago=5)
        assert len(results) >= 1
        assert "GitHub" in results[0]["window_title"]

        # Search for chrome
        results = db.search("chrome", minutes_ago=5)
        assert len(results) >= 2
        db.close()
