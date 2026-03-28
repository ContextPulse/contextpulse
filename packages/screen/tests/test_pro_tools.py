"""Tests for Pro-gated MCP tools: search_all_events, get_event_timeline, and _require_pro."""

import time
from unittest.mock import patch

import pytest
from contextpulse_core.spine import EventBus
from contextpulse_sight.sight_module import SightModule


@pytest.fixture
def bus(tmp_path):
    b = EventBus(tmp_path / "test.db")
    yield b
    b.close()


@pytest.fixture
def populated_bus(bus):
    """EventBus with a mix of events across modalities."""
    now = time.time()
    module = SightModule()
    module.register(bus.emit)
    module.start()

    # Screen capture event
    module.emit_capture(
        timestamp=now - 120,
        app_name="VS Code",
        window_title="main.py — ContextPulse",
        monitor_index=0,
        frame_path="/tmp/frame1.jpg",
        diff_score=0.08,
    )

    # OCR result
    module.emit_ocr(
        timestamp=now - 100,
        frame_path="/tmp/frame1.jpg",
        ocr_text="def search_all_events(query):",
        confidence=0.95,
        app_name="VS Code",
        window_title="main.py — ContextPulse",
    )

    # Clipboard event
    module.emit_clipboard(
        timestamp=now - 60,
        text="ImportError: no module named contextpulse",
        hash_val="abc123",
        source_app="Terminal",
    )

    # Window focus
    module.emit_window_focus("Chrome", "GitHub Pull Requests")

    # Session lock/unlock
    module.emit_session_lock(locked=True)
    module.emit_session_lock(locked=False)

    module.stop()
    return bus


class TestRequireProDecorator:
    """Test the _require_pro gating decorator.

    The decorator now uses has_pro_access() which grants access when:
    - User has a valid license (starter or pro tier), OR
    - User is within their 7-day trial period
    """

    def test_blocks_when_no_access(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_tool():
            return "success"

        with (
            patch("contextpulse_sight.mcp_server.has_pro_access", return_value=False),
            patch("contextpulse_sight.mcp_server.get_license_tier", return_value=""),
        ):
            result = my_tool()
            assert "Pro license" in result
            assert "free" in result

    def test_blocks_expired_no_trial(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_tool():
            return "success"

        with (
            patch("contextpulse_sight.mcp_server.has_pro_access", return_value=False),
            patch("contextpulse_sight.mcp_server.get_license_tier", return_value=""),
        ):
            result = my_tool()
            assert "Pro license" in result

    def test_allows_pro_tier(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_tool():
            return "success"

        with patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True):
            result = my_tool()
            assert result == "success"

    def test_allows_starter_tier(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_tool():
            return "success"

        with patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True):
            result = my_tool()
            assert result == "success"

    def test_allows_during_trial(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_tool():
            return "success"

        with patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True):
            result = my_tool()
            assert result == "success"

    def test_preserves_function_name(self):
        from contextpulse_sight.mcp_server import _require_pro

        @_require_pro
        def my_special_tool():
            """My docstring."""
            return "ok"

        assert my_special_tool.__name__ == "my_special_tool"
        assert my_special_tool.__doc__ == "My docstring."


class TestSearchAllEvents:
    """Test search_all_events MCP tool."""

    def test_returns_results_for_matching_query(self, populated_bus):
        from contextpulse_sight import mcp_server

        # Patch the event bus and license tier
        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.search_all_events("search_all_events")
            assert "Cross-Modal Search" in result
            assert "search_all_events" in result

    def test_returns_no_results_message(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.search_all_events("zzz_nonexistent_term_zzz")
            assert "No results" in result

    def test_filters_by_modality(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.search_all_events("ImportError", modality="clipboard")
            assert "Cross-Modal Search" in result

    def test_blocked_by_free_tier(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=False),
            patch.object(mcp_server, "get_license_tier", return_value=""),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.search_all_events("test")
            assert "Pro license" in result


class TestGetEventTimeline:
    """Test get_event_timeline MCP tool."""

    def test_returns_timeline(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.get_event_timeline(minutes_ago=5)
            assert "Event Timeline" in result
            assert "events" in result.lower()

    def test_shows_modality_counts(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.get_event_timeline(minutes_ago=5)
            assert "Modalities:" in result

    def test_filters_by_modality(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.get_event_timeline(minutes_ago=5, modality="sight")
            # Should only contain sight events
            assert "Event Timeline" in result

    def test_empty_timeline(self, bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", bus),
            patch.object(mcp_server, "has_pro_access", return_value=True),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.get_event_timeline(minutes_ago=5)
            assert "No events" in result

    def test_blocked_by_free_tier(self, populated_bus):
        from contextpulse_sight import mcp_server

        with (
            patch.object(mcp_server, "_event_bus", populated_bus),
            patch.object(mcp_server, "has_pro_access", return_value=False),
            patch.object(mcp_server, "get_license_tier", return_value=""),
            patch.object(mcp_server, "_activity_db") as mock_db,
        ):
            mock_db.record_mcp_call = lambda **kw: None
            result = mcp_server.get_event_timeline(minutes_ago=5)
            assert "Pro license" in result
