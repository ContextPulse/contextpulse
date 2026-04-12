# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MCP config generator — auto-configures MCP clients to use ContextPulse Sight.

Usage:
    contextpulse-sight --setup claude-code
    contextpulse-sight --setup cursor
    contextpulse-sight --setup gemini
"""

import json
import shutil
from pathlib import Path

# MCP server config that gets injected into each client's settings
_SERVER_CONFIG = {
    "command": "contextpulse-sight-mcp",
    "args": [],
}

# Client config file locations and formats
_CLIENTS = {
    "claude-code": {
        "paths": [
            Path.home() / ".claude.json",
        ],
        "key": "mcpServers",
        "description": "Claude Code (global)",
    },
    "cursor": {
        "paths": [
            Path.cwd() / ".cursor" / "mcp.json",
        ],
        "key": "mcpServers",
        "description": "Cursor (project-level)",
    },
    "gemini": {
        "paths": [
            Path.home() / ".gemini" / "settings.json",
        ],
        "key": "mcpServers",
        "description": "Gemini CLI (global)",
    },
}

SERVER_NAME = "contextpulse-sight"


def _find_command_path() -> str:
    """Find the full path to contextpulse-sight-mcp executable."""
    path = shutil.which("contextpulse-sight-mcp")
    return path if path else "contextpulse-sight-mcp"


def setup_client(client_name: str) -> bool:
    """Generate and write MCP config for the given client.

    Returns True if config was written successfully.
    """
    client_name = client_name.lower().strip()
    if client_name not in _CLIENTS:
        print(f"Unknown client: {client_name}")
        print(f"Supported clients: {', '.join(_CLIENTS.keys())}")
        return False

    client = _CLIENTS[client_name]
    command_path = _find_command_path()

    server_entry = {
        "command": command_path,
        "args": [],
    }

    for config_path in client["paths"]:
        # Read existing config or start fresh
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: could not parse {config_path}: {e}")
                print("Creating new config file.")

        # Ensure mcpServers key exists
        servers = existing.setdefault(client["key"], {})

        # Check if already configured
        if SERVER_NAME in servers:
            print(f"ContextPulse Sight already configured in {config_path}")
            current = servers[SERVER_NAME]
            if current.get("command") != command_path:
                print(f"  Updating command path: {current.get('command')} -> {command_path}")
                servers[SERVER_NAME] = server_entry
            else:
                print("  No changes needed.")
                return True

        else:
            servers[SERVER_NAME] = server_entry

        # Write config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(existing, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Configured {client['description']}: {config_path}")
        print(f"  Server: {SERVER_NAME}")
        print(f"  Command: {command_path}")
        return True

    return False


def setup_all() -> None:
    """Configure all supported MCP clients."""
    for name in _CLIENTS:
        print(f"\n--- {_CLIENTS[name]['description']} ---")
        setup_client(name)


def print_config(client_name: str | None = None) -> None:
    """Print the MCP config JSON without writing it."""
    command_path = _find_command_path()
    config = {
        "mcpServers": {
            SERVER_NAME: {
                "command": command_path,
                "args": [],
            }
        }
    }

    if client_name:
        print(f"# Add to your {client_name} MCP config:")
    else:
        print("# MCP server config for ContextPulse Sight:")
    print(json.dumps(config, indent=2))
