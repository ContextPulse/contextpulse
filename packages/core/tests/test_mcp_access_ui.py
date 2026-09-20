# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for the MCP Access UI surfaces (tray, settings dialog, first run).

tkinter and pystray are MagicMocks in this package's conftest, so a built
dialog cannot be inspected. The clipboard path is therefore tested for real
and the rest are source guards -- weaker, but they catch the failure that
actually matters here: a token the user has no way to reach, which looks
exactly like a working build until an agent gets a 401.
"""

import inspect
import sys
import types
from pathlib import Path

import pytest
from contextpulse_core import daemon, first_run, mcp_auth, settings

pytestmark = pytest.mark.unit


def test_copy_mcp_token_puts_the_snippet_on_the_clipboard(monkeypatch):
    copied: list[str] = []
    fake = types.ModuleType("pyperclip")
    fake.copy = copied.append
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    monkeypatch.setattr(mcp_auth, "load_or_create_token", lambda *a: "tok-from-file")

    daemon._copy_mcp_token()

    assert len(copied) == 1
    assert "Bearer tok-from-file" in copied[0]
    assert "127.0.0.1:8420/mcp" in copied[0]


def test_copy_mcp_token_survives_a_missing_clipboard(monkeypatch):
    """A clipboard failure must never take the tray callback's thread down."""
    fake = types.ModuleType("pyperclip")

    def boom(_):
        raise RuntimeError("no clipboard on this box")

    fake.copy = boom
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    monkeypatch.setattr(mcp_auth, "load_or_create_token", lambda *a: "tok")

    daemon._copy_mcp_token()  # must not raise


def test_tray_menu_offers_the_token_and_does_not_block_the_pump():
    src = inspect.getsource(daemon.ContextPulseDaemon._create_tray_menu)
    assert "Copy MCP Token" in src
    assert "_copy_mcp_token" in src
    assert "threading.Thread(target=_copy_mcp_token" in src, (
        "clipboard work must run on a spawned thread -- blocking a pystray "
        "menu callback blocks the whole message pump"
    )


def test_macos_menu_offers_the_token():
    # Read the source rather than importing: tray_macos imports rumps at
    # module level, which is macOS-only and absent from the Windows venv.
    src = Path(daemon.__file__).with_name("tray_macos.py").read_text(encoding="utf-8")
    assert "Copy MCP Token" in src
    assert "def copy_mcp_token" in src


def test_settings_has_an_mcp_access_section_that_can_regenerate():
    src = Path(settings.__file__).read_text(encoding="utf-8")
    assert '_section_header(frame, "MCP Access")' in src
    assert "mcp_auth.regenerate_token()" in src
    assert 'mcp_auth.config_snippet("claude-code"' in src


def test_settings_never_writes_the_token_into_config_json():
    """The token lives in its own restricted file, never in config.json."""
    save = inspect.getsource(settings._build_and_run)
    body = save[save.index("def save_and_close"):save.index("# Bottom button bar")]
    assert "token" not in body.lower(), (
        "save_and_close must not persist the token -- config.json is not "
        "permission-restricted and is read by every package"
    )


def test_first_run_tells_the_user_the_agent_needs_a_token():
    src = Path(first_run.__file__).read_text(encoding="utf-8")
    assert "MCP Access" in src
    assert "contextpulse --setup claude-code" in src
    assert "Copy MCP config" in src
