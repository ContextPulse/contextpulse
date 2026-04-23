"""Tests for ContextPulseDaemon MCP supervisor.

The daemon supervises the unified MCP server subprocess: it spawns
contextpulse-mcp (or python -m contextpulse_core.mcp_unified as fallback)
when port 8420 is free, detects crashes, and respawns with exponential
backoff. If another process already owns the port, the daemon stays
out of the way.
"""

import socket
import time
from unittest.mock import MagicMock, patch

from contextpulse_core.daemon import ContextPulseDaemon, _is_port_bound


def _make_daemon(**overrides):
    """Minimal daemon suitable for supervisor tests — no real modules."""
    with (
        patch("contextpulse_core.daemon.EventBus", return_value=MagicMock()),
        patch.object(ContextPulseDaemon, "_init_sight", lambda self: None),
        patch.object(ContextPulseDaemon, "_init_voice", lambda self: None),
        patch.object(ContextPulseDaemon, "_init_touch", lambda self: None),
    ):
        d = ContextPulseDaemon()
    for k, v in overrides.items():
        setattr(d, k, v)
    return d


class TestIsPortBound:
    def test_returns_true_for_bound_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            assert _is_port_bound(port) is True
        finally:
            s.close()

    def test_returns_false_for_free_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        assert _is_port_bound(port) is False


class TestSupervisorInitialState:
    def test_mcp_proc_none_on_init(self):
        d = _make_daemon()
        assert d._mcp_proc is None

    def test_supervisor_enabled_by_default(self):
        d = _make_daemon()
        assert d._mcp_spawn_disabled is False

    def test_backoff_starts_at_base(self):
        d = _make_daemon()
        assert d._mcp_restart_backoff == 5.0


class TestSpawnMCPServer:
    def test_returns_none_when_disabled(self):
        d = _make_daemon(_mcp_spawn_disabled=True)
        with patch("subprocess.Popen") as mock_popen:
            result = d._spawn_mcp_server()
        assert result is None
        mock_popen.assert_not_called()

    def test_returns_none_when_port_already_bound(self):
        d = _make_daemon()
        with (
            patch("contextpulse_core.daemon._is_port_bound", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            result = d._spawn_mcp_server()
        assert result is None
        mock_popen.assert_not_called()

    def test_spawns_when_port_free(self):
        d = _make_daemon()
        mock_proc = MagicMock()
        with (
            patch("contextpulse_core.daemon._is_port_bound", return_value=False),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = d._spawn_mcp_server()
        assert result is mock_proc
        mock_popen.assert_called_once()


class TestSuperviseMCP:
    def test_spawns_when_no_proc_and_port_free(self):
        d = _make_daemon()
        mock_proc = MagicMock()
        with (
            patch("contextpulse_core.daemon._is_port_bound", return_value=False),
            patch.object(d, "_spawn_mcp_server", return_value=mock_proc) as spawn,
        ):
            d._supervise_mcp()
        spawn.assert_called_once()
        assert d._mcp_proc is mock_proc

    def test_does_not_spawn_when_port_bound_externally(self):
        d = _make_daemon()
        with (
            patch("contextpulse_core.daemon._is_port_bound", return_value=True),
            patch.object(d, "_spawn_mcp_server") as spawn,
        ):
            d._supervise_mcp()
        spawn.assert_not_called()
        assert d._mcp_proc is None

    def test_clears_dead_proc(self):
        d = _make_daemon()
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 1  # nonzero exit
        d._mcp_proc = dead_proc
        d._mcp_last_start = time.time() - 1  # died fast
        with patch.object(d, "_spawn_mcp_server"):
            d._supervise_mcp()
        # Proc is cleared so next tick can decide whether to respawn
        assert d._mcp_proc is None
        # Backoff doubled due to fast crash
        assert d._mcp_restart_backoff > 5.0

    def test_respects_backoff_between_respawns(self):
        d = _make_daemon()
        d._mcp_restart_backoff = 60.0
        d._mcp_last_start = time.time() - 10  # only 10s ago
        with (
            patch("contextpulse_core.daemon._is_port_bound", return_value=False),
            patch.object(d, "_spawn_mcp_server") as spawn,
        ):
            d._supervise_mcp()
        spawn.assert_not_called()

    def test_resets_backoff_after_stable_run(self):
        d = _make_daemon()
        d._mcp_restart_backoff = 120.0
        alive_proc = MagicMock()
        alive_proc.poll.return_value = None  # still running
        d._mcp_proc = alive_proc
        d._mcp_last_start = time.time() - 601  # alive > 10 min
        d._supervise_mcp()
        assert d._mcp_restart_backoff == 5.0

    def test_does_not_reset_backoff_for_short_run(self):
        d = _make_daemon()
        d._mcp_restart_backoff = 120.0
        alive_proc = MagicMock()
        alive_proc.poll.return_value = None
        d._mcp_proc = alive_proc
        d._mcp_last_start = time.time() - 60  # only 1 min alive
        d._supervise_mcp()
        assert d._mcp_restart_backoff == 120.0
