"""Verify that mcp_server.py registers exactly 12 tools with correct names.

Imports contextpulse_sight.mcp_server and inspects the FastMCP app to confirm
all 10 free tools + 2 Pro-gated tools are present and correctly named.
"""

import asyncio

from contextpulse_sight import mcp_server as _mcp_server_mod

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
# Helper: synchronously get registered tool names from FastMCP app
# ---------------------------------------------------------------------------

def _get_tool_names() -> set[str]:
    """Return the set of tool names registered with the FastMCP app."""
    app = _mcp_server_mod.mcp_app

    # FastMCP exposes tools via async list_tools() coroutine (MCP spec)
    try:
        tools = asyncio.run(app.list_tools())
        return {t.name for t in tools}
    except Exception:
        pass

    # Fallback: inspect internal tool manager (implementation detail)
    for attr in ("_tool_manager", "_tools", "tools"):
        manager = getattr(app, attr, None)
        if manager is None:
            continue
        if hasattr(manager, "tools") and isinstance(manager.tools, dict):
            return set(manager.tools.keys())
        if isinstance(manager, dict):
            return set(manager.keys())

    raise RuntimeError(
        "Could not determine registered tools from FastMCP app. "
        "Check FastMCP API compatibility."
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMcpServerToolRegistration:
    def test_exactly_12_tools_registered(self):
        names = _get_tool_names()
        assert len(names) == 12, (
            f"Expected 12 tools, got {len(names)}: {sorted(names)}"
        )

    def test_all_free_tools_present(self):
        names = _get_tool_names()
        missing = EXPECTED_FREE_TOOLS - names
        assert not missing, f"Missing free tools: {missing}"

    def test_all_pro_tools_present(self):
        names = _get_tool_names()
        missing = EXPECTED_PRO_TOOLS - names
        assert not missing, f"Missing pro tools: {missing}"

    def test_no_unexpected_tools(self):
        names = _get_tool_names()
        unexpected = names - EXPECTED_ALL_TOOLS
        assert not unexpected, f"Unexpected tools registered: {unexpected}"

    def test_get_screenshot_registered(self):
        assert "get_screenshot" in _get_tool_names()

    def test_get_recent_registered(self):
        assert "get_recent" in _get_tool_names()

    def test_get_screen_text_registered(self):
        assert "get_screen_text" in _get_tool_names()

    def test_get_buffer_status_registered(self):
        assert "get_buffer_status" in _get_tool_names()

    def test_get_activity_summary_registered(self):
        assert "get_activity_summary" in _get_tool_names()

    def test_search_history_registered(self):
        assert "search_history" in _get_tool_names()

    def test_get_context_at_registered(self):
        assert "get_context_at" in _get_tool_names()

    def test_get_clipboard_history_registered(self):
        assert "get_clipboard_history" in _get_tool_names()

    def test_search_clipboard_registered(self):
        assert "search_clipboard" in _get_tool_names()

    def test_get_agent_stats_registered(self):
        assert "get_agent_stats" in _get_tool_names()

    def test_search_all_events_registered(self):
        assert "search_all_events" in _get_tool_names()

    def test_get_event_timeline_registered(self):
        assert "get_event_timeline" in _get_tool_names()

    def test_mcp_app_name_is_contextpulse_sight(self):
        """Verify the FastMCP server is named correctly."""
        app = _mcp_server_mod.mcp_app
        name = getattr(app, "name", None) or getattr(
            getattr(app, "_mcp_server", None), "name", None
        )
        assert name == "ContextPulse Sight"
