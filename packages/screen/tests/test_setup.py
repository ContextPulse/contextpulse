"""Tests for setup.py — MCP config generator."""

import json
from pathlib import Path
from unittest.mock import patch

from contextpulse_sight.setup import setup_client, _CLIENTS, SERVER_NAME


class TestSetupClient:
    """Test MCP config generation for each client."""

    def test_setup_claude_code(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        with patch.dict(_CLIENTS, {
            "claude-code": {
                "paths": [config_path],
                "key": "mcpServers",
                "description": "Claude Code (test)",
            }
        }), patch("contextpulse_sight.setup._find_command_path",
                   return_value="contextpulse-sight-mcp"):
            result = setup_client("claude-code")
            assert result is True
            assert config_path.exists()

            config = json.loads(config_path.read_text())
            assert SERVER_NAME in config["mcpServers"]
            assert config["mcpServers"][SERVER_NAME]["command"] == "contextpulse-sight-mcp"

    def test_setup_preserves_existing_config(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        existing = {"mcpServers": {"other-server": {"command": "other"}}, "extra": True}
        config_path.write_text(json.dumps(existing))

        with patch.dict(_CLIENTS, {
            "claude-code": {
                "paths": [config_path],
                "key": "mcpServers",
                "description": "Claude Code (test)",
            }
        }), patch("contextpulse_sight.setup._find_command_path",
                   return_value="contextpulse-sight-mcp"):
            setup_client("claude-code")

            config = json.loads(config_path.read_text())
            assert "other-server" in config["mcpServers"]  # preserved
            assert SERVER_NAME in config["mcpServers"]  # added
            assert config["extra"] is True  # preserved

    def test_setup_unknown_client(self):
        result = setup_client("unknown-client")
        assert result is False

    def test_setup_idempotent(self, tmp_path):
        config_path = tmp_path / ".claude.json"
        with patch.dict(_CLIENTS, {
            "claude-code": {
                "paths": [config_path],
                "key": "mcpServers",
                "description": "Claude Code (test)",
            }
        }), patch("contextpulse_sight.setup._find_command_path",
                   return_value="contextpulse-sight-mcp"):
            setup_client("claude-code")
            setup_client("claude-code")  # second call should be no-op

            config = json.loads(config_path.read_text())
            assert SERVER_NAME in config["mcpServers"]
