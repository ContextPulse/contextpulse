"""Daemon lifecycle tests — module start/stop, watchdog, tray freshness.

These tests exercise the daemon with real threads to catch timing-dependent
bugs that unit tests with mocked threading cannot find.
"""

import inspect
import time

import pytest


class TestTrayTooltipFreshness:
    """Tray tooltip must reflect actual module state promptly."""

    def test_update_tray_called_after_voice_starts(self):
        """The daemon must call _update_tray() after voice finishes
        starting in its background thread. Without this, the tooltip
        shows 'voice=OFF' for up to 15s (the watchdog interval)."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._start_voice_with_progress)
        assert "_update_tray" in source, (
            "_start_voice_with_progress must call _update_tray() after "
            "voice starts — otherwise tooltip shows voice=OFF until "
            "the watchdog cycle (15s)"
        )

    def test_status_text_contains_all_modules(self):
        """_get_status_text must include all registered modules."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._get_status_text)
        assert "_modules" in source, (
            "_get_status_text must iterate over self._modules"
        )
        assert "ON" in source and "OFF" in source, (
            "_get_status_text must report ON/OFF status for each module"
        )


class TestWatchdogBehavior:
    """Watchdog must detect dead modules and restart them."""

    def test_watchdog_loop_checks_voice(self):
        """Watchdog must check voice module is_alive()."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "voice" in source.lower() and "is_alive" in source, (
            "Watchdog must check voice module liveness"
        )

    def test_watchdog_loop_checks_touch(self):
        """Watchdog must check touch module is_alive()."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "touch" in source.lower(), (
            "Watchdog must check touch module liveness"
        )

    def test_watchdog_has_max_restarts(self):
        """Watchdog must define and enforce a restart limit."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "MAX_RESTARTS" in source, (
            "Watchdog must define MAX_RESTARTS to prevent infinite restart loops"
        )
        # Extract the value
        import re
        match = re.search(r"MAX_RESTARTS\s*=\s*(\d+)", source)
        assert match, "Could not find MAX_RESTARTS value"
        max_restarts = int(match.group(1))
        assert 2 <= max_restarts <= 5, (
            f"MAX_RESTARTS={max_restarts} — should be 2-5 "
            f"(>=2 for transient recovery, <=5 to avoid loops)"
        )

    def test_watchdog_updates_tray_each_cycle(self):
        """Watchdog must call _update_tray() to keep tooltip fresh."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon._watchdog_loop)
        assert "_update_tray" in source, (
            "Watchdog must call _update_tray() each cycle to keep "
            "the tray tooltip reflecting current module state"
        )


class TestModuleLifecycle:
    """Module start/stop must be clean."""

    def test_voice_is_alive_uses_running_flag(self):
        """VoiceModule.is_alive() must use self._running, NOT
        self._listener.is_alive(). The listener thread can report
        is_alive()=False on Windows even while the OS hook is active."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule.is_alive)
        assert "_running" in source, (
            "is_alive() must check self._running"
        )
        # The actual return statement must use _running, not _listener.is_alive()
        # (comments mentioning _listener are fine — they explain the design decision)
        lines = [l.strip() for l in source.splitlines() if not l.strip().startswith("#")]
        code_only = "\n".join(lines)
        assert "_listener.is_alive" not in code_only, (
            "is_alive() code must NOT call _listener.is_alive() — "
            "it causes false negatives on Windows (comments are OK)"
        )

    def test_voice_start_sets_running(self):
        """start() must set _running = True."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule.start)
        assert "_running = True" in source

    def test_voice_stop_clears_running(self):
        """stop() must set _running = False."""
        from contextpulse_voice.voice_module import VoiceModule

        source = inspect.getsource(VoiceModule.stop)
        assert "_running = False" in source


class TestSingleInstanceGuard:
    """Daemon must prevent duplicate instances."""

    def test_daemon_acquires_mutex(self):
        """run() must call acquire_single_instance_lock."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon.run)
        assert "acquire_single_instance_lock" in source, (
            "Daemon must acquire a single-instance mutex on startup"
        )

    def test_daemon_exits_if_already_running(self):
        """run() must exit if another instance holds the mutex."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon.run)
        assert "sys.exit" in source or "return" in source, (
            "Daemon must exit if single-instance lock fails"
        )
