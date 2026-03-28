"""Tests for TouchModule — ModalityModule lifecycle and event emission."""

from unittest.mock import MagicMock, patch

import pytest
from contextpulse_core.spine import ContextEvent, EventType, Modality


class TestTouchModuleLifecycle:
    @pytest.fixture
    def module(self, tmp_path):
        with patch("contextpulse_touch.touch_module.get_touch_config") as mock_cfg:
            mock_cfg.return_value = {
                "burst_timeout": 1.5,
                "correction_window": 15.0,
                "min_burst_chars": 3,
                "mouse_debounce": 0.1,
            }
            from contextpulse_touch.touch_module import TouchModule
            m = TouchModule(db_path=tmp_path / "test.db")
            yield m

    def test_get_modality(self, module):
        assert module.get_modality() == Modality.KEYS

    def test_initial_state(self, module):
        assert not module.is_alive()
        status = module.get_status()
        assert status["running"] is False
        assert status["events_emitted"] == 0
        assert status["error"] is None
        assert status["corrections_detected"] == 0

    def test_register_callback(self, module):
        cb = MagicMock()
        module.register(cb)
        assert module._callback is cb

    def test_stop_without_start(self, module):
        module.stop()
        assert not module.is_alive()

    def test_get_config_schema(self, module):
        schema = module.get_config_schema()
        assert "touch_burst_timeout" in schema
        assert "touch_correction_window" in schema
        assert "touch_mouse_debounce" in schema

    def test_emit_increments_counter(self, module):
        received = []
        module.register(lambda e: received.append(e))
        module._running = True
        event = ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST)
        module._emit(event)
        assert module._events_emitted == 1
        assert len(received) == 1

    def test_emit_without_callback(self, module):
        module._running = True
        event = ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST)
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_when_not_running(self, module):
        module.register(MagicMock())
        module._running = False
        event = ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST)
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_callback_error_captured(self, module):
        module.register(lambda e: 1/0)
        module._running = True
        event = ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST)
        module._emit(event)
        assert module._error is not None


class TestTouchModuleEvents:
    @pytest.fixture
    def module_with_callback(self, tmp_path):
        with patch("contextpulse_touch.touch_module.get_touch_config") as mock_cfg:
            mock_cfg.return_value = {
                "burst_timeout": 1.5,
                "correction_window": 15.0,
                "min_burst_chars": 3,
                "mouse_debounce": 0.1,
            }
            from contextpulse_touch.touch_module import TouchModule
            m = TouchModule(db_path=tmp_path / "test.db")
            received = []
            m.register(lambda e: received.append(e))
            m._running = True
            yield m, received

    def test_on_burst_emits_typing_burst(self, module_with_callback):
        module, received = module_with_callback
        module._on_burst({
            "char_count": 20, "word_count": 4,
            "duration_ms": 2000, "wpm": 120,
            "backspace_count": 1, "has_selection": False,
        })
        assert len(received) == 1
        assert received[0].modality == Modality.KEYS
        assert received[0].event_type == EventType.TYPING_BURST

    def test_on_mouse_click_emits_click(self, module_with_callback):
        module, received = module_with_callback
        module._on_mouse_click({
            "x": 100, "y": 200, "button": "left",
            "click_type": "single", "app_name": "chrome.exe",
            "window_title": "Google",
        })
        assert len(received) == 1
        assert received[0].modality == Modality.FLOW
        assert received[0].event_type == EventType.CLICK
        assert received[0].app_name == "chrome.exe"

    def test_on_mouse_scroll_emits_scroll(self, module_with_callback):
        module, received = module_with_callback
        module._on_mouse_scroll({
            "x": 100, "y": 200, "dx": 0, "dy": -3,
            "app_name": "code.exe", "window_title": "test.py",
        })
        assert len(received) == 1
        assert received[0].event_type == EventType.SCROLL

    def test_on_mouse_drag_emits_drag(self, module_with_callback):
        module, received = module_with_callback
        module._on_mouse_drag({
            "start_x": 100, "start_y": 200,
            "end_x": 300, "end_y": 200,
            "duration_ms": 350,
            "app_name": "code.exe", "window_title": "test.py",
        })
        assert len(received) == 1
        assert received[0].event_type == EventType.DRAG

    def test_on_correction_emits_event(self, module_with_callback):
        module, received = module_with_callback
        module._on_correction({
            "original_word": "cube control",
            "corrected_word": "kubectl",
            "correction_type": "select_replace",
            "confidence": 0.85,
            "seconds_after_paste": 4.2,
            "paste_event_id": "abc123",
        })
        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == EventType.CORRECTION_DETECTED
        assert evt.payload["original_text"] == "cube control"
        assert evt.payload["corrected_text"] == "kubectl"
        assert "correction_text" in evt.payload  # FTS-searchable
