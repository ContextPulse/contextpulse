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
from pathlib import Path

# isort: off
# Import _thread_caps FIRST so OMP/MKL/OPENBLAS/NUMEXPR env vars are set
# before transitive imports (memory/voice tool modules) pull in numpy /
# faster-whisper / sentence-transformers and eagerly allocate worker pools.
# Without this, the daemon spawns ~163 baseline threads (incident: 2026-04-29).
from contextpulse_core import _thread_caps  # noqa: F401  side-effect; must be first
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from contextpulse_core import mcp_auth
# isort: on

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


def _register_probe_tools():
    # THROWAWAY — Phase 0 wedge probe (facts_about / context_at). Retired at the
    # Phase-1 save-gated cut-over, superseded by the knowledge tools below.
    from contextpulse_core.probe_mcp import mcp_app as probe_app
    _import_tools(probe_app, "Probe")


def _register_knowledge_tools():
    # Phase 1 knowledge graph (facts_about / context_at / kg_timeline /
    # search_knowledge). Replaces the probe tools when knowledge_enabled.
    from contextpulse_knowledge.mcp_tools import mcp_app as knowledge_app
    _import_tools(knowledge_app, "Knowledge")


def _register_all():
    """Import and register tools from all packages.

    Each package's mcp_server.py defines tools on its own FastMCP instance.
    We copy the tool registrations into the unified app so all tools are
    served from a single HTTP endpoint.

    The knowledge graph and the Phase-0 probe both define facts_about /
    context_at, so exactly one is registered: the KG when knowledge_enabled,
    otherwise the probe (default). Registering both would collide on tool names.
    """
    from contextpulse_core import config

    if config.get("knowledge_enabled", False):
        recall_tools = ("knowledge", _register_knowledge_tools)
    else:
        recall_tools = ("probe", _register_probe_tools)

    errors = []
    for name, register_fn in [
        ("sight", _register_sight_tools),
        ("project", _register_project_tools),
        ("voice", _register_voice_tools),
        ("touch", _register_touch_tools),
        ("memory", _register_memory_tools),
        recall_tools,
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


def build_transport_security(port: int) -> TransportSecuritySettings:
    """Host/Origin allow-list for the streamable-http endpoint.

    FastMCP auto-enables this when host is one of three exact strings, but the
    default is silent, untested by us, and disappears the moment anyone binds
    a different host. Passing it explicitly makes the protection a property of
    ContextPulse rather than a side effect of the library, and pins the port
    instead of wildcarding it.

    Effects: a Host header that is not one of these -> 421; an Origin header
    that is present and not localhost -> 403; a POST that is not
    application/json -> 400. All three are enforced by the library on every
    request (mcp/server/transport_security.py).
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


def build_http_app(fastmcp_app: FastMCP, no_auth: bool = False, token_file: Path | None = None):
    """Return the ASGI app to serve: the streamable-http app, bearer-gated.

    FastMCP's own streamable-http entry point gives no seam to insert a
    wrapper into. It is only uvicorn around streamable_http_app() (verified
    against mcp 1.26.0, server/fastmcp/server.py:777-790), so unrolling it
    costs nothing and lets the auth wrapper sit outermost.

    Fails closed: auth is on unless explicitly disabled, and disabling it logs
    a WARNING banner once at startup.
    """
    reason = None
    if no_auth:
        reason = "--no-auth"
    elif mcp_auth.auth_disabled():
        reason = f"{mcp_auth.AUTH_ENV_VAR}=off"

    app = fastmcp_app.streamable_http_app()

    if reason:
        logger.warning(mcp_auth.disabled_banner(reason))
        return app

    token = mcp_auth.load_or_create_token(token_file)
    logger.info(
        "MCP auth enabled -- clients must send 'Authorization: Bearer <token>'. "
        "Token file: %s (re-read on change, so Regenerate takes effect without "
        "a restart)",
        token_file or mcp_auth.TOKEN_FILE,
    )
    return mcp_auth.BearerAuthASGI(app, token, token_file=token_file)


def main():
    global mcp_app

    parser = argparse.ArgumentParser(description="ContextPulse Unified MCP Server")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--stdio", action="store_true", help="Use stdio transport (for testing)")
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Serve without the bearer token (every local process can call every tool)",
    )
    parser.add_argument(
        "--print-config",
        nargs="?",
        const="claude-code",
        choices=["claude-code", "cursor", "gemini", "claude-desktop"],
        metavar="CLIENT",
        help="Print this install's MCP client config (with the access token) and exit",
    )
    args = parser.parse_args()

    if args.print_config:
        # Creates the token if it does not exist yet -- this is the
        # copy-paste path a new user takes before the server ever runs.
        print(mcp_auth.config_snippet(args.print_config, host=args.host, port=args.port))
        return

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
        transport_security=build_transport_security(args.port),
    )

    _register_all()

    if args.stdio:
        # stdio stays unauthenticated by design: the client spawns this
        # process itself, as the same user, over a private pipe. A token
        # would defend against nothing that could not already read the file.
        logger.info("Starting in stdio mode (testing)")
        mcp_app.run(transport="stdio")
    else:
        import uvicorn

        logger.info("Starting unified MCP on http://%s:%d/mcp", args.host, args.port)
        app = build_http_app(mcp_app, no_auth=args.no_auth)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
