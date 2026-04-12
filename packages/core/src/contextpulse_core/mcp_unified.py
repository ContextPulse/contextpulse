# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Unified MCP server for ContextPulse — all tools on a single HTTP endpoint.

Consolidates sight, project, voice, touch, and memory MCP servers into one
long-lived process using streamable-http transport. This eliminates
the per-session stdio process leak where each Claude Code session
spawned 4+ python processes that were never reaped.

Run standalone:
    python -m contextpulse_core.mcp_unified

Or let the daemon watchdog start it alongside the capture daemon.

Architecture:
    Claude Code ──HTTP──▶ localhost:8420/mcp  (this process)
                                │
                                ├── Sight tools   (screenshots, OCR, buffer, search)
                                ├── Project tools (detection, routing, journal)
                                ├── Voice tools   (transcription, vocabulary)
                                ├── Touch tools   (keyboard, mouse, corrections)
                                └── Memory tools  (store, recall, search — license gated)
                                         │
                                    activity.db + memory.db  (written by daemon)
"""

import argparse
import logging
import signal
import sys

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.mcp.unified")

MCP_PORT = 8420


# ── Import and register tools from each package ─────────────────────

mcp_app: FastMCP | None = None  # Created in main() with correct port


def _import_tools(source_app: FastMCP, label: str) -> int:
    """Register all tools from source_app into the unified mcp_app.

    Uses the public add_tool(fn) API for registration so we don't depend on
    SDK internals. The _tools dict is read-only here for iteration — the only
    private access that has no public equivalent.
    """
    tools = source_app._tool_manager._tools
    for tool in tools.values():
        mcp_app._tool_manager.add_tool(tool.fn)
    logger.info("Registered %d %s tools", len(tools), label)
    return len(tools)


def _register_sight_tools():
    from contextpulse_sight.mcp_server import mcp_app as sight_app
    _import_tools(sight_app, "Sight")


def _register_project_tools():
    from contextpulse_project.mcp_server import mcp_app as project_app
    _import_tools(project_app, "Project")


def _register_voice_tools():
    from contextpulse_voice.mcp_server import mcp_app as voice_app
    _import_tools(voice_app, "Voice")


def _register_touch_tools():
    from contextpulse_touch.mcp_server import mcp_app as touch_app
    _import_tools(touch_app, "Touch")


def _register_memory_tools():
    from contextpulse_memory.mcp_server import mcp_app as memory_app
    _import_tools(memory_app, "Memory")


def _register_all():
    """Import and register tools from all packages.

    Each package's mcp_server.py defines tools on its own FastMCP instance.
    We copy the tool registrations into the unified app so all tools are
    served from a single HTTP endpoint.
    """
    errors = []
    for name, register_fn in [
        ("sight", _register_sight_tools),
        ("project", _register_project_tools),
        ("voice", _register_voice_tools),
        ("touch", _register_touch_tools),
        ("memory", _register_memory_tools),
    ]:
        try:
            register_fn()
        except Exception as exc:
            logger.warning("Failed to register %s tools: %s", name, exc)
            errors.append(f"{name}: {exc}")

    total = len(mcp_app._tool_manager._tools)
    logger.info("Unified MCP server: %d tools registered", total)
    if errors:
        logger.warning("Registration errors: %s", "; ".join(errors))


def main():
    global mcp_app

    parser = argparse.ArgumentParser(description="ContextPulse Unified MCP Server")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--stdio", action="store_true", help="Use stdio transport (for testing)")
    args = parser.parse_args()

    # Graceful shutdown on SIGTERM/SIGINT
    def _shutdown(signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Create the FastMCP app with the configured host/port
    mcp_app = FastMCP(
        "ContextPulse",
        host=args.host,
        port=args.port,
        stateless_http=True,  # No per-session state needed — all state is in SQLite
    )

    _register_all()

    if args.stdio:
        logger.info("Starting in stdio mode (testing)")
        mcp_app.run(transport="stdio")
    else:
        logger.info("Starting unified MCP on http://%s:%d/mcp", args.host, args.port)
        mcp_app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
