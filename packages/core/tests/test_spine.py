"""Tests for the ContextPulse Spine — ContextEvent, EventBus, ModalityModule."""

import json
import sqlite3
import time

import pytest
from contextpulse_core.spine import (
    ContextEvent,
    EventBus,
    EventType,
    Modality,
    ModalityModule,
)

# ---------------------------------------------------------------------------
# ContextEvent tests
# ---------------------------------------------------------------------------

class TestContextEvent:
    def test_create_with_defaults(self):
        event = ContextEvent()
        assert event.modality == Modality.SYSTEM
        assert event.event_type == EventType.WINDOW_FOCUS
        assert len(event.event_id) == 16
        assert event.timestamp > 0
        assert event.payload == {}
        assert event.correlation_id is None
        assert event.attention_score == 0.0

    def test_create_sight_capture(self):
        event = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name="Code",
            window_title="main.py - VS Code",
            monitor_index=0,
            payload={"frame_path": "/screenshots/123.jpg", "diff_score": 0.05},
        )
        assert event.modality == Modality.SIGHT
        assert event.event_type == EventType.SCREEN_CAPTURE
        assert event.app_name == "Code"
        assert event.payload["diff_score"] == 0.05

    def test_create_voice_transcription(self):
        event = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={"transcript": "hello world", "confidence": 0.92},
        )
        assert event.modality == Modality.VOICE
        assert event.payload["transcript"] == "hello world"

    def test_create_clipboard(self):
        event = ContextEvent(
            modality=Modality.CLIPBOARD,
            event_type=EventType.CLIPBOARD_CHANGE,
            payload={"text": "copied text", "hash": "abc123"},
        )
        assert event.modality == Modality.CLIPBOARD

    def test_validate_valid_event(self):
        event = ContextEvent()
        assert event.validate() is True

    def test_validate_bad_timestamp_zero(self):
        event = ContextEvent(timestamp=0.0)
        assert event.validate() is False

    def test_validate_bad_timestamp_negative(self):
        event = ContextEvent(timestamp=-1.0)
        assert event.validate() is False

    def test_validate_future_timestamp_within_tolerance(self):
        event = ContextEvent(timestamp=time.time() + 30)
        assert event.validate() is True

    def test_validate_future_timestamp_beyond_tolerance(self):
        event = ContextEvent(timestamp=time.time() + 120)
        assert event.validate() is False

    def test_to_row(self):
        event = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name="Chrome",
            payload={"frame_path": "/tmp/frame.jpg"},
        )
        row = event.to_row()
        assert row["modality"] == "sight"
        assert row["event_type"] == "screen_capture"
        assert row["app_name"] == "Chrome"
        assert json.loads(row["payload"])["frame_path"] == "/tmp/frame.jpg"
        assert isinstance(row["timestamp"], float)

    def test_text_content_ocr(self):
        event = ContextEvent(
            payload={"ocr_text": "error: connection refused"},
        )
        assert "error: connection refused" in event.text_content()

    def test_text_content_transcript(self):
        event = ContextEvent(
            payload={"transcript": "deploy the fix"},
        )
        assert "deploy the fix" in event.text_content()

    def test_text_content_clipboard(self):
        event = ContextEvent(
            payload={"text": "pasted content"},
        )
        assert "pasted content" in event.text_content()

    def test_text_content_empty_payload(self):
        event = ContextEvent(payload={})
        assert event.text_content() == ""

    def test_text_content_multiple_keys(self):
        event = ContextEvent(
            payload={"ocr_text": "screen text", "transcript": "spoken text"},
        )
        content = event.text_content()
        assert "screen text" in content
        assert "spoken text" in content

    def test_from_row(self):
        original = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            app_name="Zoom",
            window_title="Meeting",
            payload={"transcript": "hello", "confidence": 0.9},
        )
        row = original.to_row()
        reconstructed = ContextEvent.from_row(row)
        assert reconstructed.modality == Modality.VOICE
        assert reconstructed.event_type == EventType.TRANSCRIPTION
        assert reconstructed.app_name == "Zoom"
        assert reconstructed.payload["transcript"] == "hello"

    def test_from_row_with_string_payload(self):
        row = {
            "event_id": "test123",
            "timestamp": time.time(),
            "modality": "sight",
            "event_type": "screen_capture",
            "payload": '{"frame_path": "/tmp/test.jpg"}',
        }
        event = ContextEvent.from_row(row)
        assert event.payload["frame_path"] == "/tmp/test.jpg"

    def test_frozen_immutable(self):
        event = ContextEvent()
        with pytest.raises(AttributeError):
            event.app_name = "should fail"  # type: ignore[misc]

    def test_unique_event_ids(self):
        ids = {ContextEvent().event_id for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# EventBus tests
# ---------------------------------------------------------------------------

@pytest.fixture
def bus(tmp_path):
    """Create an EventBus with a temporary database."""
    db_path = tmp_path / "test_activity.db"
    b = EventBus(db_path)
    yield b
    b.close()


class TestEventBus:
    def test_schema_creation(self, bus):
        """Events table and FTS should exist after init."""
        with bus._lock:
            cursor = bus._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert "events" in tables
        assert "events_fts" in tables

    def test_schema_idempotent(self, tmp_path):
        """Creating EventBus twice on same DB should not error."""
        db_path = tmp_path / "idem.db"
        bus1 = EventBus(db_path)
        bus2 = EventBus(db_path)
        bus1.close()
        bus2.close()

    def test_preserves_existing_tables(self, tmp_path):
        """EventBus should not destroy existing tables in activity.db."""
        db_path = tmp_path / "existing.db"
        # Create a pre-existing table (simulating Sight's activity table)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE activity (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO activity VALUES (1, 'test')")
        conn.commit()
        conn.close()

        # Open EventBus on the same file
        b = EventBus(db_path)
        # Verify existing table still has data
        with b._lock:
            cursor = b._conn.execute("SELECT data FROM activity WHERE id=1")
            assert cursor.fetchone()[0] == "test"
        b.close()

    def test_emit_and_count(self, bus):
        event = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
        )
        bus.emit(event)
        assert bus.count() == 1

    def test_emit_invalid_event(self, bus):
        event = ContextEvent(timestamp=-1.0)
        with pytest.raises(ValueError):
            bus.emit(event)

    def test_emit_duplicate_ignored(self, bus):
        event = ContextEvent(event_id="duplicate123")
        bus.emit(event)
        bus.emit(event)  # OR IGNORE
        assert bus.count() == 1

    def test_query_recent(self, bus):
        for i in range(5):
            bus.emit(ContextEvent(
                modality=Modality.SIGHT,
                event_type=EventType.SCREEN_CAPTURE,
                app_name=f"app_{i}",
            ))
        results = bus.query_recent(seconds=60)
        assert len(results) == 5
        assert all(isinstance(e, ContextEvent) for e in results)

    def test_query_recent_with_modality_filter(self, bus):
        bus.emit(ContextEvent(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE))
        bus.emit(ContextEvent(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION))
        bus.emit(ContextEvent(modality=Modality.CLIPBOARD, event_type=EventType.CLIPBOARD_CHANGE))

        sight_only = bus.query_recent(seconds=60, modality="sight")
        assert len(sight_only) == 1
        assert sight_only[0].modality == Modality.SIGHT

    def test_query_recent_with_limit(self, bus):
        for _ in range(10):
            bus.emit(ContextEvent())
        results = bus.query_recent(seconds=60, limit=3)
        assert len(results) == 3

    def test_search_fts(self, bus):
        bus.emit(ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload={"ocr_text": "connection refused error in main.py"},
        ))
        bus.emit(ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload={"ocr_text": "all tests passing"},
        ))
        results = bus.search("connection refused", minutes_ago=5)
        assert len(results) >= 1

    def test_search_voice_transcript(self, bus):
        bus.emit(ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={"transcript": "deploy the authentication fix"},
        ))
        results = bus.search("authentication", minutes_ago=5)
        assert len(results) >= 1

    def test_search_cross_modal(self, bus):
        """Searching without modality filter finds events from all modalities."""
        bus.emit(ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload={"ocr_text": "deploy error on staging"},
        ))
        bus.emit(ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={"transcript": "we need to fix the deploy error"},
        ))
        results = bus.search("deploy error", minutes_ago=5)
        assert len(results) >= 2

    def test_search_with_modality_filter(self, bus):
        bus.emit(ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload={"ocr_text": "unique search term xyz"},
        ))
        bus.emit(ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={"transcript": "unique search term xyz"},
        ))
        results = bus.search("unique search term xyz", minutes_ago=5, modality="voice")
        modalities = {r.get("modality") for r in results}
        assert "sight" not in modalities

    def test_get_by_time(self, bus):
        target_ts = time.time() - 200  # Use past timestamps to avoid validation limits
        bus.emit(ContextEvent(timestamp=target_ts, modality=Modality.SIGHT,
                              event_type=EventType.SCREEN_CAPTURE))
        bus.emit(ContextEvent(timestamp=target_ts + 1, modality=Modality.VOICE,
                              event_type=EventType.TRANSCRIPTION))
        bus.emit(ContextEvent(timestamp=target_ts - 100, modality=Modality.SIGHT,
                              event_type=EventType.SCREEN_CAPTURE))

        results = bus.get_by_time(target_ts, window_seconds=5)
        assert len(results) == 2  # Only the two within 5s window

    def test_count_by_modality(self, bus):
        bus.emit(ContextEvent(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE))
        bus.emit(ContextEvent(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE))
        bus.emit(ContextEvent(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION))
        assert bus.count(modality="sight") == 2
        assert bus.count(modality="voice") == 1
        assert bus.count() == 3

    def test_listener_notification(self, bus):
        received = []
        bus.on(lambda e: received.append(e))
        event = ContextEvent(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE)
        bus.emit(event)
        assert len(received) == 1
        assert received[0].event_id == event.event_id

    def test_listener_error_does_not_crash_emit(self, bus):
        def bad_listener(e):
            raise RuntimeError("listener broke")
        bus.on(bad_listener)
        # Should not raise
        bus.emit(ContextEvent())
        assert bus.count() == 1


# ---------------------------------------------------------------------------
# ModalityModule contract tests
# ---------------------------------------------------------------------------

class MockModule(ModalityModule):
    """Concrete implementation for testing the ABC contract."""

    def __init__(self):
        self._callback = None
        self._running = False
        self._emitted = 0

    def get_modality(self) -> Modality:
        return Modality.SIGHT

    def register(self, event_callback):
        self._callback = event_callback

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_alive(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        return {
            "modality": "sight",
            "running": self._running,
            "events_emitted": self._emitted,
            "last_event_timestamp": None,
            "error": None,
        }


class TestModalityModule:
    def test_mock_satisfies_abc(self):
        module = MockModule()
        assert isinstance(module, ModalityModule)

    def test_lifecycle(self):
        module = MockModule()
        assert not module.is_alive()
        module.start()
        assert module.is_alive()
        module.stop()
        assert not module.is_alive()

    def test_register_and_emit(self, bus):
        module = MockModule()
        received = []
        module.register(lambda e: received.append(e))
        module.start()
        event = ContextEvent(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE)
        module._callback(event)
        assert len(received) == 1

    def test_get_status_shape(self):
        module = MockModule()
        status = module.get_status()
        assert "modality" in status
        assert "running" in status
        assert "events_emitted" in status
        assert "last_event_timestamp" in status
        assert "error" in status

    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            ModalityModule()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Modality and EventType enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_modality_values(self):
        assert Modality.SIGHT.value == "sight"
        assert Modality.VOICE.value == "voice"
        assert Modality.CLIPBOARD.value == "clipboard"
        assert Modality.SYSTEM.value == "system"

    def test_event_type_values(self):
        assert EventType.SCREEN_CAPTURE.value == "screen_capture"
        assert EventType.TRANSCRIPTION.value == "transcription"
        assert EventType.CLIPBOARD_CHANGE.value == "clipboard_change"
        assert EventType.WINDOW_FOCUS.value == "window_focus"

    def test_modality_from_string(self):
        assert Modality("sight") == Modality.SIGHT
        assert Modality("voice") == Modality.VOICE
