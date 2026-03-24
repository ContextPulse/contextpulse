"""Edge case tests for Touch — boundary conditions, concurrent access, error handling."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.correction_detector import CorrectionDetector, VoiceasyBridge


class TestBurstTrackerEdgeCases:
    def test_rapid_fire_keystrokes(self):
        """100 keys in rapid succession should produce one burst."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.2, min_chars=3, on_burst=on_burst)
        for i in range(100):
            bt.on_key_press(chr(97 + (i % 26)))  # a-z cycling
        time.sleep(0.4)
        # Should be exactly 1 burst with all 100 chars
        assert on_burst.call_count == 1
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 100
        bt.stop()

    def test_alternating_chars_and_backspaces(self):
        """Typing and deleting should track both counts."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=1, on_burst=on_burst)
        for _ in range(10):
            bt.on_key_press("a")
            bt.on_key_press(None, is_backspace=True)
        time.sleep(0.2)
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 10
        assert data["backspace_count"] == 10
        bt.stop()

    def test_none_key_char_ignored(self):
        """Non-printable keys (None char) shouldn't count as chars."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        bt.on_key_press(None)  # Ctrl, Alt, etc.
        bt.on_key_press(None)
        bt.on_key_press(None)
        time.sleep(0.2)
        on_burst.assert_not_called()  # 0 chars, below threshold
        bt.stop()

    def test_watch_mode_very_long_text(self):
        """Watch mode should handle capturing 1000+ chars."""
        bt = BurstTracker()
        bt.enter_watch_mode()
        for c in "a" * 1000:
            bt.on_key_press(c)
        text = bt.exit_watch_mode()
        assert len(text) == 1000

    def test_watch_mode_unicode(self):
        """Watch mode should capture unicode characters."""
        bt = BurstTracker()
        bt.enter_watch_mode()
        for c in "héllo wörld":
            bt.on_key_press(c)
        text = bt.exit_watch_mode()
        assert text == "héllo wörld"

    def test_watch_mode_reenter(self):
        """Entering watch mode multiple times should reset."""
        bt = BurstTracker()
        bt.enter_watch_mode()
        bt.on_key_press("a")
        bt.exit_watch_mode()

        bt.enter_watch_mode()
        bt.on_key_press("b")
        text = bt.exit_watch_mode()
        assert text == "b"

    def test_concurrent_key_presses(self):
        """Multiple threads pressing keys shouldn't crash."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.2, min_chars=3, on_burst=on_burst)

        def press_keys():
            for c in "hello":
                bt.on_key_press(c)
                time.sleep(0.01)

        threads = [threading.Thread(target=press_keys) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.4)
        # Should have emitted at least one burst without crashing
        assert on_burst.call_count >= 1
        bt.stop()

    def test_stop_idempotent(self):
        """Calling stop() multiple times should be safe."""
        bt = BurstTracker()
        bt.stop()
        bt.stop()
        bt.stop()


class TestVoiceasyBridgeEdgeCases:
    def test_concurrent_writes(self, tmp_path):
        """Multiple threads writing corrections simultaneously."""
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        def write_correction(i):
            bridge.add_correction(f"word{i}", f"Word{i}")

        threads = [threading.Thread(target=write_correction, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have written at least some without corrupting the file
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_corrupted_learned_file(self, tmp_path):
        """Should handle corrupted learned file gracefully."""
        voice_dir = tmp_path / "voice"
        voice_dir.mkdir()
        learned_file = voice_dir / "vocabulary_learned.json"
        learned_file.write_text("not valid json!!!", encoding="utf-8")

        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("test", "Test123")
        assert result is True  # Should overwrite corrupted file

    def test_unicode_correction(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("café", "Café")
        assert result is True
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert "café" in data

    def test_very_long_correction(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        long_word = "a" * 500
        result = bridge.add_correction(long_word, "short")
        assert result is True

    def test_special_chars_in_correction(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction('test"quote', 'test\\"quote')
        assert result is True
        # File should be valid JSON
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


class TestCorrectionDetectorEdgeCases:
    def test_paste_with_no_db(self):
        """Should handle missing database gracefully."""
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            db_path=Path("/nonexistent/path/db.sqlite"),
        )
        det.on_paste_detected("hello world")
        assert not det.is_watching  # No DB = no voice match

    def test_rapid_paste_events(self, activity_db):
        """Multiple rapid Ctrl+V should not crash."""
        db_path, text, text_hash = activity_db
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt, db_path=db_path, watch_seconds=0.5,
        )
        # Rapid pastes
        for _ in range(10):
            det.on_paste_detected(text)
        time.sleep(0.1)
        det.stop()

    def test_window_change_without_watching(self):
        """on_window_change when not watching should be safe."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        det.on_window_change()  # Should not crash

    def test_key_event_without_watching(self):
        """on_key_event when not watching should be safe."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        det.on_key_event(is_backspace=True)  # Should not crash

    def test_correction_callback_error_handled(self, activity_db):
        """Callback errors should not crash the detector."""
        db_path, text, text_hash = activity_db
        bt = BurstTracker()

        def bad_callback(correction):
            raise ValueError("test error")

        det = CorrectionDetector(
            burst_tracker=bt, on_correction=bad_callback,
            db_path=db_path, watch_seconds=0.2,
        )
        bridge_mock = MagicMock()
        bridge_mock.add_correction = MagicMock(return_value=True)
        det._bridge = bridge_mock

        det.on_paste_detected(text)
        # Simulate typing a correction
        bt.on_key_press(None, is_selection=True)
        for c in "corrected":
            bt.on_key_press(c)
        time.sleep(0.5)
        # Should not have crashed
        det.stop()

    def test_extract_corrections_whitespace_typed(self):
        """Typed text that is only whitespace should produce no corrections."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        result = det._extract_corrections("hello world", "   ", 0, True)
        assert result == []

    def test_char_overlap_identical(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._char_overlap("hello", "hello") == 1.0

    def test_char_overlap_partial(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        overlap = det._char_overlap("hello", "help")
        assert 0.5 < overlap < 1.0


class TestTouchModuleEdgeCases:
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

    def test_double_start(self, module):
        """Starting twice should be idempotent."""
        # Can't actually start (mocked pynput) but verify flag logic
        module._running = True
        module.start()  # Should return early

    def test_status_with_error(self, module):
        module._error = "test error"
        status = module.get_status()
        assert status["error"] == "test error"

    def test_emit_all_event_types(self, module):
        """Verify all event type handlers don't crash."""
        received = []
        module.register(lambda e: received.append(e))
        module._running = True

        module._on_burst({"char_count": 5, "word_count": 1, "duration_ms": 500, "wpm": 60, "backspace_count": 0, "has_selection": False})
        module._on_mouse_click({"x": 0, "y": 0, "button": "left", "click_type": "single", "app_name": "", "window_title": ""})
        module._on_mouse_scroll({"x": 0, "y": 0, "dx": 0, "dy": -1, "app_name": "", "window_title": ""})
        module._on_mouse_drag({"start_x": 0, "start_y": 0, "end_x": 100, "end_y": 0, "duration_ms": 100, "app_name": "", "window_title": ""})
        module._on_correction({"original_word": "a", "corrected_word": "b", "correction_type": "test", "confidence": 0.9, "seconds_after_paste": 1.0, "paste_event_id": "x"})

        assert len(received) == 5
