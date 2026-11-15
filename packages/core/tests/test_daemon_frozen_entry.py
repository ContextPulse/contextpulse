"""Tests for the installed (PyInstaller) entry paths of contextpulse_core.daemon.

The Windows installer ships one program, ContextPulse.exe (windowed, the tray
daemon) plus ContextPulseCLI.exe (console, same code). Both enter through
daemon.main(), which must:

  * dispatch the licence commands without starting the daemon or taking the
    single-instance lock;
  * run the unified MCP server for ``--mcp-server``, because a frozen build has
    no ``python -m contextpulse_core.mcp_unified`` to call;
  * start that MCP server as a child when the tray daemon runs frozen, since no
    external watchdog exists on a buyer's machine.

conftest.py mocks tkinter / pystray / windll before import.
"""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from contextpulse_core import daemon as d

# ── single-instance name ─────────────────────────────────────────────


def test_instance_name_default(monkeypatch):
    monkeypatch.delenv("CONTEXTPULSE_INSTANCE", raising=False)
    assert d._single_instance_name() == "ContextPulse_SingleInstance"


def test_instance_name_with_suffix(monkeypatch):
    monkeypatch.setenv("CONTEXTPULSE_INSTANCE", "smoke1")
    assert d._single_instance_name() == "ContextPulse_SingleInstance_smoke1"


@pytest.mark.parametrize("bad", ["a b", "../x", "x\\y", "a" * 65, "Global\\x"])
def test_instance_name_rejects_unsafe_suffix(monkeypatch, bad):
    monkeypatch.setenv("CONTEXTPULSE_INSTANCE", bad)
    with pytest.raises(SystemExit):
        d._single_instance_name()


def test_acquire_uses_instance_name(monkeypatch):
    monkeypatch.setenv("CONTEXTPULSE_INSTANCE", "smoke1")
    plat = MagicMock()
    plat.find_contextpulse_processes.return_value = []
    plat.acquire_single_instance_lock.return_value = object()
    monkeypatch.setattr(d, "get_platform_provider", lambda: plat)
    d._acquire_single_instance_or_exit()
    plat.acquire_single_instance_lock.assert_called_once_with("ContextPulse_SingleInstance_smoke1")


# ── main() dispatch ──────────────────────────────────────────────────


def _no_daemon(monkeypatch):
    """Make any attempt to start the real daemon fail the test loudly."""
    boom = MagicMock(side_effect=AssertionError("daemon path must not run"))
    monkeypatch.setattr(d, "_acquire_single_instance_or_exit", boom)
    monkeypatch.setattr(d, "_refuse_if_session_0", boom)
    monkeypatch.setattr(d, "ContextPulseDaemon", boom)
    monkeypatch.setattr(d, "_setup_logging", lambda: None)


@pytest.mark.parametrize("argv", [["--license-status"], ["--activate-license", "k.txt"]])
def test_main_dispatches_licence_commands_without_the_daemon(monkeypatch, argv):
    _no_daemon(monkeypatch)
    seen = {}

    import contextpulse_core.license_cli as lc

    def fake_main(a):
        seen["argv"] = a
        return 0

    monkeypatch.setattr(lc, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["ContextPulseCLI.exe", *argv])
    with pytest.raises(SystemExit) as exc:
        d.main()
    assert exc.value.code == 0
    assert seen["argv"] == argv


def test_main_licence_exit_code_propagates(monkeypatch):
    _no_daemon(monkeypatch)
    import contextpulse_core.license_cli as lc

    monkeypatch.setattr(lc, "main", lambda a: 2)
    monkeypatch.setattr(sys, "argv", ["x", "--activate-license", "bad.txt"])
    with pytest.raises(SystemExit) as exc:
        d.main()
    assert exc.value.code == 2


def test_main_mcp_server_runs_unified_with_rest_of_argv(monkeypatch):
    _no_daemon(monkeypatch)
    import contextpulse_core.mcp_unified as mu

    seen = {}
    monkeypatch.setattr(mu, "main", lambda: seen.setdefault("argv", list(sys.argv)))
    monkeypatch.setattr(sys, "argv", ["ContextPulse.exe", "--mcp-server", "--port", "18420"])
    d.main()
    assert seen["argv"] == ["ContextPulse.exe", "--port", "18420"]


# ── bundled MCP child ────────────────────────────────────────────────


def test_bundled_mcp_command_default_port(monkeypatch):
    monkeypatch.delenv("CONTEXTPULSE_MCP_PORT", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\ContextPulse\ContextPulse.exe")
    assert d._bundled_mcp_command() == [
        r"C:\Apps\ContextPulse\ContextPulse.exe", "--mcp-server", "--port", "8420",
    ]


def test_bundled_mcp_command_port_override(monkeypatch):
    monkeypatch.setenv("CONTEXTPULSE_MCP_PORT", "18420")
    assert d._bundled_mcp_command()[-1] == "18420"


@pytest.mark.parametrize("bad", ["0", "70000", "abc", "-1"])
def test_bundled_mcp_command_bad_port_falls_back(monkeypatch, bad):
    monkeypatch.setenv("CONTEXTPULSE_MCP_PORT", bad)
    assert d._bundled_mcp_command()[-1] == "8420"


def _bare_daemon():
    dm = d.ContextPulseDaemon.__new__(d.ContextPulseDaemon)
    dm._mcp_proc = None
    dm._mcp_restarts = 0
    return dm


def test_mcp_child_not_started_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    popen = MagicMock()
    monkeypatch.setattr(d.subprocess, "Popen", popen)
    dm = _bare_daemon()
    dm._start_bundled_mcp_server()
    popen.assert_not_called()
    assert dm._mcp_proc is None


def test_mcp_child_started_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    proc = MagicMock()
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr(d.subprocess, "Popen", popen)
    dm = _bare_daemon()
    dm._start_bundled_mcp_server()
    assert dm._mcp_proc is proc
    cmd = popen.call_args.args[0]
    assert cmd[1] == "--mcp-server"
    if sys.platform == "win32":
        assert popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_mcp_child_spawn_failure_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(d.subprocess, "Popen", MagicMock(side_effect=OSError("denied")))
    dm = _bare_daemon()
    dm._module_errors = {}
    dm._start_bundled_mcp_server()
    assert dm._mcp_proc is None
    assert "mcp" in dm._module_errors


def test_mcp_child_restarted_when_dead_up_to_cap(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    dead = MagicMock()
    dead.poll.return_value = 1
    popen = MagicMock(return_value=dead)
    monkeypatch.setattr(d.subprocess, "Popen", popen)
    dm = _bare_daemon()
    dm._module_errors = {}
    dm._mcp_proc = dead
    for _ in range(10):
        dm._check_bundled_mcp_server()
    assert popen.call_count == d.MCP_MAX_RESTARTS


def test_mcp_child_alive_is_left_alone(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    alive = MagicMock()
    alive.poll.return_value = None
    popen = MagicMock()
    monkeypatch.setattr(d.subprocess, "Popen", popen)
    dm = _bare_daemon()
    dm._mcp_proc = alive
    dm._check_bundled_mcp_server()
    popen.assert_not_called()


def test_stop_terminates_child():
    dm = _bare_daemon()
    proc = MagicMock()
    proc.poll.return_value = None
    dm._mcp_proc = proc
    dm._stop_bundled_mcp_server()
    proc.terminate.assert_called_once()
    assert dm._mcp_proc is None


def test_stop_kills_child_that_ignores_terminate():
    dm = _bare_daemon()
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("x", 5), 0]
    dm._mcp_proc = proc
    dm._stop_bundled_mcp_server()
    proc.kill.assert_called_once()


def test_quit_stops_child(monkeypatch):
    dm = _bare_daemon()
    called = []
    monkeypatch.setattr(dm, "_stop_modules", lambda: None, raising=False)
    monkeypatch.setattr(dm, "_stop_bundled_mcp_server", lambda: called.append(1), raising=False)
    dm._mutex = None
    dm.tray = None
    dm._quit()
    assert called == [1]


# ── windowed std streams ─────────────────────────────────────────────


def test_ensure_std_streams_replaces_none(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = tmp_path / "x.log"
    d._ensure_std_streams(log)
    print("hello")
    sys.stderr.write("err\n")
    sys.stdout.flush()
    sys.stderr.flush()
    assert sys.stdout.isatty() is False  # uvicorn calls this
    assert "hello" in log.read_text(encoding="utf-8")


def test_ensure_std_streams_keeps_real_streams(monkeypatch, tmp_path):
    out = sys.stdout
    d._ensure_std_streams(tmp_path / "x.log")
    assert sys.stdout is out
