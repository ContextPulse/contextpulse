"""Verify that mcp_server.py registers exactly 12 tools with correct names.

This test imports the mcp_server module and inspects the FastMCP app to confirm
all 10 free tools + 2 Pro-gated tools are present and correctly named.
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Expected tool registry — must stay in sync with PROJECT_CONTEXT.md
# ---------------------------------------------------------------------------

EXPECTED_FREE_TOOLS = {
    "get_screenshot",
    "get_recent",
    "get_screen_text",
    "get_buffer_status",
    "get_activity_summary",
    "search_history",
    "get_context_at",
    "get_clipboard_history",
    "search_clipboard",
    "get_agent_stats",
}

EXPECTED_PRO_TOOLS = {
    "search_all_events",
    "get_event_timeline",
}

EXPECTED_ALL_TOOLS = EXPECTED_FREE_TOOLS | EXPECTED_PRO_TOOLS


# ---------------------------------------------------------------------------
# Fixture: import mcp_server module with all heavy deps mocked
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mcp_module():
    """Import contextpulse_sight.mcp_server with heavy dependencies stubbed."""
    # Stub modules that require hardware/platform access
    stubs = {
        "mss": MagicMock(),
        "pynput": MagicMock(),
        "pynput.keyboard": MagicMock(),
    }

    # Apply stubs for any not already mocked (conftest may have mocked some)
    patches = {}
    for mod, mock in stubs.items():
        if mod not in sys.modules or not isinstance(sys.modules[mod], MagicMock):
            patches[mod] = mock

    with patch.dict(sys.modules, patches):
        # Patch the module-level singletons that do I/O on import
        with patch("contextpulse_sight.mcp_server.RollingBuffer") as mock_buf, \
             patch("contextpulse_sight.mcp_server.ActivityDB") as mock_db:

            mock_buf.return_value = MagicMock()
            mock_db.return_value = MagicMock()

            import importlib
            import contextpulse_sight.mcp_server as mcp_server_mod
            # Force reload to pick up fresh state in case already imported
            importlib.reload(mcp_server_mod)
            yield mcp_server_mod


# ---------------------------------------------------------------------------
# Helper: synchronously get registered tool names from FastMCP app
# ---------------------------------------------------------------------------

def _get_tool_names(mcp_module) -> set[str]:
    """Return the set of tool names registered with the FastMCP app."""
    app = mcp_module.mcp_app

    # FastMCP exposes tools via list_tools() coroutine (MCP spec)
    try:
        tools = asyncio.run(app.list_tools())
        return {t.name for t in tools}
    except Exception:
        pass

    # Fallback: inspect internal tool manager (implementation detail, may change)
    for attr in ("_tool_manager", "_tools", "tools"):
        manager = getattr(app, attr, None)
        if manager is not None:
            if hasattr(manager, "tools"):
                return set(manager.tools.keys())
            if isinstance(manager, dict):
                return set(manager.keys())

    # Last resort: inspect module for functions decorated with tool
    raise RuntimeError(
        "Could not determine registered tools from FastMCP app. "
        "Check FastMCP API compatibility."
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMcpServerToolRegistration:
    def test_exactly_12_tools_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert len(names) == 12, (
            f"Expected 12 tools, got {len(names)}: {sorted(names)}"
        )

    def test_all_free_tools_present(self, mcp_module):
        names = _get_tool_names(mcp_module)
        missing = EXPECTED_FREE_TOOLS - names
        assert not missing, f"Missing free tools: {missing}"

    def test_all_pro_tools_present(self, mcp_module):
        names = _get_tool_names(mcp_module)
        missing = EXPECTED_PRO_TOOLS - names
        assert not missing, f"Missing pro tools: {missing}"

    def test_no_unexpected_tools(self, mcp_module):
        names = _get_tool_names(mcp_module)
        unexpected = names - EXPECTED_ALL_TOOLS
        assert not unexpected, f"Unexpected tools registered: {unexpected}"

    def test_get_screenshot_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_screenshot" in names

    def test_get_recent_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_recent" in names

    def test_get_screen_text_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_screen_text" in names

    def test_get_buffer_status_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_buffer_status" in names

    def test_get_activity_summary_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_activity_summary" in names

    def test_search_history_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "search_history" in names

    def test_get_context_at_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_context_at" in names

    def test_get_clipboard_history_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_clipboard_history" in names

    def test_search_clipboard_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "search_clipboard" in names

    def test_get_agent_stats_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_agent_stats" in names

    def test_search_all_events_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "search_all_events" in names

    def test_get_event_timeline_registered(self, mcp_module):
        names = _get_tool_names(mcp_module)
        assert "get_event_timeline" in names

    def test_mcp_app_name_is_contextpulse_sight(self, mcp_module):
        """Verify the FastMCP server is named correctly."""
        app = mcp_module.mcp_app
        # FastMCP stores name as .name or ._mcp_server.name
        name = getattr(app, "name", None) or getattr(
            getattr(app, "_mcp_server", None), "name", None
        )
        assert name == "ContextPulse Sight"
