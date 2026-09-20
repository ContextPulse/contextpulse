"""Tests for setup.py — MCP config generator.

Rewritten 2026-09-19: setup.py stopped writing a stdio `contextpulse-sight`
entry and now writes the authenticated http entry for the unified endpoint
under the server name `contextpulse`. The assertions below changed with it.

Every test passes an explicit `token=` and `paths=`. A call without them
reads and creates the REAL token file under %APPDATA% and rewrites the real
~/.claude.json, so a test that omits either is not a test, it is a live edit.
"""

import json

from contextpulse_sight.setup import SERVER_NAME, print_config, setup_client

TOKEN = "test-token-abc"


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
