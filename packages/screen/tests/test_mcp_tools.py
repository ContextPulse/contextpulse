"""Tests for MCP server tools — tests the underlying logic of each tool.

Since FastMCP decorators wrap functions, we test the tool logic by importing
the module and calling the functions directly via the module namespace,
patching dependencies at the module level.
"""

import time
from unittest.mock import patch

from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from PIL import Image


def _make_image(width=1280, height=720, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


class TestGetActivitySummary:
    """Test activity summary logic."""

    def test_empty_returns_no_activity(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        summary = db.get_summary(hours=1)
        assert summary["total_captures"] == 0
        db.close()

    def test_groups_by_app(self, tmp_path):
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
        assert len(summary["titles"]) > 0
        assert summary["time_range"][0] > 0
        db.close()

    def test_respects_time_range(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        # Record from 2 hours ago
        db.record(time.time() - 7200, "Old Window", "old.exe")
        # Record from now
        db.record(time.time(), "New Window", "new.exe")

        # Only last 1 hour
        summary = db.get_summary(hours=1)
        assert summary["total_captures"] == 1
        assert "new.exe" in summary["apps"]
        db.close()


class TestSearchHistory:
    """Test search history logic."""

    def test_no_results(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        results = db.search("nonexistent", minutes_ago=5)
        assert results == []
        db.close()

    def test_finds_window_title(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        db.record(time.time(), "AWS Console - EC2 Instances", "chrome.exe")
        results = db.search("AWS", minutes_ago=5)
        assert len(results) >= 1
        assert "AWS" in results[0]["window_title"]
        db.close()

    def test_finds_ocr_text(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record(time.time(), "Terminal", "cmd.exe")
        db.update_ocr(row_id, "ERROR: connection refused on port 8080", 0.92)
        results = db.search("connection", minutes_ago=5)
        assert len(results) >= 1
        assert "connection refused" in results[0]["ocr_text"]
        db.close()

    def test_respects_time_range(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        # Old record
        db.record(time.time() - 600, "Old AWS", "chrome.exe")
        # Recent record
        db.record(time.time(), "New AWS", "chrome.exe")

        results = db.search("AWS", minutes_ago=5)
        assert len(results) == 1
        assert results[0]["window_title"] == "New AWS"
        db.close()

    def test_finds_app_name(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        db.record(time.time(), "Some Window", "thinkorswim.exe")
        results = db.search("thinkorswim", minutes_ago=5)
        assert len(results) >= 1
        db.close()


class TestGetContextAt:
    """Test context retrieval logic."""

    def test_no_records_returns_none(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        result = db.get_context_at(minutes_ago=5)
        assert result is None
        db.close()

    def test_finds_nearest_record(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        target = time.time() - 300  # 5 min ago
        db.record(target, "Error Window", "app.exe", monitor_index=1,
                  frame_path="/tmp/frame.jpg")
        result = db.get_context_at(minutes_ago=5)
        assert result is not None
        assert result["window_title"] == "Error Window"
        assert result["app_name"] == "app.exe"
        assert result["monitor_index"] == 1
        db.close()

    def test_returns_ocr_text_when_available(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        row_id = db.record(time.time() - 300, "Code Editor", "Code.exe")
        db.update_ocr(row_id, "def hello_world():\n    print('hi')", 0.95)
        result = db.get_context_at(minutes_ago=5)
        assert result is not None
        assert "hello_world" in result["ocr_text"]
        db.close()


class TestBufferMCPIntegration:
    """Test buffer operations used by MCP tools."""

    def test_get_recent_with_monitor_labels(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            from contextpulse_sight.buffer import parse_frame_path
            buf = RollingBuffer()
            buf.add(_make_image(color=(50, 50, 50)), monitor_index=0)
            time.sleep(0.01)
            buf.add(_make_image(color=(200, 200, 200)), monitor_index=1)

            frames = buf.get_recent(seconds=60)
            assert len(frames) >= 2
            # Frames should have monitor index in filename
            for f in frames:
                parsed = parse_frame_path(f)
                assert parsed is not None
                assert parsed[1] in (0, 1)

    def test_buffer_status_shows_monitors(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            from contextpulse_sight.buffer import parse_frame_path
            buf = RollingBuffer()
            buf.add(_make_image(color=(50, 50, 50)), monitor_index=0)
            time.sleep(0.01)
            buf.add(_make_image(color=(200, 200, 200)), monitor_index=1)

            frames = buf.list_frames()
            monitors = set()
            for f in frames:
                parsed = parse_frame_path(f)
                if parsed:
                    monitors.add(parsed[1])
            assert 0 in monitors
            assert 1 in monitors


class TestStorageModeLogic:
    """Test smart storage mode classification."""

    def test_text_heavy_classified_correctly(self):
        from contextpulse_sight.classifier import classify_and_extract
        # Mock OCR to return no results (solid black image)
        with patch("contextpulse_sight.classifier._get_ocr") as mock_ocr:
            mock_ocr.return_value.return_value = (None, None)
            img = _make_image(100, 100, (0, 0, 0))
            result = classify_and_extract(img)
            assert result["type"] == "image"
            assert result["chars"] == 0

    def test_always_both_apps_config(self):
        from contextpulse_sight.config import ALWAYS_BOTH_APPS
        assert "thinkorswim.exe" in ALWAYS_BOTH_APPS
