"""Tests for Touch MCP server tools."""

import json
import sqlite3
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def touch_db(tmp_path):
    """Create a temp DB with touch events."""
    db_path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, timestamp REAL,
            modality TEXT, event_type TEXT, app_name TEXT,
            window_title TEXT, monitor_index INTEGER, payload TEXT,
            correlation_id TEXT, attention_score REAL
        )
    """)

    now = time.time()

    # Typing bursts
    for i in range(3):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'keys', 'typing_burst', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (f"burst_{i}", now - i * 60, json.dumps({
                "char_count": 20 + i * 5, "word_count": 4 + i,
                "duration_ms": 2000, "wpm": 120, "backspace_count": i,
            })),
        )

    # Clicks
    for i in range(2):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'flow', 'click', 'chrome.exe', 'Google', 0, ?, NULL, 0.0)",
            (f"click_{i}", now - i * 30, json.dumps({
                "x": 100 + i * 50, "y": 200, "button": "left", "click_type": "single",
            })),
        )

    # Scrolls
    conn.execute(
        "INSERT INTO events VALUES ('scroll_0', ?, 'flow', 'scroll', 'chrome.exe', 'Google', 0, ?, NULL, 0.0)",
        (now - 10, json.dumps({"x": 100, "y": 200, "dx": 0, "dy": -3})),
    )

    # Correction
    conn.execute(
        "INSERT INTO events VALUES ('corr_0', ?, 'keys', 'correction_detected', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
        (now - 5, json.dumps({
            "original_text": "cube control", "corrected_text": "kubectl",
            "correction_type": "select_replace", "confidence": 0.85,
            "seconds_after_paste": 4.2,
        })),
    )

    conn.commit()
    conn.close()
    return db_path


class TestGetRecentTouchEvents:
    def test_returns_events(self, touch_db):
        from contextpulse_touch.mcp_server import get_recent_touch_events
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_recent_touch_events(seconds=600)
        assert "Recent Touch Events" in result
        assert "BURST" in result
        assert "CLICK" in result

    def test_filter_keyboard(self, touch_db):
        from contextpulse_touch.mcp_server import get_recent_touch_events
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_recent_touch_events(seconds=600, event_types="keyboard")
        assert "BURST" in result
        assert "CLICK" not in result

    def test_filter_mouse(self, touch_db):
        from contextpulse_touch.mcp_server import get_recent_touch_events
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_recent_touch_events(seconds=600, event_types="mouse")
        assert "CLICK" in result

    def test_filter_corrections(self, touch_db):
        from contextpulse_touch.mcp_server import get_recent_touch_events
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_recent_touch_events(seconds=600, event_types="corrections")
        assert "CORRECTION" in result
        assert "kubectl" in result

    def test_no_db(self, tmp_path):
        from contextpulse_touch.mcp_server import get_recent_touch_events
        with patch("contextpulse_touch.mcp_server._DB_PATH", tmp_path / "nope.db"):
            result = get_recent_touch_events()
        assert "No activity database" in result


class TestGetTouchStats:
    def test_returns_stats(self, touch_db):
        from contextpulse_touch.mcp_server import get_touch_stats
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_touch_stats(hours=24)
        assert "Touch Stats" in result
        assert "Typing bursts: 3" in result
        assert "Mouse clicks: 2" in result
        assert "Corrections detected: 1" in result


class TestGetCorrectionHistory:
    def test_returns_corrections(self, touch_db):
        from contextpulse_touch.mcp_server import get_correction_history
        with patch("contextpulse_touch.mcp_server._DB_PATH", touch_db):
            result = get_correction_history()
        assert "Correction History" in result
        assert "cube control" in result
        assert "kubectl" in result

    def test_no_corrections(self, tmp_path):
        db_path = tmp_path / "empty.db"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, timestamp REAL,
                modality TEXT, event_type TEXT, app_name TEXT,
                window_title TEXT, monitor_index INTEGER, payload TEXT,
                correlation_id TEXT, attention_score REAL
            )
        """)
        conn.commit()
        conn.close()

        from contextpulse_touch.mcp_server import get_correction_history
        with patch("contextpulse_touch.mcp_server._DB_PATH", db_path):
            result = get_correction_history()
        assert "No corrections" in result
