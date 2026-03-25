"""Tests for dual-write wiring: SightModule integration with OCRWorker and ClipboardMonitor."""

import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_core.spine import EventBus, EventType, Modality
from contextpulse_sight.sight_module import SightModule


@pytest.fixture
def bus(tmp_path):
    b = EventBus(tmp_path / "test.db")
    yield b
    b.close()


@pytest.fixture
def sight_module(bus):
    mod = SightModule()
    mod.register(bus.emit)
    mod.start()
    return mod


class TestOCRWorkerDualWrite:
    """Test that OCRWorker emits events via SightModule when attached."""

    def test_set_sight_module(self):
        from contextpulse_sight.ocr_worker import OCRWorker
        db = MagicMock()
        buf = MagicMock()
        worker = OCRWorker(db, buf)
        assert worker._sight_module is None
        mock_mod = MagicMock()
        worker.set_sight_module(mock_mod)
        assert worker._sight_module is mock_mod

    def test_ocr_emits_event_when_module_attached(self, sight_module, bus, tmp_path):
        """Simulate OCR processing and verify EventBus receives OCR_RESULT."""
        from contextpulse_sight.ocr_worker import OCRWorker

        db = MagicMock()
        buf = MagicMock()
        worker = OCRWorker(db, buf)
        worker.set_sight_module(sight_module)

        # Create a fake frame file
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"fake")

        # Directly call emit_ocr through the module (simulating what _process does)
        sight_module.emit_ocr(
            timestamp=time.time(),
            frame_path=str(frame),
            ocr_text="error: connection refused",
            confidence=0.92,
            app_name="Terminal",
        )

        events = bus.query_recent(seconds=60, modality="sight")
        assert len(events) == 1
        assert events[0].event_type == EventType.OCR_RESULT
        assert events[0].payload["ocr_text"] == "error: connection refused"
        assert events[0].payload["ocr_confidence"] == 0.92

    def test_ocr_no_emit_without_module(self):
        """OCRWorker works fine without a sight module (backwards compatible)."""
        from contextpulse_sight.ocr_worker import OCRWorker
        db = MagicMock()
        buf = MagicMock()
        worker = OCRWorker(db, buf)
        # No set_sight_module call — _sight_module stays None
        assert worker._sight_module is None
        # Should not crash during normal operation


class TestClipboardMonitorDualWrite:
    """Test that ClipboardMonitor emits events via SightModule when attached."""

    def test_set_sight_module(self):
        from contextpulse_sight.clipboard import ClipboardMonitor
        db = MagicMock()
        mon = ClipboardMonitor(db)
        assert mon._sight_module is None
        mock_mod = MagicMock()
        mon.set_sight_module(mock_mod)
        assert mon._sight_module is mock_mod

    def test_clipboard_emits_event_when_module_attached(self, sight_module, bus):
        """Simulate clipboard change and verify EventBus receives CLIPBOARD_CHANGE."""
        text = "copied error message"
        hash_val = hashlib.sha256(text.encode()).hexdigest()[:16]

        sight_module.emit_clipboard(
            timestamp=time.time(),
            text=text,
            hash_val=hash_val,
        )

        events = bus.query_recent(seconds=60, modality="clipboard")
        assert len(events) == 1
        assert events[0].event_type == EventType.CLIPBOARD_CHANGE
        assert events[0].payload["text"] == text
        assert events[0].payload["hash"] == hash_val

    def test_clipboard_no_emit_without_module(self):
        """ClipboardMonitor works fine without a sight module (backwards compatible)."""
        from contextpulse_sight.clipboard import ClipboardMonitor
        db = MagicMock()
        mon = ClipboardMonitor(db)
        assert mon._sight_module is None


class TestAppDualWriteWiring:
    """Test that the app correctly wires SightModule into all components."""

    def test_app_has_sight_module_and_event_bus(self, tmp_path, monkeypatch):
        """ContextPulseSightApp creates SightModule + EventBus in __init__."""
        # Monkeypatch config paths to use tmp_path
        import contextpulse_sight.config as cfg
        import contextpulse_sight.activity as act
        monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

        from contextpulse_sight.app import ContextPulseSightApp
        app = ContextPulseSightApp()

        assert hasattr(app, "_sight_module")
        assert hasattr(app, "_event_bus")
        assert isinstance(app._sight_module, SightModule)
        assert isinstance(app._event_bus, EventBus)
        assert app._sight_module.is_alive()

        # Verify OCR worker and clipboard monitor have module attached
        assert app._ocr_worker._sight_module is app._sight_module
        assert app._clipboard_monitor._sight_module is app._sight_module

        # Cleanup
        app._sight_module.stop()
        app._event_bus.close()
        app.activity_db.close()

    def test_capture_emits_screen_capture_event(self, sight_module, bus):
        """Simulate what _do_auto_capture does after activity_db.record()."""
        ts = time.time()
        sight_module.emit_capture(
            timestamp=ts,
            app_name="Chrome",
            window_title="GitHub",
            monitor_index=0,
            frame_path="/tmp/frame.jpg",
            diff_score=0.05,
        )

        events = bus.query_recent(seconds=60, modality="sight")
        assert len(events) == 1
        assert events[0].event_type == EventType.SCREEN_CAPTURE
        assert events[0].app_name == "Chrome"
        assert events[0].window_title == "GitHub"
        assert events[0].payload["diff_score"] == 0.05

    def test_session_lock_emits_event(self, sight_module, bus):
        sight_module.emit_session_lock(locked=True)
        events = bus.query_recent(seconds=60, modality="system")
        assert len(events) == 1
        assert events[0].event_type == EventType.SESSION_LOCK

    def test_session_unlock_emits_event(self, sight_module, bus):
        sight_module.emit_session_lock(locked=False)
        events = bus.query_recent(seconds=60, modality="system")
        assert len(events) == 1
        assert events[0].event_type == EventType.SESSION_UNLOCK
