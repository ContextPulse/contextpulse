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
