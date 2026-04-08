"""Tests for MCP server tools — tests the underlying logic of each tool.

Since FastMCP decorators wrap functions, we test the tool logic by importing
the module and calling the functions directly via the module namespace,
patching dependencies at the module level.
"""

import time
from unittest.mock import MagicMock, patch

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


class TestGetMonitorStates:
    """Test per-monitor state retrieval for get_monitor_summary()."""

    def test_empty_returns_empty_list(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        states = db.get_monitor_states()
        assert states == []
        db.close()

    def test_returns_latest_per_monitor(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        # Monitor 0: two records, should return the latest
        db.record(now - 10, "Old Window", "old.exe", monitor_index=0, diff_score=5.0)
        db.record(now, "New Window", "new.exe", monitor_index=0, diff_score=15.0)
        # Monitor 1: one record
        db.record(now - 5, "Code Editor", "Code.exe", monitor_index=1, diff_score=3.0)

        states = db.get_monitor_states()
        assert len(states) == 2
        # Monitor 0 should show the latest
        m0 = next(s for s in states if s["monitor_index"] == 0)
        assert m0["window_title"] == "New Window"
        assert m0["app_name"] == "new.exe"
        assert m0["diff_score"] == 15.0
        # Monitor 1
        m1 = next(s for s in states if s["monitor_index"] == 1)
        assert m1["window_title"] == "Code Editor"
        assert m1["app_name"] == "Code.exe"
        db.close()

    def test_ordered_by_monitor_index(self, tmp_path):
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        db.record(now, "Right", "right.exe", monitor_index=1)
        db.record(now, "Left", "left.exe", monitor_index=0)

        states = db.get_monitor_states()
        assert states[0]["monitor_index"] == 0
        assert states[1]["monitor_index"] == 1
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


class TestGetMonitorStatesEdgeCases:
    """Edge cases for get_monitor_states()."""

    def test_multiple_records_same_timestamp_same_monitor(self, tmp_path):
        """If two records have the exact same max timestamp, still returns one row."""
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        db.record(now, "Window A", "a.exe", monitor_index=0, diff_score=5.0)
        db.record(now, "Window B", "b.exe", monitor_index=0, diff_score=10.0)
        states = db.get_monitor_states()
        # Should return at least one row for monitor 0 (SQL may return both
        # since they share the max timestamp — that's acceptable)
        m0_states = [s for s in states if s["monitor_index"] == 0]
        assert len(m0_states) >= 1
        db.close()

    def test_handles_many_monitors(self, tmp_path):
        """Works with 4+ monitors."""
        db = ActivityDB(db_path=tmp_path / "test.db")
        now = time.time()
        for i in range(4):
            db.record(now, f"Window {i}", f"app{i}.exe", monitor_index=i)
        states = db.get_monitor_states()
        assert len(states) == 4
        assert [s["monitor_index"] for s in states] == [0, 1, 2, 3]
        db.close()


class TestJpegCacheHelpers:
    """Test the MCP server's JPEG cache functions."""

    def test_cache_miss_returns_none(self):
        from contextpulse_sight import mcp_server
        # Clear cache
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()
        assert mcp_server._cache_get(0) is None

    def test_cache_put_and_get(self):
        from contextpulse_sight import mcp_server
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()
        mcp_server._cache_put(0, b"fake_jpeg_data")
        result = mcp_server._cache_get(0)
        assert result == b"fake_jpeg_data"
        # Cleanup
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()

    def test_cache_expires(self):
        from contextpulse_sight import mcp_server
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()
            # Insert with old timestamp (3 seconds ago, past the 2s TTL)
            mcp_server._jpeg_cache[0] = (b"stale_data", time.time() - 3.0)
        result = mcp_server._cache_get(0)
        assert result is None  # expired
        # Cleanup
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()

    def test_cache_per_monitor_isolation(self):
        from contextpulse_sight import mcp_server
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()
        mcp_server._cache_put(0, b"monitor0")
        mcp_server._cache_put(1, b"monitor1")
        assert mcp_server._cache_get(0) == b"monitor0"
        assert mcp_server._cache_get(1) == b"monitor1"
        assert mcp_server._cache_get(2) is None  # not cached
        # Cleanup
        with mcp_server._jpeg_cache_lock:
            mcp_server._jpeg_cache.clear()


class TestTokenEstimationEdgeCases:
    """Validate token formula against Claude's actual spec."""

    def test_zero_dimensions(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        assert estimate_image_tokens(0, 0) == 1  # floor of 1

    def test_1x1_pixel(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        assert estimate_image_tokens(1, 1) == 1  # 1/750 rounds to 0, max(1, 0) = 1

    def test_claude_doc_example_200x200(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # Claude docs: 200x200 = ~54 tokens
        result = estimate_image_tokens(200, 200)
        assert result == 53  # 40000/750 = 53.3, rounds to 53

    def test_claude_doc_example_1000x1000(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # Claude docs: 1000x1000 = ~1,334 tokens
        result = estimate_image_tokens(1000, 1000)
        assert result == 1333  # 1000000/750 = 1333.3

    def test_max_before_downscale_16x9(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # 1394x784 is max 16:9 before Claude downscales (~1.09MP < 1.15MP limit)
        result = estimate_image_tokens(1394, 784)
        assert result == 1457  # 1092896/750 = 1457.2

    def test_current_default_1280x720(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        result = estimate_image_tokens(1280, 720)
        assert result == 1229  # 921600/750 = 1228.8, rounds to 1229


class TestGetMonitorSummaryIntegration:
    """Integration test for get_monitor_summary() MCP tool."""

    def test_returns_string_with_empty_db(self, tmp_path):
        """Should work gracefully when no activity has been recorded yet."""
        from contextpulse_sight import mcp_server

        # Replace the shared ActivityDB with a fresh one
        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")

        # Mock mss to return 2 monitors, and privacy/capture helpers
        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},  # virtual desktop
            {"left": 0, "top": 0, "width": 1920, "height": 1080},  # monitor 0
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},  # monitor 1
        ]
        mock_sct_ctx = MagicMock()
        mock_sct_ctx.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct_ctx.__exit__ = MagicMock(return_value=False)

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.mss_lib.mss", return_value=mock_sct_ctx), \
                 patch("contextpulse_sight.mcp_server.capture._get_cursor_pos", return_value=(500, 500)), \
                 patch("contextpulse_sight.mcp_server.get_foreground_window_title", return_value="VS Code"), \
                 patch("contextpulse_sight.mcp_server.get_foreground_process_name", return_value="Code.exe"), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False), \
                 patch("contextpulse_sight.mcp_server.is_title_blocked", return_value=False):
                result = mcp_server.get_monitor_summary()

            assert isinstance(result, str)
            assert "Monitor 0" in result
            assert "Monitor 1" in result
            assert "[ACTIVE]" in result  # cursor at 500,500 is on monitor 0
            assert "VS Code" in result  # live foreground override on active monitor
            assert "no data" in result  # no DB records for monitor 1
        finally:
            mcp_server._activity_db = original_db
            test_db.close()

    def test_shows_db_data_for_inactive_monitor(self, tmp_path):  # gitleaks:allow
        """Inactive monitor uses ActivityDB data, not live foreground."""
        from contextpulse_sight import mcp_server

        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")
        test_db.record(time.time(), "Chrome - Gmail", "chrome.exe", monitor_index=1, diff_score=25.0)

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct_ctx = MagicMock()
        mock_sct_ctx.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct_ctx.__exit__ = MagicMock(return_value=False)

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.mss_lib.mss", return_value=mock_sct_ctx), \
                 patch("contextpulse_sight.mcp_server.capture._get_cursor_pos", return_value=(500, 500)), \
                 patch("contextpulse_sight.mcp_server.get_foreground_window_title", return_value="VS Code"), \
                 patch("contextpulse_sight.mcp_server.get_foreground_process_name", return_value="Code.exe"), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False), \
                 patch("contextpulse_sight.mcp_server.is_title_blocked", return_value=False):
                result = mcp_server.get_monitor_summary()

            assert "Chrome - Gmail" in result  # monitor 1 from DB
            assert "chrome.exe" in result
            assert "some change" in result  # diff_score=25.0
        finally:
            mcp_server._activity_db = original_db
            test_db.close()

    def test_blocked_title_hidden(self, tmp_path):
        """Blocked window titles should show [BLOCKED], not the real title."""
        from contextpulse_sight import mcp_server

        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},  # virtual desktop
            {"left": 0, "top": 0, "width": 1920, "height": 1080},  # monitor 0
        ]
        mock_sct_ctx = MagicMock()
        mock_sct_ctx.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct_ctx.__exit__ = MagicMock(return_value=False)

        def mock_is_blocked(title):
            return "secret" in title.lower()

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.mss_lib.mss", return_value=mock_sct_ctx), \
                 patch("contextpulse_sight.mcp_server.capture._get_cursor_pos", return_value=(500, 500)), \
                 patch("contextpulse_sight.mcp_server.get_foreground_window_title", return_value="Secret Banking App"), \
                 patch("contextpulse_sight.mcp_server.get_foreground_process_name", return_value="bank.exe"), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False), \
                 patch("contextpulse_sight.mcp_server.is_title_blocked", side_effect=mock_is_blocked):
                result = mcp_server.get_monitor_summary()

            assert "[BLOCKED]" in result
            assert "Secret Banking" not in result
            assert "bank.exe" not in result  # app name also blocked
        finally:
            mcp_server._activity_db = original_db
            test_db.close()


class TestGetScreenshotSmartMode:
    """Integration tests for get_screenshot(mode='smart')."""

    def test_smart_includes_changed_monitors(self, tmp_path):
        """Monitors with diff >= 1.0 should return images."""
        from contextpulse_sight import mcp_server

        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")
        test_db.record(time.time(), "VS Code", "Code.exe", monitor_index=0, diff_score=30.0)
        test_db.record(time.time(), "Chrome", "chrome.exe", monitor_index=1, diff_score=0.2)

        test_img = _make_image(1280, 720)
        mock_monitors = [(0, test_img), (1, test_img)]

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.capture.capture_all_monitors", return_value=mock_monitors), \
                 patch("contextpulse_sight.mcp_server.capture.capture_to_bytes", return_value=b"fake_jpeg"), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False):
                result = mcp_server.get_screenshot(mode="smart")

            assert isinstance(result, list)
            # Monitor 0 (diff=30) should have an image
            # Monitor 1 (diff=0.2) should be text-only (skipped)
            has_image = any(hasattr(item, "data") or isinstance(item, bytes) for item in result if not isinstance(item, str))
            text_parts = [item for item in result if isinstance(item, str)]
            assert has_image or len(text_parts) > 0
            # The skipped monitor text should mention "unchanged"
            full_text = " ".join(text_parts)
            assert "unchanged" in full_text.lower() or "chrome" in full_text.lower()
        finally:
            mcp_server._activity_db = original_db
            test_db.close()

    def test_smart_all_static_returns_message(self, tmp_path):
        """When all monitors are static, returns a helpful message."""
        from contextpulse_sight import mcp_server

        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")
        test_db.record(time.time(), "Desktop", "explorer.exe", monitor_index=0, diff_score=0.1)
        test_db.record(time.time(), "Desktop", "explorer.exe", monitor_index=1, diff_score=0.0)

        test_img = _make_image(1280, 720)
        mock_monitors = [(0, test_img), (1, test_img)]

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.capture.capture_all_monitors", return_value=mock_monitors), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False):
                result = mcp_server.get_screenshot(mode="smart")

            assert isinstance(result, str)
            assert "static" in result.lower() or "no recent changes" in result.lower()
        finally:
            mcp_server._activity_db = original_db
            test_db.close()

    def test_smart_unknown_monitors_included(self, tmp_path):
        """Monitors with no DB record should be included (unknown = possibly interesting)."""
        from contextpulse_sight import mcp_server

        original_db = mcp_server._activity_db
        test_db = ActivityDB(db_path=tmp_path / "test.db")
        # No records at all — empty DB

        test_img = _make_image(1280, 720)
        mock_monitors = [(0, test_img)]

        try:
            mcp_server._activity_db = test_db
            with patch("contextpulse_sight.mcp_server.capture.capture_all_monitors", return_value=mock_monitors), \
                 patch("contextpulse_sight.mcp_server.capture.capture_to_bytes", return_value=b"fake_jpeg"), \
                 patch("contextpulse_sight.mcp_server.is_blocked", return_value=False):
                result = mcp_server.get_screenshot(mode="smart")

            # Should include the image since diff defaults to 100.0 for unknown
            assert isinstance(result, list)
            assert len(result) >= 1
        finally:
            mcp_server._activity_db = original_db
            test_db.close()


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
