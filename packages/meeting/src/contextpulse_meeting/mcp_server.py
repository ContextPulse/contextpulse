# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MCP server for ContextPulse Meeting.

Exposes meeting tools to AI agents via Model Context Protocol:
    - meeting_start / meeting_end — manual meeting control
    - meeting_status — is a meeting active?
    - meeting_summary — get the latest summary
    - meeting_action_items — get extracted action items
    - meeting_timeline — get the correlated timeline
    - meeting_search — search across meeting history
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# MCP tool definitions will follow the same pattern as
# contextpulse_voice.mcp_server and contextpulse_sight.mcp_server.
# Each tool is a function decorated with @server.tool() that
# returns structured data.

# Placeholder — will be implemented after spec is provided.


def main() -> None:
    """Entry point for the MCP server."""
    raise NotImplementedError(
        "MCP server pending rebuild from spec. "
        "Run: contextpulse-meeting-mcp"
    )


if __name__ == "__main__":
    main()
