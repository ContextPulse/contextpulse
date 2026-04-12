"""Integration test fixtures for ContextPulse.

IMPORTANT: This conftest does NOT mock pystray, tkinter, pynput, or sounddevice
at the sys.modules level. Integration tests exercise real thread interactions.
Only physical hardware (microphone, display) is mocked.
"""

import sys
import threading

import pytest

# ---------------------------------------------------------------------------
# Skip on non-Windows — integration tests depend on Win32 APIs
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests on non-Windows platforms."""
    if sys.platform != "win32":
        skip = pytest.mark.skip(reason="integration tests require Windows")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


# ---------------------------------------------------------------------------
# Mock ctypes.windll if absent (for any non-Windows CI sanity checks)
# ---------------------------------------------------------------------------
import ctypes

if not hasattr(ctypes, "windll"):
    from unittest.mock import MagicMock

    ctypes.windll = MagicMock()

# ---------------------------------------------------------------------------
# Thread leak detection
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def thread_baseline():
    """Detect non-daemon threads leaked by a test.

    Takes a snapshot of threads before the test and asserts
    no new non-daemon threads remain after teardown.
    """
    before = {t.ident for t in threading.enumerate() if not t.daemon}
    yield
    import time

    time.sleep(0.5)  # brief grace for daemon thread cleanup
    after = {t for t in threading.enumerate() if not t.daemon}
    leaked = {t for t in after if t.ident not in before}
    # Don't fail on the main thread or pytest workers
    leaked = {t for t in leaked if "MainThread" not in t.name}
    if leaked:
        names = [f"{t.name}(id={t.ident})" for t in leaked]
        pytest.fail(f"Test leaked non-daemon threads: {names}")


# ---------------------------------------------------------------------------
# Daemon fixture — real threads, mocked hardware
# ---------------------------------------------------------------------------


@pytest.fixture
def live_daemon(tmp_path):
    """Create a real ContextPulseDaemon with mocked hardware.

    Real threads run. Only the single-instance mutex and output directory
    are redirected to tmp_path.
    """
    from unittest.mock import MagicMock, patch

    from contextpulse_core.platform import factory as _platform_factory
    from contextpulse_core.platform.base import PlatformProvider

    # Mock the platform provider
    mock_platform = MagicMock(spec=PlatformProvider)
    mock_platform.get_foreground_window_title.return_value = "Test Window"
    mock_platform.get_foreground_process_name.return_value = "test.exe"
    mock_platform.get_cursor_pos.return_value = (500, 500)
    mock_platform.get_clipboard_sequence.return_value = 0
    mock_platform.get_clipboard_text.return_value = None
    mock_platform.get_caret_position.return_value = None
    mock_platform.acquire_single_instance_lock.return_value = object()
    mock_platform.find_contextpulse_processes.return_value = []

    old_instance = getattr(_platform_factory, "_instance", None)
    _platform_factory._instance = mock_platform

    # Patch heavy modules (sound, display) but let threads run
    with (
        patch("sounddevice.InputStream"),
        patch("contextpulse_voice.transcriber.LocalTranscriber") as MockTranscriber,
        patch("contextpulse_voice.overlay.RecordingOverlay"),
    ):
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "test transcription"
        MockTranscriber.return_value = mock_transcriber

        from contextpulse_core.daemon import ContextPulseDaemon

        daemon = ContextPulseDaemon()
        yield daemon

        # Cleanup
        daemon.stop_event.set()
        daemon._stop_modules()

    _platform_factory._instance = old_instance
