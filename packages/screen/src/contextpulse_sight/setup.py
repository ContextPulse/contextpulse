# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MCP config generator — points MCP clients at the unified ContextPulse endpoint.

Usage:
    contextpulse --setup                 # configure every auto-configurable client
    contextpulse --setup print           # print the snippets instead of writing

What it writes, under the server name `contextpulse`:

    "contextpulse": {
      "type": "http",
      "url": "http://127.0.0.1:8420/mcp",
      "headers": { "Authorization": "Bearer <this install's token>" }
    }

Until 2026-09-19 this wrote a stdio `contextpulse-sight` entry pointing at
`contextpulse-sight-mcp`. That was two things wrong at once: it named a
per-package server that the unified endpoint replaced (each stdio server
re-loads the whisper and OCR models in its own process), and it predated the
bearer token, so a client configured by --setup could not authenticate. The
stale entry is removed when found.
"""

import json
from pathlib import Path

from contextpulse_core import mcp_auth

# Client config file locations and formats.
#   auto=False  -> configured only on explicit request, never by setup_all().
#                  Claude Desktop's entry shells out to `npx mcp-remote`, so
#                  writing it unasked would hand a user a config that fails
#                  unless Node is installed.
_CLIENTS = {
    "claude-code": {
        "paths": [Path.home() / ".claude.json"],
        "key": "mcpServers",
        "description": "Claude Code (global)",
        "auto": True,
    },
    "cursor": {
        "paths": [Path.cwd() / ".cursor" / "mcp.json"],
        "key": "mcpServers",
        "description": "Cursor (project-level)",
        "auto": True,
    },
    "gemini": {
        "paths": [Path.home() / ".gemini" / "settings.json"],
        "key": "mcpServers",
        "description": "Gemini CLI (global)",
        "auto": True,
    },
    "claude-desktop": {
        "paths": [Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"],
        "key": "mcpServers",
        "description": "Claude Desktop (via npx mcp-remote)",
        "auto": False,
    },
}

SERVER_NAME = mcp_auth.SERVER_NAME  # "contextpulse"

# Server names THIS tool wrote in earlier builds, removed on sight so a client
# does not end up with two ContextPulse servers fighting over the same tool
# names. Deliberately only the name we wrote: contextpulse-memory and
# contextpulse-project entries can only exist because a user made them by
# hand, and deleting a user's own config is not this function's business.
_LEGACY_SERVER_NAMES = ("contextpulse-sight",)


def setup_client(
    client_name: str,
    token: str | None = None,
    paths: list[Path] | None = None,
) -> bool:
    """Write the ContextPulse MCP entry into one client's config file.

    Merges: every other server in the file is preserved byte for byte, and
    only the `contextpulse` key is replaced. Returns True on success.

    `paths` exists so tests can target a scratch file; it is not reachable
    from the CLI and does not bypass anything.
    """
    client_name = client_name.lower().strip()
    if client_name not in _CLIENTS:
        print(f"Unknown client: {client_name}")
        print(f"Supported clients: {', '.join(_CLIENTS)}")
        return False

    client = _CLIENTS[client_name]
    if token is None:
        token = mcp_auth.load_or_create_token()
    server_entry = mcp_auth.server_entry(client_name, token=token)

    wrote_any = False
    for config_path in (paths if paths is not None else client["paths"]):
        existing: dict = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                # Never overwrite a config we could not parse -- it is the
                # user's file and it may hold every other server they use.
                print(f"ERROR: could not parse {config_path}: {e}")
                print("  Left it untouched. Fix the JSON, or add this by hand:")
                print(json.dumps({SERVER_NAME: server_entry}, indent=2))
                return False
            if not isinstance(existing, dict):
                print(f"ERROR: {config_path} is not a JSON object. Left it untouched.")
                return False

        servers = existing.setdefault(client["key"], {})
        removed = [name for name in _LEGACY_SERVER_NAMES if servers.pop(name, None) is not None]
        unchanged = servers.get(SERVER_NAME) == server_entry and not removed
        servers[SERVER_NAME] = server_entry

        if unchanged:
            print(f"Already configured: {client['description']} ({config_path})")
            wrote_any = True
            continue

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(f"Configured {client['description']}: {config_path}")
        print(f"  Server: {SERVER_NAME} -> {mcp_auth.endpoint_url()}")
        print("  Auth:   Authorization: Bearer <token>")
        for name in removed:
            print(f"  Removed stale entry: {name}")
        wrote_any = True

    return wrote_any


def setup_all() -> None:
    """Configure every client that is safe to configure unattended."""
    token = mcp_auth.load_or_create_token()
    for name, client in _CLIENTS.items():
        if not client["auto"]:
            continue
        print(f"\n--- {client['description']} ---")
        setup_client(name, token=token)

    print(f"\nAccess token: {mcp_auth.TOKEN_FILE}")
    print("Restart the ContextPulse MCP server, then reconnect the client.")
    skipped = [c["description"] for c in _CLIENTS.values() if not c["auto"]]
    if skipped:
        print(f"Not configured automatically: {', '.join(skipped)}")
        print("  Run:  contextpulse-mcp --print-config claude-desktop")


def print_config(client_name: str | None = None) -> None:
    """Print config snippets without writing anything."""
    token = mcp_auth.load_or_create_token()
    names = [client_name.lower().strip()] if client_name else list(_CLIENTS)
    for name in names:
        if name not in _CLIENTS:
            print(f"Unknown client: {name}")
            print(f"Supported clients: {', '.join(_CLIENTS)}")
            return
        print(f"\n--- {_CLIENTS[name]['description']} ---")
        print(mcp_auth.config_snippet(name, token=token))
    print(f"\n# Access token file: {mcp_auth.TOKEN_FILE}")
