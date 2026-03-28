"""Tests for ContextEvent creation, validation, serialization, and text extraction."""

import json
import time

import pytest
from contextpulse_core.spine.events import (
    ContextEvent,
    EventType,
    Modality,
)


class TestContextEventCreation:
    """Test ContextEvent default construction and field assignment."""

    def test_default_creation(self):
        event = ContextEvent()
        assert event.event_id  # non-empty
        assert isinstance(event.timestamp, float)
        assert event.modality == Modality.SYSTEM
        assert event.event_type == EventType.WINDOW_FOCUS
        assert event.payload == {}

    def test_custom_fields(self):
        ts = time.time()
        event = ContextEvent(
            event_id="abc123",
            timestamp=ts,
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            app_name="Chrome",
            window_title="Google",
            monitor_index=1,
            payload={"ocr_text": "hello world"},
            correlation_id="corr-1",
            attention_score=0.75,
        )
        assert event.event_id == "abc123"
        assert event.timestamp == ts
        assert event.modality == Modality.SIGHT
        assert event.event_type == EventType.OCR_RESULT
        assert event.app_name == "Chrome"
        assert event.window_title == "Google"
        assert event.monitor_index == 1
        assert event.payload == {"ocr_text": "hello world"}
        assert event.correlation_id == "corr-1"
        assert event.attention_score == 0.75

    def test_frozen_immutability(self):
        event = ContextEvent()
        with pytest.raises(AttributeError):
            event.event_id = "changed"


class TestContextEventValidation:
    """Test ContextEvent.validate() with valid and invalid inputs."""

    def test_valid_default_event(self):
        event = ContextEvent()
        assert event.validate() is True

    def test_valid_custom_event(self):
        event = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={"transcript": "test"},
        )
        assert event.validate() is True

    def test_invalid_empty_event_id(self):
        event = ContextEvent(event_id="")
        assert event.validate() is False

    def test_invalid_negative_timestamp(self):
        event = ContextEvent(timestamp=-1.0)
        assert event.validate() is False

    def test_invalid_zero_timestamp(self):
        event = ContextEvent(timestamp=0.0)
        assert event.validate() is False

    def test_invalid_future_timestamp(self):
        # More than 60 seconds in the future
        event = ContextEvent(timestamp=time.time() + 120)
        assert event.validate() is False

    def test_valid_near_future_timestamp(self):
        # Within 60s buffer is OK
        event = ContextEvent(timestamp=time.time() + 30)
        assert event.validate() is True


class TestToRowFromRowRoundtrip:
    """Test serialization to/from database rows."""

    def test_roundtrip_simple(self):
        original = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name="VSCode",
            window_title="main.py",
        )
        row = original.to_row()
        restored = ContextEvent.from_row(row)

        assert restored.event_id == original.event_id
        assert restored.timestamp == original.timestamp
        assert restored.modality == original.modality
        assert restored.event_type == original.event_type
        assert restored.app_name == original.app_name
        assert restored.window_title == original.window_title
        assert restored.payload == original.payload

    def test_roundtrip_with_payload(self):
        payload = {"ocr_text": "hello", "confidence": 0.95, "nested": {"a": 1}}
        original = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload=payload,
        )
        row = original.to_row()
        # payload should be JSON string in the row
        assert isinstance(row["payload"], str)
        assert json.loads(row["payload"]) == payload

        restored = ContextEvent.from_row(row)
        assert restored.payload == payload

    def test_roundtrip_with_correlation(self):
        original = ContextEvent(
            correlation_id="batch-42",
            attention_score=0.9,
        )
        row = original.to_row()
        restored = ContextEvent.from_row(row)
        assert restored.correlation_id == "batch-42"
        assert restored.attention_score == 0.9

    def test_from_row_with_string_payload(self):
        row = {
            "event_id": "test1",
            "timestamp": time.time(),
            "modality": "system",
            "event_type": "window_focus",
            "payload": '{"key": "val"}',
        }
        event = ContextEvent.from_row(row)
        assert event.payload == {"key": "val"}

    def test_to_row_modality_is_string(self):
        event = ContextEvent(modality=Modality.VOICE)
        row = event.to_row()
        assert row["modality"] == "voice"
        assert row["event_type"] == "window_focus"


class TestTextContent:
    """Test text_content() extraction from payload."""

    def test_ocr_text(self):
        event = ContextEvent(payload={"ocr_text": "screen text here"})
        assert event.text_content() == "screen text here"

    def test_transcript(self):
        event = ContextEvent(payload={"transcript": "spoken words"})
        assert event.text_content() == "spoken words"

    def test_text_key(self):
        event = ContextEvent(payload={"text": "clipboard content"})
        assert event.text_content() == "clipboard content"

    def test_burst_text(self):
        event = ContextEvent(payload={"burst_text": "typed stuff"})
        assert event.text_content() == "typed stuff"

    def test_multiple_text_keys(self):
        event = ContextEvent(payload={
            "ocr_text": "screen",
            "transcript": "voice",
        })
        assert event.text_content() == "screen voice"

    def test_empty_payload(self):
        event = ContextEvent(payload={})
        assert event.text_content() == ""

    def test_non_text_payload(self):
        event = ContextEvent(payload={"confidence": 0.8, "count": 5})
        assert event.text_content() == ""


class TestCognitiveLoadField:
    """Test cognitive_load field added per spec section 3."""

    def test_default_cognitive_load(self):
        event = ContextEvent()
        assert event.cognitive_load == 0.0

    def test_custom_cognitive_load(self):
        event = ContextEvent(cognitive_load=0.75)
        assert event.cognitive_load == 0.75

    def test_cognitive_load_in_to_row(self):
        event = ContextEvent(cognitive_load=0.5)
        row = event.to_row()
        assert row["cognitive_load"] == 0.5

    def test_cognitive_load_in_from_row(self):
        import time as _time
        row = {
            "event_id": "cog1",
            "timestamp": _time.time(),
            "modality": "system",
            "event_type": "window_focus",
            "payload": "{}",
            "cognitive_load": 0.8,
        }
        event = ContextEvent.from_row(row)
        assert event.cognitive_load == 0.8

    def test_cognitive_load_defaults_zero_from_row(self):
        import time as _time
        row = {
            "event_id": "cog2",
            "timestamp": _time.time(),
            "modality": "system",
            "event_type": "window_focus",
            "payload": "{}",
        }
        event = ContextEvent.from_row(row)
        assert event.cognitive_load == 0.0


class TestModalityAndEventTypeEnums:
    """Test all modalities and event types from spec section 3."""

    def test_all_modalities_present(self):
        modalities = {m.value for m in Modality}
        assert "sight" in modalities
        assert "voice" in modalities
        assert "clipboard" in modalities
        assert "system" in modalities
        assert "keys" in modalities
        assert "flow" in modalities

    def test_keys_event_types_present(self):
        types = {t.value for t in EventType}
        assert "keystroke" in types
        assert "typing_burst" in types
        assert "typing_pause" in types
        assert "shortcut" in types

    def test_flow_event_types_present(self):
        types = {t.value for t in EventType}
        assert "click" in types
        assert "scroll" in types
        assert "hover_dwell" in types
        assert "drag" in types

    def test_keys_event_with_keys_modality(self):
        event = ContextEvent(
            modality=Modality.KEYS,
            event_type=EventType.TYPING_BURST,
            payload={"burst_text": "def main():", "wpm_snapshot": 65.0},
        )
        assert event.modality == Modality.KEYS
        assert event.event_type == EventType.TYPING_BURST
        assert event.payload["burst_text"] == "def main():"

    def test_flow_event_with_flow_modality(self):
        event = ContextEvent(
            modality=Modality.FLOW,
            event_type=EventType.CLICK,
            payload={"x": 100, "y": 200, "target_element": "Submit"},
        )
        assert event.modality == Modality.FLOW
        assert event.event_type == EventType.CLICK

    def test_modality_roundtrip_keys(self):
        event = ContextEvent(modality=Modality.KEYS, event_type=EventType.KEYSTROKE)
        row = event.to_row()
        assert row["modality"] == "keys"
        restored = ContextEvent.from_row(row)
        assert restored.modality == Modality.KEYS
