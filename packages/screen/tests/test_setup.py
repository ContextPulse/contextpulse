"""Tests for setup.py — MCP config generator.

Rewritten 2026-09-19: setup.py stopped writing a stdio `contextpulse-sight`
entry and now writes the authenticated http entry for the unified endpoint
under the server name `contextpulse`. The assertions below changed with it.

Every test passes an explicit `token=` and `paths=`. A call without them
reads and creates the REAL token file under %APPDATA% and rewrites the real
~/.claude.json, so a test that omits either is not a test, it is a live edit.
"""

import json
import os
from pathlib import Path

from contextpulse_sight import setup as setup_mod
from contextpulse_sight.setup import (
    _CLIENTS,
    BACKUP_SUFFIX,
    KNOWN_CLIENTS,
    SERVER_NAME,
    print_config,
    setup_client,
)

TOKEN = "test-token-abc"


class TestNothingIsWrittenRelativeToCwd:
    """B1-1. Cursor's path was `Path.cwd() / ".cursor" / "mcp.json"`, so
    `contextpulse --setup claude-code` run from the ContextPulse checkout
    dropped a live bearer token into the working tree of a public AGPL repo.
    """

    def test_every_declared_client_path_is_under_home(self):
        home = str(Path.home().resolve()).lower()
        for name, client in _CLIENTS.items():
            for path in client["paths"]:
                assert path.is_absolute(), f"{name}: {path} is relative"
                assert str(path.resolve()).lower().startswith(home + os.sep), (
                    f"{name}: {path} is outside the user's home directory"
                )

    def test_no_declared_client_path_is_under_the_cwd(self, monkeypatch, tmp_path):
        """The paths are resolved at import, so cwd must not appear in them."""
        monkeypatch.chdir(tmp_path)
        for name, client in _CLIENTS.items():
            for path in client["paths"]:
                assert Path.cwd() not in path.parents, f"{name}: {path} is under cwd"

    def test_cursor_uses_the_global_config_not_the_project_one(self):
        assert _CLIENTS["cursor"]["paths"] == [Path.home() / ".cursor" / "mcp.json"]

    def test_a_cwd_relative_path_is_refused_and_writes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        target = Path(".cursor") / "mcp.json"
        assert setup_client("cursor", token=TOKEN, paths=[target]) is False
        assert list(tmp_path.iterdir()) == [], "a refused write still created something"

    def test_an_absolute_path_inside_the_cwd_is_refused(self, tmp_path, monkeypatch):
        """The real B1-1 shape: an absolute path that happens to be the cwd."""
        work = tmp_path / "checkout"
        work.mkdir()
        monkeypatch.chdir(work)
        target = work / ".cursor" / "mcp.json"
        assert setup_client("cursor", token=TOKEN, paths=[target]) is False
        assert not target.exists()
        assert list(work.iterdir()) == []

    def test_a_home_relative_path_is_allowed_when_run_from_home(self, tmp_path, monkeypatch):
        """Running from ~ must not lock the user out of their own config."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.chdir(home)
        target = home / ".claude.json"
        assert setup_client("claude-code", token=TOKEN, paths=[target]) is True
        assert target.exists()

    def test_known_clients_is_what_the_cli_dispatches_on(self):
        assert set(KNOWN_CLIENTS) == set(_CLIENTS)
        assert "claude-code" in KNOWN_CLIENTS


class TestAtomicWriteWithBackup:
    """B1-2. ~/.claude.json is Claude Code's entire user state."""

    def test_existing_config_is_backed_up_before_being_replaced(self, tmp_path):
        cfg = tmp_path / ".claude.json"
        original = json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1})
        cfg.write_text(original, encoding="utf-8")

        setup_client("claude-code", token=TOKEN, paths=[cfg])

        backups = list(tmp_path.glob(f".claude.json.*{BACKUP_SUFFIX}"))
        assert len(backups) == 1, backups
        assert json.loads(backups[0].read_text(encoding="utf-8")) == json.loads(original)

    def test_only_one_backup_generation_is_kept(self, tmp_path):
        cfg = tmp_path / ".claude.json"
        cfg.write_text("{}", encoding="utf-8")
        for i in range(3):
            cfg.write_text(json.dumps({"round": i}), encoding="utf-8")
            setup_client("claude-code", token=TOKEN, paths=[cfg])
        assert len(list(tmp_path.glob(f".claude.json.*{BACKUP_SUFFIX}"))) == 1

    def test_no_backup_is_made_when_there_was_no_file(self, tmp_path):
        cfg = tmp_path / ".claude.json"
        setup_client("claude-code", token=TOKEN, paths=[cfg])
        assert list(tmp_path.glob(f"*{BACKUP_SUFFIX}")) == []

    def test_the_write_goes_through_os_replace_and_leaves_no_tmp(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".claude.json"
        cfg.write_text("{}", encoding="utf-8")
        replaced: list[tuple] = []
        real_replace = os.replace

        def spy(src, dst):
            replaced.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(setup_mod.os, "replace", spy)
        setup_client("claude-code", token=TOKEN, paths=[cfg])

        assert len(replaced) == 1, "config was not written through os.replace"
        src, dst = replaced[0]
        assert Path(src).parent == Path(dst).parent, (
            "os.replace is only atomic within one directory"
        )
        assert not list(tmp_path.glob("*.tmp"))

    def test_a_failed_write_leaves_the_original_intact(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".claude.json"
        cfg.write_text('{"precious": true}', encoding="utf-8")

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(setup_mod.os, "replace", boom)
        try:
            setup_client("claude-code", token=TOKEN, paths=[cfg])
        except OSError:
            pass
        assert json.loads(cfg.read_text(encoding="utf-8")) == {"precious": True}
        assert not list(tmp_path.glob("*.tmp"))


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSetupClient:
    """Test MCP config generation for each client."""

    def test_setup_claude_code_writes_http_entry_with_auth_header(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        assert setup_client("claude-code", token=TOKEN, paths=[config_path]) is True
        assert config_path.exists()

        entry = _read(config_path)["mcpServers"][SERVER_NAME]
        assert entry["type"] == "http"
        assert entry["url"] == "http://127.0.0.1:8420/mcp"
        assert entry["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert "command" not in entry, "the stdio entry must not come back"

    def test_setup_preserves_existing_config(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        config_path.write_text(
            json.dumps({"mcpServers": {"other-server": {"command": "other"}}, "extra": True}),
            encoding="utf-8",
        )
        setup_client("claude-code", token=TOKEN, paths=[config_path])

        config = _read(config_path)
        assert config["mcpServers"]["other-server"] == {"command": "other"}
        assert SERVER_NAME in config["mcpServers"]
        assert config["extra"] is True

    def test_setup_unknown_client(self):
        assert setup_client("unknown-client") is False

    def test_setup_idempotent(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        setup_client("claude-code", token=TOKEN, paths=[config_path])
        first = config_path.read_text(encoding="utf-8")
        setup_client("claude-code", token=TOKEN, paths=[config_path])
        assert config_path.read_text(encoding="utf-8") == first

    def test_setup_cursor_omits_the_type_key(self, tmp_path):
        """Cursor infers the transport from the URL and rejects nothing else."""
        config_path = tmp_path / "mcp.json"
        setup_client("cursor", token=TOKEN, paths=[config_path])
        entry = _read(config_path)["mcpServers"][SERVER_NAME]
        assert set(entry) == {"url", "headers"}

    def test_setup_gemini_uses_httpurl(self, tmp_path):
        config_path = tmp_path / "settings.json"
        setup_client("gemini", token=TOKEN, paths=[config_path])
        entry = _read(config_path)["mcpServers"][SERVER_NAME]
        assert entry["httpUrl"] == "http://127.0.0.1:8420/mcp"

    def test_setup_claude_desktop_shells_out_to_mcp_remote(self, tmp_path):
        config_path = tmp_path / "claude_desktop_config.json"
        setup_client("claude-desktop", token=TOKEN, paths=[config_path])
        entry = _read(config_path)["mcpServers"][SERVER_NAME]
        assert entry["command"] == "npx"
        assert entry["env"]["AUTH_HEADER"] == f"Bearer {TOKEN}"


class TestPrintConfig:
    def test_print_config_names_every_client(self, capsys, monkeypatch):
        monkeypatch.setattr(
            "contextpulse_sight.setup.mcp_auth.load_or_create_token", lambda *a: TOKEN
        )
        print_config()
        out = capsys.readouterr().out
        for client in ("Claude Code", "Cursor", "Gemini", "Claude Desktop"):
            assert client in out
        assert TOKEN in out

    def test_print_config_writes_nothing(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "contextpulse_sight.setup.mcp_auth.load_or_create_token", lambda *a: TOKEN
        )
        print_config("claude-code")
        assert capsys.readouterr().out.count("mcpServers") == 1
        assert not list(tmp_path.iterdir())
