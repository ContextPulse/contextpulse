"""Daemon lifecycle tests — module start/stop, watchdog, tray freshness.

These tests exercise the daemon with real threads to catch timing-dependent
bugs that unit tests with mocked threading cannot find.
"""

import inspect
from unittest.mock import MagicMock, patch

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
    """Daemon must prevent duplicate instances.

    cp-daemon-double-launch-race-wastes-4s: two overlapping launch attempts
    (e.g. the nightly 4am restart racing a manual relaunch) used to both pay
    the full Sight/Voice/Touch/Whisper module-init cost (~4-7s observed)
    before either one checked the mutex, because ``main()`` constructed
    ``ContextPulseDaemon()`` (which runs all four ``_init_*`` methods) BEFORE
    calling ``run()`` (where the mutex check lived). The loser did all that
    work only to discover it lost the race. The guard now runs in ``main()``
    before the daemon is constructed at all; ``run()`` keeps a fallback copy
    of the same guard for any caller that constructs+runs a
    ``ContextPulseDaemon`` directly without going through ``main()`` first.
    """

    def test_guard_helper_acquires_mutex(self):
        """The extracted guard must call acquire_single_instance_lock."""
        from contextpulse_core.daemon import _acquire_single_instance_or_exit

        source = inspect.getsource(_acquire_single_instance_or_exit)
        assert "acquire_single_instance_lock" in source, (
            "Single-instance guard must acquire a mutex on startup"
        )

    def test_guard_helper_exits_if_already_running(self):
        """The extracted guard must exit if another instance holds the mutex."""
        from contextpulse_core.daemon import _acquire_single_instance_or_exit

        source = inspect.getsource(_acquire_single_instance_or_exit)
        assert "sys.exit" in source, (
            "Guard must exit the process if the single-instance lock fails"
        )

    def test_run_still_guards_when_mutex_not_preacquired(self):
        """run() must remain a complete guard on its own (fallback path) for
        any caller that constructs+runs a daemon without going through
        main() first — it must not silently skip the check."""
        from contextpulse_core.daemon import ContextPulseDaemon

        source = inspect.getsource(ContextPulseDaemon.run)
        assert "_acquire_single_instance_or_exit" in source, (
            "run() must fall back to the single-instance guard when "
            "self._mutex was not already set by main()"
        )

    def test_main_acquires_guard_before_constructing_daemon(self):
        """main() must call the guard BEFORE constructing ContextPulseDaemon()
        -- this is the actual fix: checking the mutex first means a losing
        launch attempt exits immediately instead of paying the full
        Sight/Voice/Touch/Whisper module-init cost only to discover it lost
        the race."""
        from contextpulse_core.daemon import main

        source = inspect.getsource(main)
        guard_pos = source.find("mutex = _acquire_single_instance_or_exit(")
        construct_pos = source.find("= ContextPulseDaemon()")
        assert guard_pos != -1, "main() must call the single-instance guard"
        assert construct_pos != -1, "main() must construct ContextPulseDaemon()"
        assert guard_pos < construct_pos, (
            "main() must acquire the single-instance guard BEFORE "
            "constructing ContextPulseDaemon() — otherwise module init "
            "(Sight/Voice/Touch/Whisper) runs before the mutex is checked"
        )

    def test_main_exits_without_constructing_daemon_when_already_running(self):
        """Behavioral proof: when another instance holds the mutex, main()
        must exit WITHOUT ever constructing a ContextPulseDaemon — i.e.
        without ever calling any of the expensive _init_* methods."""
        mock_platform = MagicMock()
        mock_platform.find_contextpulse_processes.return_value = []
        mock_platform.acquire_single_instance_lock.return_value = None  # already running

        mock_daemon_cls = MagicMock()

        with patch("contextpulse_core.daemon.get_platform_provider", return_value=mock_platform), \
             patch("contextpulse_core.daemon.ContextPulseDaemon", mock_daemon_cls), \
             patch("contextpulse_core.daemon._setup_logging"), \
             patch("contextpulse_core.daemon.sys.argv", ["contextpulse"]):
            from contextpulse_core.daemon import main

            with pytest.raises(SystemExit):
                main()

        mock_daemon_cls.assert_not_called()
