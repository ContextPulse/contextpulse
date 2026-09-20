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
import os
import sys
import time
from pathlib import Path

from contextpulse_core import mcp_auth


def _claude_desktop_config_path() -> Path:
    """Claude Desktop's config, which is not in the same place on every OS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

# Every config path MUST be user-scoped -- under the user's home directory,
# absolute, and never derived from the current working directory. This file
# writes a live bearer token, and Cursor's path used to be
# `Path.cwd() / ".cursor" / "mcp.json"`: running `contextpulse --setup` from
# the ContextPulse checkout dropped the token into the working tree of a
# public AGPL repo, where the next `git add -A` would stage it.
# _is_user_scoped() below enforces the invariant rather than trusting this
# table to stay correct.
#
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
        # Cursor's global config, per its own docs: "Create ~/.cursor/mcp.json
        # in your home directory for tools available everywhere." The
        # project-level .cursor/mcp.json is deliberately NOT used.
        "paths": [Path.home() / ".cursor" / "mcp.json"],
        "key": "mcpServers",
        "description": "Cursor (global)",
        "auto": True,
    },
    "gemini": {
        "paths": [Path.home() / ".gemini" / "settings.json"],
        "key": "mcpServers",
        "description": "Gemini CLI (global)",
        "auto": True,
    },
    "claude-desktop": {
        "paths": [_claude_desktop_config_path()],
        "key": "mcpServers",
        "description": "Claude Desktop (via npx mcp-remote)",
        "auto": False,
    },
}

SERVER_NAME = mcp_auth.SERVER_NAME  # "contextpulse"

# The names `--setup <client>` accepts. Exported so callers dispatch on the
# same list this module implements, instead of keeping their own copy.
KNOWN_CLIENTS = tuple(_CLIENTS)

# Server names THIS tool wrote in earlier builds, removed on sight so a client
# does not end up with two ContextPulse servers fighting over the same tool
# names. Deliberately only the name we wrote: contextpulse-memory and
# contextpulse-project entries can only exist because a user made them by
# hand, and deleting a user's own config is not this function's business.
_LEGACY_SERVER_NAMES = ("contextpulse-sight",)

BACKUP_SUFFIX = ".contextpulse-bak"


def _under(path: Path, parent: Path) -> bool:
    """Is `path` inside `parent`? Compared as case-insensitively as the disk."""
    return str(path).lower().startswith(str(parent).lower().rstrip(os.sep) + os.sep)


def _is_user_scoped(path: Path) -> bool:
    """Refuse any config path that is relative, or that lands in the cwd.

    The invariant that stops a live bearer token being written into whatever
    directory the command happened to be run from. Checked on every path
    before it is opened, so a future edit to _CLIENTS cannot quietly
    reintroduce a cwd-relative location.

    The one exception is running FROM the home directory, where every
    legitimate target (~/.claude.json, ~/.cursor/mcp.json) is trivially
    "under the cwd" -- there the rule becomes "must be under home", which is
    the same invariant stated the other way round.
    """
    if not path.is_absolute():
        return False
    try:
        resolved = path.resolve()
        cwd = Path.cwd().resolve()
        home = Path.home().resolve()
    except OSError:
        return False
    if cwd == home:
        return _under(resolved, home)
    return not _under(resolved, cwd)


def _write_config_atomically(path: Path, payload: str) -> None:
    """Replace `path` with `payload`, keeping one timestamped backup.

    ~/.claude.json is Claude Code's entire user state -- every project entry,
    its history, and OAuth material. A bare write_text() truncates it first,
    so a kill or a full disk between truncate and flush loses all of it with
    nothing to restore from.

    tmp + os.replace() is atomic on both Windows and POSIX as long as both
    names are in the same directory, which is why the temp file is a sibling
    rather than something under %TEMP%. The backup is written with the same
    user-only permissions as the token file: it is a copy of a credential
    file, so it must not be more readable than the original.
    """
    backup = path.with_name(f"{path.name}.{time.strftime('%Y%m%dT%H%M%S')}{BACKUP_SUFFIX}")
    if path.exists():
        backup.write_bytes(path.read_bytes())
        mcp_auth.restrict_to_user(backup)
        for older in sorted(path.parent.glob(f"{path.name}.*{BACKUP_SUFFIX}"))[:-1]:
            # One generation only -- these are copies of a credential file.
            older.unlink(missing_ok=True)

    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _claude_code_is_running() -> bool:
    """Best-effort: is a `claude` process live right now?

    Claude Code holds ~/.claude.json in memory and rewrites it from that copy,
    so an edit made underneath a running session can be silently dropped on
    its next flush. We cannot stop that; we can tell the user.
    """
    try:
        import psutil
    except ImportError:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in ("claude", "claude.exe"):
                return True
    except Exception:
        return False
    return False


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
        if not _is_user_scoped(config_path):
            print(f"REFUSING to write {config_path}")
            print(
                "  MCP client configs carry a live access token. They belong in your "
                f"home directory ({Path.home()}), never inside the directory you "
                "happen to be running from."
            )
            return False

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
        _write_config_atomically(config_path, json.dumps(existing, indent=2) + "\n")
        print(f"Configured {client['description']}: {config_path}")
        print(f"  Server: {SERVER_NAME} -> {mcp_auth.endpoint_url()}")
        print("  Auth:   Authorization: Bearer <token>")
        for name in removed:
            print(f"  Removed stale entry: {name}")
        if client_name == "claude-code" and _claude_code_is_running():
            print(
                "  WARNING: Claude Code is running. It rewrites this file from memory,\n"
                "  so this entry may be dropped on its next flush. Safer, from that\n"
                "  session:\n"
                "    claude mcp add --transport http -s user contextpulse "
                f"{mcp_auth.endpoint_url()} -H \"Authorization: Bearer <token>\""
            )
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
