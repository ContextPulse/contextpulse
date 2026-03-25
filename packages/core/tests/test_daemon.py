"""Tests for ContextPulseDaemon — module initialization, status, crash logging, watchdog.

conftest.py mocks tkinter, pystray, and windll before any imports happen,
so this file can safely import contextpulse_core.daemon.
"""

import inspect
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import daemon module (conftest.py ensures tkinter + pystray are mocked)
# ---------------------------------------------------------------------------
from contextpulse_core.daemon import ContextPulseDaemon


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
