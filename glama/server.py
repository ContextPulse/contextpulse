# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Glama.ai registry stub for ContextPulse.

ContextPulse is a local-only desktop daemon that captures screen, voice, and
input activity on the user's own machine. It cannot function inside a remote
container: there is no screen to capture, no microphone, no keyboard.

This stub satisfies Glama's MCP handshake so the server can be listed in the
registry for discovery. Users install ContextPulse locally and point their
MCP client at localhost:8420/mcp to get the real 35 tools.

Install locally: https://github.com/ContextPulse/contextpulse
"""
from mcp.server.fastmcp import FastMCP

GITHUB_URL = "https://github.com/ContextPulse/contextpulse"
SITE_URL = "https://contextpulse.ai"

TOOLS = [
    ("get_monitor_summary", "List connected displays with resolution + active monitor."),
    ("get_screenshot", "Capture a screenshot of the active or specified monitor."),
    ("get_recent", "Return recent screenshots from the rolling buffer."),
    ("get_screen_text", "Return OCR text from the current screen."),
    ("get_buffer_status", "Report rolling-buffer size, age, and disk usage."),
    ("get_activity_summary", "Summarize activity (apps, focus, OCR hits) over last N hours."),
    ("search_history", "Search OCR history by query string."),
    ("get_context_at", "Return screen + activity context from N minutes ago."),
    ("get_clipboard_history", "Return recent clipboard entries."),
    ("search_clipboard", "Search clipboard history by query."),
    ("identify_project", "Score text against indexed projects and return best match."),
    ("get_active_project", "Detect current project from CWD and/or window title."),
    ("list_projects", "List all indexed projects with overviews."),
    ("get_project_context", "Return full PROJECT_CONTEXT.md for a specific project."),
    ("route_to_journal", "Route text to the correct project's journal."),
    ("get_recent_voice", "Return recent voice transcription segments."),
    ("search_voice", "Search voice transcripts by query."),
    ("get_voice_stats", "Return dictation stats (WPM, error rate, corrections)."),
    ("add_to_vocabulary", "Add a term to the user's voice vocabulary."),
    ("get_vocabulary", "List the user's voice vocabulary terms."),
    ("remove_from_vocabulary", "Remove a term from the voice vocabulary."),
    ("get_recent_touch_events", "Get recent keyboard and mouse activity events."),
    ("get_touch_stats", "Return typing and mouse usage stats over last N hours."),
    ("get_correction_history", "Return recent voice correction detections."),
    ("memory_store", "Store a key/value in the local memory database."),
    ("memory_recall", "Recall a value by key from local memory."),
    ("memory_search", "Full-text search over stored memories."),
    ("memory_semantic_search", "Vector-search over stored memories."),
    ("memory_list", "List stored memory keys, optionally filtered by tag."),
    ("memory_forget", "Delete a memory by key."),
    ("memory_stats", "Return memory database stats (count, size, tags)."),
    ("get_session_summary", "Summarize the current session's activity."),
    ("get_app_usage", "Return per-app time-on-task over a window."),
    ("find_related_context", "Find screen/voice/clipboard context related to a query."),
    ("describe_workspace", "Describe the current desktop workspace state."),
]

_LOCAL_ONLY_MSG = (
    f"ContextPulse is a local-only desktop daemon. Install from {GITHUB_URL} "
    f"and point your MCP client at localhost:8420/mcp to use this tool. "
    f"This Glama stub exists for registry discovery only."
)

mcp_app = FastMCP("ContextPulse")


def _make_stub(tool_name: str, description: str):
    def _stub() -> str:
        return _LOCAL_ONLY_MSG
    _stub.__name__ = tool_name
    _stub.__doc__ = f"{description}\n\n{_LOCAL_ONLY_MSG}"
    return _stub


for name, desc in TOOLS:
    mcp_app.tool(name=name, description=desc)(_make_stub(name, desc))


@mcp_app.tool()
def about() -> str:
    """About ContextPulse - local-first ambient context for AI agents."""
    return (
        "ContextPulse is a local desktop daemon that captures screen (OCR), voice "
        "(Whisper), keyboard/mouse activity, and clipboard, then serves the data to "
        f"AI agents over MCP. All processing is local. Install: {GITHUB_URL} "
        f"Site: {SITE_URL}"
    )


if __name__ == "__main__":
    mcp_app.run(transport="stdio")
