"""Tests for SightModule — the spine adapter for the Sight capture pipeline."""

import time
from pathlib import Path

import pytest

from contextpulse_core.spine import EventBus, EventType, Modality
from contextpulse_sight.sight_module import SightModule


@pytest.fixture
def module():
    return SightModule()


@pytest.fixture
def bus(tmp_path):
    b = EventBus(tmp_path / "test.db")
    yield b
    b.close()


@pytest.fixture
def wired_module(module, bus):
    """Module registered with an EventBus."""
    module.register(bus.emit)
    module.start()
    return module


class TestSightModuleLifecycle:
    def test_get_modality(self, module):
        assert module.get_modality() == Modality.SIGHT

    def test_starts_stopped(self, module):
        assert not module.is_alive()

    def test_start_stop(self, module):
        module.start()
        assert module.is_alive()
        module.stop()
        assert not module.is_alive()

    def test_stop_is_idempotent(self, module):
        module.stop()
        module.stop()
        assert not module.is_alive()

    def test_status_before_start(self, module):
        status = module.get_status()
        assert status["modality"] == "sight"
        assert status["running"] is False
        assert status["events_emitted"] == 0
        assert status["last_event_timestamp"] is None
        assert status["error"] is None

    def test_status_after_events(self, wired_module, bus):
        wired_module.emit_capture(
            timestamp=time.time(), app_name="Chrome",
            window_title="Google", monitor_index=0,
            frame_path="/tmp/frame.jpg", diff_score=0.05,
        )
        status = wired_module.get_status()
        assert status["running"] is True
        assert status["events_emitted"] == 1
        assert status["last_event_timestamp"] is not None


class TestSightModuleEmitCapture:
    def test_emit_capture_creates_event(self, wired_module, bus):
        ts = time.time()
        wired_module.emit_capture(
            timestamp=ts, app_name="Code", window_title="main.py",
            monitor_index=0, frame_path="/tmp/f.jpg", diff_score=0.03,
            token_estimate=516, storage_mode="image",
        )
        events = bus.query_recent(seconds=60, modality="sight")
        assert len(events) == 1
        e = events[0]
        assert e.modality == Modality.SIGHT
        assert e.event_type == EventType.SCREEN_CAPTURE
        assert e.app_name == "Code"
        assert e.payload["frame_path"] == "/tmp/f.jpg"
        assert e.payload["diff_score"] == 0.03
        assert e.payload["token_estimate"] == 516

    def test_emit_capture_not_emitted_when_stopped(self, module, bus):
        module.register(bus.emit)
        # NOT started
        module.emit_capture(
            timestamp=time.time(), app_name="X", window_title="Y",
            monitor_index=0, frame_path="/tmp/x.jpg", diff_score=0.01,
        )
        assert bus.count() == 0

    def test_emit_capture_not_emitted_without_register(self, module):
        module.start()
        # No callback registered — should not crash
        module.emit_capture(
            timestamp=time.time(), app_name="X", window_title="Y",
            monitor_index=0, frame_path="/tmp/x.jpg", diff_score=0.01,
        )
        assert module.get_status()["events_emitted"] == 0


class TestSightModuleEmitOCR:
    def test_emit_ocr(self, wired_module, bus):
        ts = time.time()
        wired_module.emit_ocr(
            timestamp=ts, frame_path="/tmp/f.jpg",
            ocr_text="error: connection refused", confidence=0.92,
            app_name="Terminal", window_title="bash",
        )
        events = bus.query_recent(seconds=60, modality="sight")
        assert len(events) == 1
        assert events[0].event_type == EventType.OCR_RESULT
        assert events[0].payload["ocr_text"] == "error: connection refused"

    def test_ocr_text_is_fts_searchable(self, wired_module, bus):
        wired_module.emit_ocr(
            timestamp=time.time(), frame_path="/tmp/f.jpg",
            ocr_text="unique_search_term_42", confidence=0.9,
        )
        results = bus.search("unique_search_term_42", minutes_ago=5)
        assert len(results) >= 1


class TestSightModuleEmitClipboard:
    def test_emit_clipboard(self, wired_module, bus):
        ts = time.time()
        wired_module.emit_clipboard(
            timestamp=ts, text="copied text here",
            hash_val="abc123", source_app="Chrome",
        )
        events = bus.query_recent(seconds=60, modality="clipboard")
        assert len(events) == 1
        assert events[0].event_type == EventType.CLIPBOARD_CHANGE
        assert events[0].payload["text"] == "copied text here"
        assert events[0].payload["source_app"] == "Chrome"

    def test_clipboard_text_is_fts_searchable(self, wired_module, bus):
        wired_module.emit_clipboard(
            timestamp=time.time(), text="special_clipboard_content_99",
            hash_val="xyz", source_app=None,
        )
        results = bus.search("special_clipboard_content_99", minutes_ago=5)
        assert len(results) >= 1


class TestSightModuleSystemEvents:
    def test_emit_window_focus(self, wired_module, bus):
        wired_module.emit_window_focus("Chrome", "Google Search")
        events = bus.query_recent(seconds=60, modality="system")
        assert len(events) == 1
        assert events[0].event_type == EventType.WINDOW_FOCUS
        assert events[0].app_name == "Chrome"

    def test_emit_idle_start(self, wired_module, bus):
        wired_module.emit_idle(idle_start=True)
        events = bus.query_recent(seconds=60, modality="system")
        assert len(events) == 1
        assert events[0].event_type == EventType.IDLE_START

    def test_emit_idle_end(self, wired_module, bus):
        wired_module.emit_idle(idle_start=False)
        events = bus.query_recent(seconds=60, modality="system")
        assert events[0].event_type == EventType.IDLE_END

    def test_emit_session_lock(self, wired_module, bus):
        wired_module.emit_session_lock(locked=True)
        events = bus.query_recent(seconds=60, modality="system")
        assert events[0].event_type == EventType.SESSION_LOCK

    def test_emit_session_unlock(self, wired_module, bus):
        wired_module.emit_session_lock(locked=False)
        events = bus.query_recent(seconds=60, modality="system")
        assert events[0].event_type == EventType.SESSION_UNLOCK


class TestSightModuleErrorHandling:
    def test_callback_error_captured_in_status(self, module):
        def bad_callback(event):
            raise RuntimeError("db write failed")

        module.register(bad_callback)
        module.start()
        module.emit_capture(
            timestamp=time.time(), app_name="X", window_title="Y",
            monitor_index=0, frame_path="/tmp/x.jpg", diff_score=0.01,
        )
        status = module.get_status()
        assert status["error"] is not None
        assert "db write failed" in status["error"]

    def test_callback_error_does_not_crash_module(self, module):
        call_count = 0

        def flaky_callback(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call fails")

        module.register(flaky_callback)
        module.start()
        # First call errors
        module.emit_capture(
            timestamp=time.time(), app_name="X", window_title="Y",
            monitor_index=0, frame_path="/tmp/1.jpg", diff_score=0.01,
        )
        # Second call succeeds
        module.emit_capture(
            timestamp=time.time(), app_name="X", window_title="Y",
            monitor_index=0, frame_path="/tmp/2.jpg", diff_score=0.02,
        )
        assert call_count == 2  # Both calls reached the callback
