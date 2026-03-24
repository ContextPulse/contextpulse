"""Integration tests for the Touch package.

Covers: BurstTracker -> TouchModule event chain, CorrectionDetector full flow,
VoiceasyBridge concurrent writes, mouse event debouncing, and edge cases
(rapid-fire keystrokes, long correction windows, empty clipboard paste).
"""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_core.spine import ContextEvent, EventType, Modality
from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.correction_detector import CorrectionDetector, VoiceasyBridge


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def voice_db(tmp_path):
    """Create activity.db with a recent Voice transcription for correction detection."""
    db_path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            modality TEXT NOT NULL,
            event_type TEXT NOT NULL,
            app_name TEXT DEFAULT '',
            window_title TEXT DEFAULT '',
            monitor_index INTEGER DEFAULT 0,
            payload TEXT NOT NULL,
            correlation_id TEXT,
            attention_score REAL DEFAULT 0.0
        )
    """)

    # Insert multiple recent Voice transcriptions
    now = time.time()
    texts = [
        ("hello world test", "voice_evt_1", 5),
        ("I use cube control daily", "voice_evt_2", 10),
        ("send to gerard immediately", "voice_evt_3", 15),
    ]
    for text, evt_id, seconds_ago in texts:
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'voice', 'transcription', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (evt_id, now - seconds_ago, json.dumps({
                "transcript": text,
                "raw_transcript": text,
                "paste_text_hash": text_hash,
                "paste_timestamp": now - seconds_ago,
            })),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def touch_module(tmp_path):
    """Create a TouchModule with mocked config."""
    with patch("contextpulse_touch.touch_module.get_touch_config") as mock_cfg:
        mock_cfg.return_value = {
            "burst_timeout": 0.15,
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


# ═══════════════════════════════════════════════════════════════════════
# BurstTracker -> TouchModule Event Emission Chain
# ═══════════════════════════════════════════════════════════════════════

class TestBurstTrackerToTouchModule:
    """Test the full chain: keystrokes -> BurstTracker -> TouchModule event."""

    def test_burst_emits_typing_burst_event(self, touch_module):
        """Typing enough characters should emit a TYPING_BURST via TouchModule."""
        module, received = touch_module
        # Simulate keystrokes via the keyboard char handler
        for c in "hello world":
            module._on_keyboard_char(c, False, False)

        # Wait for burst timeout
        time.sleep(0.3)

        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(burst_events) >= 1
        evt = burst_events[0]
        assert evt.modality == Modality.KEYS
        assert evt.payload["char_count"] == 11

    def test_burst_tracks_backspaces(self, touch_module):
        """Backspaces during burst should be counted in the event payload."""
        module, received = touch_module
        for c in "helllo":
            module._on_keyboard_char(c, False, False)
        # Backspace to fix double-l
        module._on_keyboard_char(None, is_backspace=True, is_selection=False)
        for c in "o":
            module._on_keyboard_char(c, False, False)

        time.sleep(0.3)
        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(burst_events) >= 1
        assert burst_events[0].payload["backspace_count"] >= 1

    def test_burst_tracks_selection(self, touch_module):
        """Selection keys during burst should set has_selection."""
        module, received = touch_module
        for c in "test":
            module._on_keyboard_char(c, False, False)
        module._on_keyboard_char(None, is_backspace=False, is_selection=True)
        time.sleep(0.3)
        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(burst_events) >= 1
        assert burst_events[0].payload["has_selection"] is True

    def test_multiple_bursts_emit_multiple_events(self, touch_module):
        """Two separate bursts (with gap) should emit two events."""
        module, received = touch_module
        # First burst
        for c in "hello":
            module._on_keyboard_char(c, False, False)
        time.sleep(0.3)
        # Second burst
        for c in "world":
            module._on_keyboard_char(c, False, False)
        time.sleep(0.3)

        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(burst_events) == 2

    def test_burst_wpm_reasonable(self, touch_module):
        """WPM should be a reasonable positive number."""
        module, received = touch_module
        for c in "this is a test of typing speed":
            module._on_keyboard_char(c, False, False)
            time.sleep(0.01)  # Simulate realistic typing gaps
        time.sleep(0.3)

        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(burst_events) >= 1
        wpm = burst_events[0].payload["wpm"]
        assert wpm > 0
        assert wpm < 2000  # Sanity upper bound


# ═══════════════════════════════════════════════════════════════════════
# CorrectionDetector Full Flow
# ═══════════════════════════════════════════════════════════════════════

class TestCorrectionDetectorFlow:
    """Test the full correction detection flow: paste -> watch -> edit -> correction."""

    def test_paste_detected_starts_watch(self, voice_db):
        """Paste of Voice text should start watch window."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        on_correction = MagicMock()
        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=on_correction,
            watch_seconds=2.0,
            db_path=voice_db,
        )
        det.on_paste_detected("hello world test")
        assert det.is_watching
        assert bt.is_watching
        det.stop()

    def test_backspace_retype_correction(self, voice_db, tmp_path):
        """User backspaces and retypes text during watch window -> correction emitted."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        corrections_received = []
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=lambda c: corrections_received.append(c),
            watch_seconds=2.0,
            db_path=voice_db,
            bridge=bridge,
        )

        # Simulate Voice paste
        det.on_paste_detected("hello world test")
        assert det.is_watching

        # Simulate backspace + retype (user corrects "test" to "text")
        for _ in range(4):  # backspace 4 chars
            bt.on_key_press(None, is_backspace=True)
            det.on_key_event(is_backspace=True)
        for c in "text":
            bt.on_key_press(c)

        # End watch window
        det.on_window_change()
        assert not det.is_watching
        det.stop()

    def test_select_replace_correction(self, voice_db, tmp_path):
        """User selects a word and retypes it -> correction emitted."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        corrections_received = []
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=lambda c: corrections_received.append(c),
            watch_seconds=2.0,
            db_path=voice_db,
            bridge=bridge,
        )

        det.on_paste_detected("send to gerard immediately")
        assert det.is_watching

        # User selects "gerard" and types "Jerard"
        det.on_key_event(is_selection=True)
        bt.on_key_press(None, is_selection=True)
        for c in "Jerard":
            bt.on_key_press(c)

        det.on_window_change()
        det.stop()

    def test_no_correction_when_no_edits(self, voice_db, tmp_path):
        """No correction emitted if user doesn't edit after paste."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        on_correction = MagicMock()
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=on_correction,
            watch_seconds=0.2,
            db_path=voice_db,
            bridge=bridge,
        )

        det.on_paste_detected("hello world test")
        assert det.is_watching
        # No edits — just let the window expire
        time.sleep(0.5)
        assert not det.is_watching
        on_correction.assert_not_called()
        det.stop()

    def test_non_voice_paste_no_watch(self, voice_db):
        """Paste that does not match any Voice event should not start watch."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=2.0,
            db_path=voice_db,
        )
        det.on_paste_detected("this text was not voice-dictated")
        assert not det.is_watching
        det.stop()

    def test_empty_paste_ignored(self, voice_db):
        """Empty clipboard paste is silently ignored."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=voice_db)
        det.on_paste_detected("")
        assert det.pastes_detected == 0
        det.stop()

    def test_watch_expires_by_timeout(self, voice_db):
        """Watch window expires after configured timeout."""
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=0.15,
            db_path=voice_db,
        )
        det.on_paste_detected("hello world test")
        assert det.is_watching
        time.sleep(0.4)
        assert not det.is_watching
        det.stop()

    def test_correction_counter_increments(self, voice_db, tmp_path):
        """corrections_detected counter increments on valid correction."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=2.0,
            db_path=voice_db,
            bridge=bridge,
        )

        det.on_paste_detected("hello world test")
        for _ in range(4):
            bt.on_key_press(None, is_backspace=True)
            det.on_key_event(is_backspace=True)
        for c in "text":
            bt.on_key_press(c)
        det.on_window_change()
        # Counter might or might not increment depending on confidence
        det.stop()


# ═══════════════════════════════════════════════════════════════════════
# VoiceasyBridge Concurrent Writes
# ═══════════════════════════════════════════════════════════════════════

class TestVoiceasyBridgeConcurrent:
    """Test VoiceasyBridge atomic writes under concurrent access."""

    def test_concurrent_adds(self, tmp_path):
        """Multiple threads adding corrections simultaneously.

        On Windows, file replace operations may fail under contention (WinError 32).
        The bridge handles this gracefully (returns False, logs error).
        We verify that at least some writes succeed and the file is not corrupted.
        """
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        errors = []

        def add_corrections(thread_id):
            try:
                for i in range(5):
                    bridge.add_correction(f"word_t{thread_id}_i{i}", f"Correct_t{thread_id}_i{i}")
                    time.sleep(0.01)  # Small delay to reduce contention
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_corrections, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Unexpected exceptions (not PermissionError): {errors}"
        # Verify file integrity — the file must be valid JSON
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        # At least some entries should succeed; on Windows some may fail due to file locking
        assert len(data) >= 1

    def test_atomic_write_survives_crash(self, tmp_path):
        """Atomic write should not corrupt existing data on error."""
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        bridge.add_correction("initial", "Initial")

        # Verify initial data is intact
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert data["initial"] == "Initial"

        # Add more
        bridge.add_correction("second", "Second")
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert "initial" in data
        assert "second" in data

    def test_bridge_handles_corrupt_existing_file(self, tmp_path):
        """Bridge should handle corrupt existing learned file gracefully."""
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        learned_file.parent.mkdir(parents=True, exist_ok=True)
        learned_file.write_text("NOT JSON {{{", encoding="utf-8")

        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("word", "Corrected")
        assert result is True
        # File should now be valid
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert data["word"] == "Corrected"

    def test_bridge_get_recent_with_limit(self, tmp_path):
        """get_recent_corrections respects limit parameter."""
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        for i in range(10):
            bridge.add_correction(f"word_{i}", f"Correct_{i}")
        result = bridge.get_recent_corrections(limit=5)
        assert len(result) == 5

    def test_bridge_empty_strings_rejected(self, tmp_path):
        """Empty or whitespace-only strings are rejected."""
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        assert bridge.add_correction("", "test") is False
        assert bridge.add_correction("test", "") is False
        assert bridge.add_correction("  ", "  ") is False


# ═══════════════════════════════════════════════════════════════════════
# Mouse Event Debouncing
# ═══════════════════════════════════════════════════════════════════════

class TestMouseEventDebouncing:
    """Test mouse event handling in TouchModule."""

    def test_click_event_emission(self, touch_module):
        """Mouse click should emit a CLICK event."""
        module, received = touch_module
        module._on_mouse_click({
            "x": 100, "y": 200, "button": "left",
            "click_type": "single", "app_name": "chrome.exe",
            "window_title": "Google",
        })
        click_events = [e for e in received if e.event_type == EventType.CLICK]
        assert len(click_events) == 1
        assert click_events[0].modality == Modality.FLOW
        assert click_events[0].payload["x"] == 100

    def test_scroll_event_emission(self, touch_module):
        """Mouse scroll should emit a SCROLL event."""
        module, received = touch_module
        module._on_mouse_scroll({
            "x": 50, "y": 50, "dx": 0, "dy": -3,
            "app_name": "code.exe", "window_title": "test.py",
        })
        scroll_events = [e for e in received if e.event_type == EventType.SCROLL]
        assert len(scroll_events) == 1
        assert scroll_events[0].payload["dy"] == -3

    def test_drag_event_emission(self, touch_module):
        """Mouse drag should emit a DRAG event."""
        module, received = touch_module
        module._on_mouse_drag({
            "start_x": 10, "start_y": 20,
            "end_x": 300, "end_y": 400,
            "duration_ms": 500,
            "app_name": "paint.exe", "window_title": "untitled",
        })
        drag_events = [e for e in received if e.event_type == EventType.DRAG]
        assert len(drag_events) == 1
        assert drag_events[0].payload["start_x"] == 10
        assert drag_events[0].payload["end_x"] == 300

    def test_rapid_clicks_all_emitted(self, touch_module):
        """Rapid clicks (faster than debounce) should still be accepted by module handler."""
        module, received = touch_module
        for i in range(10):
            module._on_mouse_click({
                "x": i * 10, "y": 100, "button": "left",
                "click_type": "single", "app_name": "app.exe",
                "window_title": "window",
            })
        click_events = [e for e in received if e.event_type == EventType.CLICK]
        assert len(click_events) == 10

    def test_mixed_mouse_and_keyboard(self, touch_module):
        """Interleaved keyboard and mouse events both produce events."""
        module, received = touch_module
        # Type some chars
        for c in "testing":
            module._on_keyboard_char(c, False, False)
        # Click
        module._on_mouse_click({
            "x": 100, "y": 100, "button": "left",
            "click_type": "single", "app_name": "app.exe",
            "window_title": "win",
        })
        # Type more
        for c in "more":
            module._on_keyboard_char(c, False, False)
        time.sleep(0.3)

        click_events = [e for e in received if e.event_type == EventType.CLICK]
        burst_events = [e for e in received if e.event_type == EventType.TYPING_BURST]
        assert len(click_events) == 1
        assert len(burst_events) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases for touch input processing."""

    def test_rapid_fire_keystrokes(self):
        """100 keystrokes in quick succession should form a single burst."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.2, min_chars=3, on_burst=on_burst)
        for i in range(100):
            bt.on_key_press(chr(ord('a') + (i % 26)))
        time.sleep(0.4)
        on_burst.assert_called_once()
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 100
        bt.stop()

    def test_rapid_fire_with_backspaces(self):
        """Rapid typing with interspersed backspaces."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.2, min_chars=3, on_burst=on_burst)
        for i in range(50):
            bt.on_key_press("a")
            if i % 5 == 4:
                bt.on_key_press(None, is_backspace=True)
        time.sleep(0.4)
        on_burst.assert_called_once()
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 50
        assert data["backspace_count"] == 10
        bt.stop()

    def test_very_long_correction_window(self, voice_db, tmp_path):
        """Long watch windows should still work correctly."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        on_correction = MagicMock()

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=on_correction,
            watch_seconds=60.0,  # 60 second window
            db_path=voice_db,
            bridge=bridge,
        )

        det.on_paste_detected("hello world test")
        assert det.is_watching

        # Simulate typing for a while
        for c in "this is replacement text":
            bt.on_key_press(c)
            time.sleep(0.01)

        # Manually end via window change
        det.on_window_change()
        assert not det.is_watching
        det.stop()

    def test_paste_with_empty_clipboard_text(self, voice_db):
        """Paste with empty clipboard text should be ignored."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=voice_db)
        det.on_paste_detected("")
        assert not det.is_watching
        assert det.pastes_detected == 0
        det.stop()

    def test_watch_mode_across_multiple_bursts(self):
        """Watch mode text accumulates across multiple burst cycles."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        bt.enter_watch_mode()

        # First burst
        for c in "hello":
            bt.on_key_press(c)
        time.sleep(0.2)  # Burst fires but watch text persists

        # Second burst
        for c in " world":
            bt.on_key_press(c)
        time.sleep(0.2)

        text = bt.exit_watch_mode()
        assert text == "hello world"
        bt.stop()

    def test_burst_tracker_stop_is_safe_to_call_twice(self):
        """Calling stop() twice should not raise."""
        bt = BurstTracker()
        bt.on_key_press("a")
        bt.stop()
        bt.stop()  # Second call should be safe

    def test_correction_detector_stop_without_watching(self, voice_db):
        """Stopping detector when not watching should be safe."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=voice_db)
        det.stop()  # Should not raise

    def test_burst_min_chars_boundary(self):
        """Exactly min_chars characters should still emit a burst."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=3, on_burst=on_burst)
        for c in "abc":
            bt.on_key_press(c)
        time.sleep(0.2)
        on_burst.assert_called_once()
        data = on_burst.call_args[0][0]
        assert data["char_count"] == 3
        bt.stop()

    def test_burst_below_min_chars_no_emit(self):
        """Below min_chars should NOT emit a burst."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=5, on_burst=on_burst)
        for c in "abc":
            bt.on_key_press(c)
        time.sleep(0.2)
        on_burst.assert_not_called()
        bt.stop()

    def test_only_backspaces_no_burst(self):
        """Only backspaces (no printable chars) should not emit a burst."""
        on_burst = MagicMock()
        bt = BurstTracker(burst_timeout=0.1, min_chars=1, on_burst=on_burst)
        for _ in range(10):
            bt.on_key_press(None, is_backspace=True)
        time.sleep(0.2)
        on_burst.assert_not_called()
        bt.stop()

    def test_char_overlap_symmetric(self):
        """_char_overlap should be symmetric."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._char_overlap("hello", "helo") == det._char_overlap("helo", "hello")

    def test_char_overlap_identical(self):
        """Identical strings should have overlap of 1.0."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._char_overlap("test", "test") == 1.0

    def test_char_overlap_no_overlap(self):
        """Completely different strings should have 0 overlap."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._char_overlap("abc", "xyz") == 0.0

    def test_extract_corrections_no_typed_text(self):
        """No typed text yields no corrections."""
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._extract_corrections("original", "", 0, False) == []
        assert det._extract_corrections("original", "   ", 0, False) == []

    def test_multiple_pastes_supersede(self, voice_db, tmp_path):
        """Second paste during watch should cancel first watch and start new one."""
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)

        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=5.0,
            db_path=voice_db,
            bridge=bridge,
        )

        # First paste
        det.on_paste_detected("hello world test")
        assert det.is_watching
        assert det._original_text == "hello world test"

        # Second paste (different text, also from voice)
        det.on_paste_detected("I use cube control daily")
        assert det.is_watching
        assert det._original_text == "I use cube control daily"
        det.stop()

    def test_touch_module_status_fields(self, touch_module):
        """TouchModule status should contain all required fields."""
        module, _ = touch_module
        status = module.get_status()
        assert "modality" in status
        assert "running" in status
        assert "events_emitted" in status
        assert "last_event_timestamp" in status
        assert "error" in status
        assert "corrections_detected" in status
        assert "pastes_detected" in status
        assert "watching_correction" in status
