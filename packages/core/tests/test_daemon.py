"""Tests for ContextPulseDaemon — module initialization, status, crash logging, watchdog.

conftest.py mocks tkinter, pystray, and windll before any imports happen,
so this file can safely import contextpulse_core.daemon.
"""

import ast
import inspect
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import daemon module (conftest.py ensures tkinter + pystray are mocked)
# ---------------------------------------------------------------------------
from contextpulse_core.daemon import (
    ContextPulseDaemon,
    _refuse_if_session_0,
    _write_fatal_crash_log,
)

# ---------------------------------------------------------------------------
# Factory: build a daemon instance without triggering real __init__ side-effects
# ---------------------------------------------------------------------------

def _make_daemon(tmp_path, sight_ok=True, voice_ok=True, touch_ok=True):
    """Create a ContextPulseDaemon by mocking the module init methods.

    Returns (daemon, mocks_dict).
    """
    mock_event_bus = MagicMock()

    mock_sight = MagicMock()
    mock_sight._sight_module = MagicMock()
    mock_sight._sight_module.is_alive.return_value = True
    mock_sight._event_bus = MagicMock()

    mock_voice = MagicMock()
    mock_voice.is_alive.return_value = True

    mock_touch = MagicMock()
    mock_touch.is_alive.return_value = True

    def _init_sight_impl(self):
        if not sight_ok:
            self._module_errors["sight"] = "no display"
            self._sight_app = None
        else:
            self._sight_app = mock_sight
            self._modules.append(("sight", mock_sight))

    def _init_voice_impl(self):
        if not voice_ok:
            self._module_errors["voice"] = "no audio"
            self._voice_module = None
        else:
            self._voice_module = mock_voice
            self._modules.append(("voice", mock_voice))

    def _init_touch_impl(self):
        if not touch_ok:
            self._module_errors["touch"] = "no input"
            self._touch_module = None
        else:
            self._touch_module = mock_touch
            self._modules.append(("touch", mock_touch))

    with patch("contextpulse_core.daemon.EventBus", return_value=mock_event_bus), \
         patch.object(ContextPulseDaemon, "_init_sight", _init_sight_impl), \
         patch.object(ContextPulseDaemon, "_init_voice", _init_voice_impl), \
         patch.object(ContextPulseDaemon, "_init_touch", _init_touch_impl):
        daemon = ContextPulseDaemon()

    return daemon, {
        "event_bus": mock_event_bus,
        "sight": mock_sight,
        "voice": mock_voice,
        "touch": mock_touch,
    }


# ---------------------------------------------------------------------------
# Module initialization
# ---------------------------------------------------------------------------

class TestModuleInitialization:
    def test_all_three_modules_registered(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        module_names = [name for name, _ in daemon._modules]
        assert "sight" in module_names
        assert "voice" in module_names
        assert "touch" in module_names

    def test_sight_module_accessible(self, tmp_path):
        daemon, mocks = _make_daemon(tmp_path)
        assert daemon._sight_app is mocks["sight"]

    def test_voice_module_accessible(self, tmp_path):
        daemon, mocks = _make_daemon(tmp_path)
        assert daemon._voice_module is mocks["voice"]

    def test_touch_module_accessible(self, tmp_path):
        daemon, mocks = _make_daemon(tmp_path)
        assert daemon._touch_module is mocks["touch"]

    def test_no_errors_when_all_ok(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert daemon._module_errors == {}

    def test_sight_failure_recorded_in_errors(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, sight_ok=False)
        assert "sight" in daemon._module_errors
        assert daemon._sight_app is None

    def test_voice_failure_recorded_in_errors(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, voice_ok=False)
        assert "voice" in daemon._module_errors
        assert daemon._voice_module is None

    def test_touch_failure_recorded_in_errors(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, touch_ok=False)
        assert "touch" in daemon._module_errors
        assert daemon._touch_module is None

    def test_partial_failure_still_registers_ok_modules(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, voice_ok=False)
        module_names = [n for n, _ in daemon._modules]
        assert "sight" in module_names
        assert "touch" in module_names
        assert "voice" not in module_names

    def test_restart_counts_empty_on_init(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert daemon._restart_counts == {}

    def test_stop_event_not_set_on_init(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert not daemon.stop_event.is_set()


# ---------------------------------------------------------------------------
# _get_status_text
# ---------------------------------------------------------------------------

class TestGetStatusText:
    def test_starts_with_contextpulse(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        text = daemon._get_status_text()
        assert text.startswith("ContextPulse")

    def test_contains_module_names(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        text = daemon._get_status_text()
        assert "sight" in text
        assert "voice" in text
        assert "touch" in text

    def test_alive_module_shows_on(self, tmp_path):
        daemon, mocks = _make_daemon(tmp_path)
        mocks["sight"]._sight_module.is_alive.return_value = True
        mocks["voice"].is_alive.return_value = True
        text = daemon._get_status_text()
        assert "ON" in text

    def test_dead_module_shows_off(self, tmp_path):
        daemon, mocks = _make_daemon(tmp_path)
        mocks["sight"]._sight_module.is_alive.return_value = False
        mocks["voice"].is_alive.return_value = False
        mocks["touch"].is_alive.return_value = False
        text = daemon._get_status_text()
        assert "OFF" in text

    def test_format_uses_pipe_separator(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        text = daemon._get_status_text()
        assert "|" in text

    def test_failed_module_shows_err(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, sight_ok=False)
        text = daemon._get_status_text()
        assert "ERR" in text

    def test_no_modules_returns_contextpulse_only(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._modules = []
        daemon._module_errors = {}
        text = daemon._get_status_text()
        assert text == "ContextPulse"


# ---------------------------------------------------------------------------
# _log_crash
# ---------------------------------------------------------------------------

class TestLogCrash:
    def _write_crash(self, daemon, crash_log, module, exc):
        """Helper to call _log_crash with the module-level CRASH_LOG patched."""
        import contextpulse_core.daemon as daemon_mod
        original = daemon_mod.CRASH_LOG
        daemon_mod.CRASH_LOG = crash_log
        try:
            daemon._log_crash(module, exc)
        finally:
            daemon_mod.CRASH_LOG = original

    def test_creates_crash_log_file(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        crash_log = tmp_path / "contextpulse_crash.log"
        self._write_crash(daemon, crash_log, "voice", RuntimeError("boom"))
        assert crash_log.exists()

    def test_crash_log_contains_module_name(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        crash_log = tmp_path / "crash.log"
        self._write_crash(daemon, crash_log, "voice", RuntimeError("test error"))
        assert "voice" in crash_log.read_text(encoding="utf-8")

    def test_crash_log_contains_error_message(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        crash_log = tmp_path / "crash.log"
        self._write_crash(daemon, crash_log, "touch", RuntimeError("keyboard exploded"))
        assert "keyboard exploded" in crash_log.read_text(encoding="utf-8")

    def test_crash_log_has_separator(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        crash_log = tmp_path / "crash.log"
        self._write_crash(daemon, crash_log, "sight", ValueError("oops"))
        assert "=" * 10 in crash_log.read_text(encoding="utf-8")

    def test_crash_log_appends_multiple_entries(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        crash_log = tmp_path / "crash.log"
        self._write_crash(daemon, crash_log, "voice", RuntimeError("first"))
        self._write_crash(daemon, crash_log, "touch", RuntimeError("second"))
        content = crash_log.read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content

    def test_log_crash_silent_on_unwritable_path(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        import contextpulse_core.daemon as daemon_mod
        original = daemon_mod.CRASH_LOG
        daemon_mod.CRASH_LOG = tmp_path  # directory, not file — open() will fail
        try:
            daemon._log_crash("voice", RuntimeError("silent"))  # must not raise
        finally:
            daemon_mod.CRASH_LOG = original


# ---------------------------------------------------------------------------
# _write_fatal_crash_log — the standalone helper main() uses for
# MemoryError/fatal-exception crash reporting (no ContextPulseDaemon
# instance required, unlike _log_crash above).
# ---------------------------------------------------------------------------

class TestWriteFatalCrashLog:
    def _write(self, crash_log, header):
        """Helper to call _write_fatal_crash_log with CRASH_LOG patched."""
        import contextpulse_core.daemon as daemon_mod
        original = daemon_mod.CRASH_LOG
        daemon_mod.CRASH_LOG = crash_log
        try:
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                _write_fatal_crash_log(header)
        finally:
            daemon_mod.CRASH_LOG = original

    def test_creates_crash_log_file(self, tmp_path):
        crash_log = tmp_path / "contextpulse_crash.log"
        self._write(crash_log, "FATAL DAEMON CRASH")
        assert crash_log.exists()

    def test_crash_log_contains_header_and_traceback(self, tmp_path):
        crash_log = tmp_path / "crash.log"
        self._write(crash_log, "FATAL MemoryError")
        content = crash_log.read_text(encoding="utf-8")
        assert "FATAL MemoryError" in content
        assert "RuntimeError" in content
        assert "boom" in content

    def test_rotates_when_oversized(self, tmp_path):
        """Regression test: main()'s fatal-crash paths bypassed rotation
        before this fix, letting a crash loop regrow the log without bound
        (the original 2026-08-07 incident: 431MB + 339MB unrotated logs)."""
        crash_log = tmp_path / "contextpulse_crash.log"
        crash_log.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")  # > default 10MiB
        self._write(crash_log, "FATAL DAEMON CRASH")
        rotated = crash_log.with_name(f"{crash_log.name}.1")
        assert rotated.exists(), "oversized crash log was not rotated before append"
        # The active log is now small again — only this run's entry.
        assert crash_log.stat().st_size < 11 * 1024 * 1024

    def test_silent_on_unwritable_path(self, tmp_path):
        import contextpulse_core.daemon as daemon_mod
        original = daemon_mod.CRASH_LOG
        daemon_mod.CRASH_LOG = tmp_path  # directory, not file — open() will fail
        try:
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                _write_fatal_crash_log("FATAL DAEMON CRASH")  # must not raise
        finally:
            daemon_mod.CRASH_LOG = original


# ---------------------------------------------------------------------------
# Watchdog restart counting logic
# ---------------------------------------------------------------------------

class TestWatchdogRestartCounting:
    def test_max_restarts_is_three(self, tmp_path):
        """Verify MAX_RESTARTS constant in the watchdog source."""
        src = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "MAX_RESTARTS = 3" in src

    def test_restart_count_starts_at_zero(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert daemon._restart_counts.get("voice", 0) == 0
        assert daemon._restart_counts.get("touch", 0) == 0

    def test_restart_counts_independent_per_module(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._restart_counts["voice"] = 2
        daemon._restart_counts["touch"] = 0
        assert daemon._restart_counts["voice"] == 2
        assert daemon._restart_counts["touch"] == 0

    def test_restart_count_increments_correctly(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._restart_counts["voice"] = 0
        daemon._restart_counts["voice"] += 1
        assert daemon._restart_counts["voice"] == 1

    def test_watchdog_loop_checks_voice_and_touch(self, tmp_path):
        """Watchdog source references both voice and touch module checks."""
        src = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "_voice_module" in src
        assert "_touch_module" in src

    def test_watchdog_loop_uses_stop_event(self, tmp_path):
        """Watchdog must honor stop_event to exit cleanly."""
        src = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "stop_event" in src


# ---------------------------------------------------------------------------
# Module error tracking
# ---------------------------------------------------------------------------

class TestModuleErrorTracking:
    def test_errors_dict_is_initially_empty(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert isinstance(daemon._module_errors, dict)
        assert daemon._module_errors == {}

    def test_error_stored_on_sight_failure(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, sight_ok=False)
        assert "sight" in daemon._module_errors
        assert len(daemon._module_errors["sight"]) > 0

    def test_error_stored_on_voice_failure(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, voice_ok=False)
        assert "voice" in daemon._module_errors

    def test_error_stored_on_touch_failure(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, touch_ok=False)
        assert "touch" in daemon._module_errors

    def test_multiple_failures_all_tracked(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, voice_ok=False, touch_ok=False)
        assert "voice" in daemon._module_errors
        assert "touch" in daemon._module_errors

    def test_error_is_string(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path, voice_ok=False)
        assert isinstance(daemon._module_errors["voice"], str)

    def test_no_errors_for_successful_modules(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        assert "sight" not in daemon._module_errors
        assert "voice" not in daemon._module_errors
        assert "touch" not in daemon._module_errors


# ---------------------------------------------------------------------------
# _notify_tray debounce
# ---------------------------------------------------------------------------

class TestNotifyTrayDebounce:
    def test_notification_sent_on_first_call(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        mock_tray = MagicMock()
        daemon.tray = mock_tray
        daemon._last_tray_notification = 0

        daemon._notify_tray("Test Title", "Test Message")
        mock_tray.notify.assert_called_once_with("Test Message", "Test Title")

    def test_second_notification_within_30s_suppressed(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        mock_tray = MagicMock()
        daemon.tray = mock_tray
        daemon._last_tray_notification = time.time()

        daemon._notify_tray("Second", "Should be suppressed")
        mock_tray.notify.assert_not_called()

    def test_notification_allowed_after_30s(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        mock_tray = MagicMock()
        daemon.tray = mock_tray
        daemon._last_tray_notification = time.time() - 31

        daemon._notify_tray("After Wait", "Should go through")
        mock_tray.notify.assert_called_once()

    def test_no_tray_no_error(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._last_tray_notification = 0
        # No tray attribute set — should not raise
        daemon._notify_tray("No Tray", "Message")


# ---------------------------------------------------------------------------
# run() entry point — tray keep-alive loop
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "Exercises the pystray keep-alive loop, which does not exist on macOS. "
        "daemon.py imports pystray only in its non-darwin branch and uses the "
        "rumps-based tray_macos tray instead, so patching "
        "contextpulse_core.daemon.pystray raises AttributeError here. Skipping "
        "rather than asserting: there is no pystray path on this platform to "
        "get wrong. The macOS tray needs its own coverage, tracked separately."
    ),
)
class TestRunTrayLoop:
    """Smoke-tests the run() tray keep-alive loop.

    Regression guard: a local ``import time`` inside run()'s zombie-kill block
    made Python treat ``time`` as function-local for the whole method, so the
    tray loop's ``time.time()`` raised UnboundLocalError whenever the
    zombie-kill branch did NOT run (the common no-zombie case). The unit suite
    missed it because nothing exercised run(); this crash-looped the live
    daemon on deploy. These tests drive run() with no zombies and assert it
    reaches and exits the tray loop cleanly.
    """

    def _run_once(self, tmp_path, zombies):
        daemon, mocks = _make_daemon(tmp_path)

        mock_platform = MagicMock()
        mock_platform.find_contextpulse_processes.return_value = zombies
        mock_platform.acquire_single_instance_lock.return_value = object()

        mock_tray = MagicMock()
        # Break the keep-alive loop after one iteration by simulating an
        # intentional quit (the line under regression test, _tray_start =
        # time.time(), runs BEFORE tray.run() each iteration).
        def _quit_after_run():
            daemon._running = False
        mock_tray.run.side_effect = _quit_after_run

        with patch("contextpulse_core.daemon.get_platform_provider", return_value=mock_platform), \
             patch("contextpulse_core.daemon.is_first_run", return_value=False), \
             patch.object(ContextPulseDaemon, "_start_modules"), \
             patch.object(ContextPulseDaemon, "_create_tray_menu", return_value=MagicMock()), \
             patch("contextpulse_core.daemon.threading.Thread"), \
             patch("contextpulse_core.daemon.pystray.Icon", return_value=mock_tray), \
             patch("contextpulse_sight.icon.create_icon", return_value=MagicMock()):
            daemon.run()

        return daemon, mock_tray

    def test_run_no_zombies_does_not_raise(self, tmp_path):
        # The exact branch that crash-looped prod: no zombies, so the local
        # ``import time`` never executed and time.time() hit an unbound local.
        daemon, mock_tray = self._run_once(tmp_path, zombies=[])
        mock_tray.run.assert_called_once()

    def test_run_with_zombies_does_not_raise(self, tmp_path):
        daemon, mock_tray = self._run_once(tmp_path, zombies=[99999])
        mock_tray.run.assert_called_once()


# ---------------------------------------------------------------------------
# Phase-1 knowledge-graph integration (gated on knowledge_enabled)
# ---------------------------------------------------------------------------

class TestKnowledgeIntegration:
    def test_disabled_by_default_is_byte_identical(self, tmp_path):
        # Default flag false -> no store, no ingestor (AT-4 byte-identical path).
        daemon, _ = _make_daemon(tmp_path)
        assert daemon._knowledge_ingestor is None
        assert daemon._knowledge_store is None

    def test_enabled_creates_and_attaches_ingestor(self, tmp_path, monkeypatch):
        import contextpulse_core.config as cfg
        import contextpulse_knowledge.bridge as bridgemod
        monkeypatch.setattr(cfg, "get", lambda k, d=None: True if k == "knowledge_enabled" else d)
        monkeypatch.setattr(bridgemod, "default_knowledge_db", lambda: str(tmp_path / "knowledge.db"))
        daemon, mocks = _make_daemon(tmp_path)
        try:
            assert daemon._knowledge_ingestor is not None
            assert daemon._knowledge_store is not None
            # attached as a listener on the (mocked) shared EventBus
            mocks["event_bus"].on.assert_called_once()
        finally:
            daemon._stop_knowledge()

    def test_init_is_fail_soft(self, tmp_path, monkeypatch):
        # A KG init failure must never break the daemon or capture.
        import contextpulse_core.config as cfg
        import contextpulse_knowledge.store_sqlite as ss
        monkeypatch.setattr(cfg, "get", lambda k, d=None: True if k == "knowledge_enabled" else d)

        def _boom(*a, **k):
            raise RuntimeError("kg boom")

        monkeypatch.setattr(ss, "KnowledgeStore", _boom)
        daemon, _ = _make_daemon(tmp_path)  # must not raise
        assert daemon._knowledge_ingestor is None
        assert daemon._knowledge_store is None
        assert "knowledge" in daemon._module_errors

    def test_start_knowledge_delegates(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._knowledge_ingestor = MagicMock()
        daemon._start_knowledge()
        daemon._knowledge_ingestor.start.assert_called_once()

    def test_start_knowledge_noop_when_absent(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._knowledge_ingestor = None
        daemon._start_knowledge()  # must not raise

    def test_stop_knowledge_stops_thread_and_closes_store(self, tmp_path):
        daemon, _ = _make_daemon(tmp_path)
        daemon._knowledge_ingestor = MagicMock()
        daemon._knowledge_store = MagicMock()
        daemon._stop_knowledge()
        daemon._knowledge_ingestor.stop.assert_called_once()
        daemon._knowledge_store.close.assert_called_once()


# ---------------------------------------------------------------------------
# _refuse_if_session_0 -- the Session 0 startup guard.
#
# Regression coverage for cp-daemon-session0-blind-capture: a 4-day silent
# outage where the whole supervision chain got relaunched into Windows
# Session 0 (non-interactive) and every existing liveness signal (heartbeat,
# process-alive, MCP port) stayed green the entire time because none of
# them observe whether a capture actually happened.
# ---------------------------------------------------------------------------

class TestRefuseIfSession0:
    def test_noop_on_non_windows(self, monkeypatch):
        import contextpulse_core.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod.sys, "platform", "darwin")
        with patch.object(daemon_mod, "get_windows_session_id") as mock_get:
            _refuse_if_session_0()  # must not raise / not exit
        mock_get.assert_not_called()

    def test_exits_when_session_is_zero(self, monkeypatch):
        import contextpulse_core.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod.sys, "platform", "win32")
        with patch.object(daemon_mod, "get_windows_session_id", return_value=0), \
             pytest.raises(SystemExit) as exc_info:
            _refuse_if_session_0()
        assert exc_info.value.code == 1

    def test_exits_when_session_is_undeterminable(self, monkeypatch):
        """Fails CLOSED: None (couldn't determine) is treated the same as
        Session 0 -- proof of safety is required, not merely absence of
        proof of danger."""
        import contextpulse_core.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod.sys, "platform", "win32")
        with patch.object(daemon_mod, "get_windows_session_id", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            _refuse_if_session_0()
        assert exc_info.value.code == 1

    def test_allows_a_real_interactive_session(self, monkeypatch):
        import contextpulse_core.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod.sys, "platform", "win32")
        with patch.object(daemon_mod, "get_windows_session_id", return_value=1):
            _refuse_if_session_0()  # must not raise

    def test_logs_error_naming_session_0_before_exiting(self, monkeypatch, caplog):
        import logging

        import contextpulse_core.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod.sys, "platform", "win32")
        with patch.object(daemon_mod, "get_windows_session_id", return_value=0), \
             caplog.at_level(logging.ERROR, logger="contextpulse.daemon"), \
             pytest.raises(SystemExit):
            _refuse_if_session_0()
        assert any("Session 0" in r.getMessage() for r in caplog.records)


class TestSessionGuardOrdering:
    """Source-inspection guards (same style as TestWatchdogRestartCounting
    above) proving the guard runs BEFORE expensive/unsafe work, not just
    that it exists somewhere in the file."""

    def test_main_checks_session_before_acquiring_mutex(self):
        import contextpulse_core.daemon as daemon_mod
        src = inspect.getsource(daemon_mod.main)
        assert src.index("_refuse_if_session_0()") < src.index("_acquire_single_instance_or_exit()")

    def test_run_checks_session_as_its_first_statement(self):
        src = inspect.getsource(ContextPulseDaemon.run)
        # First non-docstring statement inside run(): the mutex fallback
        # comment block is documentation, but the guard call itself must
        # precede the mutex fallback's own acquisition line.
        assert src.index("_refuse_if_session_0()") < src.index("_acquire_single_instance_or_exit()")

    def test_run_refuses_before_touching_the_tray(self, tmp_path, monkeypatch):
        """End-to-end: run() must exit before pystray.Icon is ever built --
        not just log an error and continue."""
        import contextpulse_core.daemon as daemon_mod
        daemon, _ = _make_daemon(tmp_path)
        monkeypatch.setattr(daemon_mod.sys, "platform", "win32")
        with patch.object(daemon_mod, "get_windows_session_id", return_value=0), \
             patch.object(ContextPulseDaemon, "_start_modules") as mock_start, \
             pytest.raises(SystemExit):
            daemon.run()
        mock_start.assert_not_called()



# ---------------------------------------------------------------------------
# Clipboard monitor is optional (clipboard_enabled=False)
# ---------------------------------------------------------------------------

def _sight_app_with_clipboard(tmp_path, monkeypatch, enabled):
    """A REAL ContextPulseSightApp, with only its heavy parts mocked.

    A MagicMock sight app cannot test this: the daemon would call a mocked
    _start_clipboard_monitor that never touches the monitor, so the test
    would pass no matter what the daemon did. The clipboard lifecycle
    methods have to be the real ones.
    """
    import contextpulse_sight.activity as act
    import contextpulse_sight.app as app_mod
    import contextpulse_sight.buffer as buf_mod
    import contextpulse_sight.config as cfg

    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
    monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")
    monkeypatch.setattr(
        app_mod,
        "cfg_get",
        lambda key, default=None: enabled if key == "clipboard_enabled" else default,
    )

    app = app_mod.ContextPulseSightApp()
    # Everything _start_modules touches other than the clipboard helpers --
    # real ones would spawn capture threads and load OCR models.
    app._event_detector = MagicMock()
    app._ocr_worker = MagicMock()
    app._sight_module = MagicMock()
    # The daemon now starts both of these unconditionally (it used to gate
    # them on contextpulse_sight.config.AUTO_INTERVAL, a frozen constant).
    # Stub the loop BODIES rather than the threads: the thread objects stay
    # real, so a test can still assert they were created and started, but
    # nothing takes a screenshot or polls a clipboard for 15s.
    app._auto_capture_loop = MagicMock(name="_auto_capture_loop")
    app._watchdog_loop = MagicMock(name="_watchdog_loop")
    return app


class TestClipboardMonitorMayBeAbsent:
    """The unified daemon is the process that actually runs on this machine.

    ContextPulseSightApp._clipboard_monitor is None when clipboard_enabled is
    false. _start_modules and _stop_modules reach into the sight app's
    internals directly, so gating the monitor inside the app was not enough --
    the daemon dereferenced it unguarded and raised AttributeError on startup,
    taking Sight, Voice and Touch down with it. Verified failing against the
    unguarded call sites before the fix.
    """

    def _daemon_with(self, tmp_path, monkeypatch, enabled):
        daemon, _ = _make_daemon(tmp_path)
        app = _sight_app_with_clipboard(tmp_path, monkeypatch, enabled)
        daemon._sight_app = app
        daemon._voice_module = None
        daemon._touch_module = None
        daemon._knowledge_ingestor = None
        return daemon, app

    def _start(self, daemon):
        # No AUTO_INTERVAL patch: the daemon no longer reads that constant,
        # and _sight_app_with_clipboard stubs the two loop bodies instead.
        with patch("contextpulse_sight.privacy.SessionMonitor"), \
             patch("pynput.keyboard.Listener"):
            daemon._start_modules()

    def test_start_modules_completes_when_clipboard_disabled(self, tmp_path, monkeypatch):
        daemon, app = self._daemon_with(tmp_path, monkeypatch, enabled=False)
        assert app._clipboard_monitor is None

        self._start(daemon)  # must not raise AttributeError

        assert app._clipboard_monitor is None, "a disabled monitor was started anyway"
        app._ocr_worker.start.assert_called_once()

    def test_stop_modules_completes_when_clipboard_disabled(self, tmp_path, monkeypatch):
        daemon, app = self._daemon_with(tmp_path, monkeypatch, enabled=False)
        daemon._stop_modules()  # must not raise AttributeError
        app._ocr_worker.stop.assert_called_once()

    def test_start_modules_still_starts_an_enabled_monitor(self, tmp_path, monkeypatch):
        daemon, app = self._daemon_with(tmp_path, monkeypatch, enabled=True)
        assert app._clipboard_monitor is not None

        self._start(daemon)
        try:
            assert app._clipboard_monitor.is_alive(), (
                "the monitor thread is not running -- the disabled case would "
                "pass here too if the daemon simply stopped starting it"
            )
        finally:
            app._clipboard_monitor.stop()

    def test_stop_modules_stops_an_enabled_monitor(self, tmp_path, monkeypatch):
        daemon, app = self._daemon_with(tmp_path, monkeypatch, enabled=True)
        monitor = app._clipboard_monitor
        self._start(daemon)
        daemon._stop_modules()
        assert monitor._stop.is_set(), "the running monitor was not told to stop"


# ---------------------------------------------------------------------------
# The capture and watchdog threads start regardless of auto_interval
# ---------------------------------------------------------------------------

class TestCaptureThreadAlwaysStarts:
    """auto_interval: 0 must not be a one-way door.

    _start_modules used to wrap both thread starts in
    `if contextpulse_sight.config.AUTO_INTERVAL > 0`. That constant is frozen
    at import, so setting the interval to 0 meant no capture thread existed
    for the life of the process -- the Settings slider could turn capture off
    and then could not turn it back on, and nothing said so.

    Worse, the same branch also skipped the WATCHDOG thread, which is what
    re-reads clipboard_enabled every 15s and restarts a dead capture thread.
    A user who set auto_interval: 0 lost the "Capture clipboard" checkbox's
    live behaviour as a side effect of a setting about screenshots.

    Handling 0 belongs inside _auto_capture_loop (it skips the iteration),
    where a later save can change the answer.
    """

    def _daemon_and_app(self, tmp_path, monkeypatch):
        daemon, _ = _make_daemon(tmp_path)
        app = _sight_app_with_clipboard(tmp_path, monkeypatch, enabled=False)
        daemon._sight_app = app
        daemon._voice_module = None
        daemon._touch_module = None
        daemon._knowledge_ingestor = None
        with patch("contextpulse_sight.privacy.SessionMonitor"), \
             patch("pynput.keyboard.Listener"):
            daemon._start_modules()
        return daemon, app

    def test_both_threads_start_when_auto_interval_is_zero(self, tmp_path, monkeypatch):
        import contextpulse_core.config as cfg_mod
        monkeypatch.setitem(cfg_mod._DEFAULTS, "auto_interval", 0)
        cfg_mod.clear_config_cache()

        _, app = self._daemon_and_app(tmp_path, monkeypatch)

        assert app._capture_thread is not None, "no capture thread with auto_interval 0"
        assert app._watchdog_thread is not None, "no watchdog thread with auto_interval 0"
        # The loop BODY is what decides whether to capture, and it is reached.
        app._auto_capture_loop.assert_called_once()
        app._watchdog_loop.assert_called_once()

    def test_both_threads_start_at_the_default_interval(self, tmp_path, monkeypatch):
        """Positive control: the zero case above would also pass if the daemon
        had simply stopped starting threads altogether."""
        _, app = self._daemon_and_app(tmp_path, monkeypatch)
        app._auto_capture_loop.assert_called_once()
        app._watchdog_loop.assert_called_once()

    def test_daemon_does_not_read_the_frozen_sight_constant(self):
        """The gate is gone from the CODE, not merely inert at runtime.

        Parsed, not grepped: the first version of this test searched the raw
        source and failed on the comment that explains the removal -- the
        same false positive tests/test_config_readers.py avoids by walking
        the AST, where comments do not exist.
        """
        import contextpulse_core.daemon as daemon_mod
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(daemon_mod.ContextPulseDaemon._start_modules)
        ))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {a.name for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "AUTO_INTERVAL" not in names, (
            "_start_modules still reads contextpulse_sight.config.AUTO_INTERVAL, "
            "a constant frozen at import"
        )


class TestActivityDbPathIsImportedNotRecomputed:
    """daemon.py held a third copy of the activity-DB path expression.

    It read CONTEXTPULSE_ACTIVITY_DB itself and joined it to OUTPUT_DIR, which
    agreed with contextpulse_core.config only for as long as both spelled the
    same env var with the same default. The daemon WRITES the database that
    contextpulse_sight.activity and the MCP server read, so a disagreement is
    a split-brain store rather than a tidy-up.
    """

    def test_daemon_path_is_the_config_path(self):
        import contextpulse_core.config as cfg_mod
        import contextpulse_core.daemon as daemon_mod
        assert daemon_mod.ACTIVITY_DB_PATH == cfg_mod.ACTIVITY_DB_PATH

    def test_daemon_module_no_longer_reads_the_env_var(self):
        """Again parsed, not grepped -- the comment above the import names it."""
        import contextpulse_core.daemon as daemon_mod
        tree = ast.parse(
            Path(inspect.getfile(daemon_mod)).read_text(encoding="utf-8")
        )
        literals = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "CONTEXTPULSE_ACTIVITY_DB"
        ]
        assert not literals, (
            f"daemon.py still resolves the activity DB path itself (line(s) {literals}) "
            "instead of importing contextpulse_core.config.ACTIVITY_DB_PATH"
        )
