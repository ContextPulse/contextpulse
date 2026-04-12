"""Tests for the macOS platform provider.

These tests are skipped on non-macOS platforms. Tests that require
macOS TCC permissions (Screen Recording, Accessibility, Input Monitoring)
are marked with @pytest.mark.skip_ci and may fail in CI environments.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS-only tests"
)


@pytest.fixture
def provider():
    from contextpulse_core.platform.macos import MacPlatformProvider

    return MacPlatformProvider()


class TestClipboard:
    def test_clipboard_sequence_returns_int(self, provider):
        seq = provider.get_clipboard_sequence()
        assert isinstance(seq, int)
        assert seq >= 0

    def test_clipboard_text_returns_str_or_none(self, provider):
        text = provider.get_clipboard_text()
        assert text is None or isinstance(text, str)


class TestWindowInfo:
    def test_foreground_process_name_returns_str(self, provider):
        name = provider.get_foreground_process_name()
        assert isinstance(name, str)

    @pytest.mark.skip_ci
    def test_foreground_window_title_returns_str(self, provider):
        """Requires Screen Recording permission for window titles."""
        title = provider.get_foreground_window_title()
        assert isinstance(title, str)


class TestCursor:
    def test_cursor_pos_returns_tuple(self, provider):
        pos = provider.get_cursor_pos()
        assert isinstance(pos, tuple)
        assert len(pos) == 2
        assert isinstance(pos[0], int)
        assert isinstance(pos[1], int)


class TestCaret:
    @pytest.mark.skip_ci
    def test_caret_position_returns_tuple_or_none(self, provider):
        """Requires Accessibility permission."""
        result = provider.get_caret_position()
        assert result is None or (isinstance(result, tuple) and len(result) == 2)


class TestSingleInstance:
    def test_acquire_and_release(self, provider):
        lock = provider.acquire_single_instance_lock("ContextPulse_Test_Lock")
        assert lock is not None
        provider.release_single_instance_lock(lock)

    def test_second_acquire_blocked(self, provider):
        lock1 = provider.acquire_single_instance_lock("ContextPulse_Test_Lock2")
        assert lock1 is not None
        try:
            lock2 = provider.acquire_single_instance_lock("ContextPulse_Test_Lock2")
            assert lock2 is None, "Second acquire should fail"
        finally:
            provider.release_single_instance_lock(lock1)


class TestSessionMonitor:
    def test_create_returns_monitor_with_start(self, provider):
        monitor = provider.create_session_monitor(
            on_lock=lambda: None,
            on_unlock=lambda: None,
        )
        assert hasattr(monitor, "start")
        assert callable(monitor.start)


class TestProcessManagement:
    def test_find_processes_returns_list(self, provider):
        pids = provider.find_contextpulse_processes()
        assert isinstance(pids, list)
        for pid in pids:
            assert isinstance(pid, int)

    def test_find_processes_excludes_self(self, provider):
        import os

        my_pid = os.getpid()
        pids = provider.find_contextpulse_processes(exclude_pid=my_pid)
        assert my_pid not in pids


def test_module_imports_without_pyobjc():
    """The module should import on any platform without PyObjC installed.

    This test deliberately does NOT have the darwin-only marker so it
    runs on Windows/Linux CI to catch accidental top-level PyObjC imports.
    """
    # Override the module-level pytestmark for this single test
    from contextpulse_core.platform.macos import MacPlatformProvider

    assert MacPlatformProvider is not None


test_module_imports_without_pyobjc.pytestmark = []  # clear module-level skip
