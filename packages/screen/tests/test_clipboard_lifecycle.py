# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The clipboard toggle must be honoured whatever else is switched off.

Review findings S4 and S7.

S4: _reconcile_clipboard_monitor was only ever called from _watchdog_loop, and
BOTH start sites wrap that watchdog in `if AUTO_INTERVAL > 0`. A user who set
CONTEXTPULSE_AUTO_INTERVAL=0 could untick "Capture clipboard contents" and the
monitor kept polling and storing until the next restart -- the half-honoured
behaviour the reconcile exists to prevent. A privacy control must not depend on
an unrelated capture setting.

S7: stop() set the event and returned, while the reconcile started a
replacement immediately, so two monitors could write for up to one poll
interval -- and the fresh monitor's empty _last_text lets the same clip be
captured twice.
"""

import inspect
import threading
import time
from unittest.mock import patch

import pytest


def _make_app(tmp_path, monkeypatch):
    import contextpulse_sight.activity as act
    import contextpulse_sight.buffer as buf_mod
    import contextpulse_sight.config as cfg

    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir()
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
    monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

    from contextpulse_sight.app import ContextPulseSightApp

    return ContextPulseSightApp()


class TestReconcileDoesNotDependOnAutoCapture:
    def test_the_timer_starts_even_with_auto_capture_off(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        try:
            app._start_clipboard_monitor()
            thread = app._clipboard_reconcile_thread
            assert thread is not None and thread.is_alive(), (
                "no reconcile timer: with AUTO_INTERVAL=0 nothing would ever "
                "notice the clipboard setting changing"
            )
            assert thread.daemon, "a non-daemon timer would block shutdown"
        finally:
            app.stop_event.set()
            app._stop_clipboard_monitor()

    def test_the_timer_starts_even_when_the_monitor_does_not(self, tmp_path, monkeypatch):
        """Switched off at startup, switched on mid-session: something has to
        be watching, and it cannot be the monitor that was never created."""
        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app = _make_app(tmp_path, monkeypatch)
            try:
                app._start_clipboard_monitor()
                assert app._clipboard_monitor is None
                assert app._clipboard_reconcile_thread.is_alive()
            finally:
                app.stop_event.set()

    def test_starting_twice_does_not_stack_timers(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        try:
            app._start_clipboard_monitor()
            first = app._clipboard_reconcile_thread
            app._start_clipboard_monitor()
            assert app._clipboard_reconcile_thread is first, (
                "every restart would leak another timer thread"
            )
        finally:
            app.stop_event.set()
            app._stop_clipboard_monitor()

    def test_the_loop_survives_a_failing_reconcile(self, tmp_path, monkeypatch):
        """A dead timer silently restores the bug it was added to fix."""
        from contextpulse_sight.app import ContextPulseSightApp

        source = inspect.getsource(ContextPulseSightApp._clipboard_reconcile_loop)
        assert "except Exception" in source
        assert "logger.exception" in source


class TestStopJoinsTheThread:
    def test_stop_waits_for_the_poll_thread(self, tmp_path, monkeypatch):
        from contextpulse_sight.activity import ActivityDB
        from contextpulse_sight.clipboard import ClipboardMonitor

        db = ActivityDB(db_path=tmp_path / "activity.db")
        monitor = ClipboardMonitor(db)
        try:
            monitor.start()
            assert monitor.is_alive()
            monitor.stop()
            # The contract is that stop() RETURNS after the thread is done,
            # not that the thread eventually stops on its own.
            assert not monitor.is_alive(), (
                "stop() returned with the poll thread still running; the "
                "reconcile starts a replacement immediately, so two monitors "
                "would write for up to one poll interval"
            )
        finally:
            db.close()

    def test_stop_is_safe_on_a_thread_that_never_started(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB
        from contextpulse_sight.clipboard import ClipboardMonitor

        db = ActivityDB(db_path=tmp_path / "activity.db")
        try:
            ClipboardMonitor(db).stop()  # must not raise
        finally:
            db.close()

    def test_stop_gives_up_rather_than_hanging(self, tmp_path):
        """A shutdown path must not wait forever on a wedged thread."""
        from contextpulse_sight.activity import ActivityDB
        from contextpulse_sight.clipboard import ClipboardMonitor

        db = ActivityDB(db_path=tmp_path / "activity.db")
        monitor = ClipboardMonitor(db)
        wedged = threading.Event()

        def never_finishes():
            wedged.set()
            time.sleep(30)

        monitor._thread = threading.Thread(target=never_finishes, daemon=True)
        monitor._thread.start()
        assert wedged.wait(2), "the wedged thread never started"
        try:
            started = time.time()
            monitor.stop(timeout=0.2)
            assert time.time() - started < 5, "stop() hung on a wedged thread"
        finally:
            db.close()


@pytest.mark.parametrize("site", ["run", "_start_clipboard_monitor"])
def test_no_start_site_gates_the_reconcile_on_auto_interval(site):
    """The bug was structural: the reconcile lived inside an AUTO_INTERVAL
    branch. Pin that it does not again."""
    from contextpulse_sight.app import ContextPulseSightApp

    source = inspect.getsource(getattr(ContextPulseSightApp, site))
    if "_start_clipboard_reconcile_thread" not in source:
        return
    before = source.split("_start_clipboard_reconcile_thread")[0]
    assert "AUTO_INTERVAL > 0" not in before, (
        f"{site} starts the reconcile timer inside an AUTO_INTERVAL branch"
    )
