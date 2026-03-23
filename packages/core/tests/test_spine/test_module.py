"""Tests for ModalityModule ABC contract."""

import pytest

from contextpulse_core.spine.events import ContextEvent, EventType, Modality
from contextpulse_core.spine.module import ModalityModule


class TestModalityModuleABC:
    """Test that ModalityModule enforces the abstract interface."""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ModalityModule()

    def test_partial_implementation_raises(self):
        class PartialModule(ModalityModule):
            def get_modality(self):
                return Modality.SYSTEM

        with pytest.raises(TypeError):
            PartialModule()

    def test_full_implementation_works(self):
        class DummyModule(ModalityModule):
            def __init__(self):
                self._callback = None
                self._running = False
                self._count = 0

            def get_modality(self):
                return Modality.SYSTEM

            def register(self, event_callback):
                self._callback = event_callback

            def start(self):
                self._running = True

            def stop(self):
                self._running = False

            def is_alive(self):
                return self._running

            def get_status(self):
                return {
                    "modality": self.get_modality().value,
                    "running": self._running,
                    "events_emitted": self._count,
                    "last_event_timestamp": None,
                    "error": None,
                }

            def get_config_schema(self):
                return {}

        module = DummyModule()
        assert module.get_modality() == Modality.SYSTEM
        assert module.is_alive() is False

        module.start()
        assert module.is_alive() is True

        module.stop()
        assert module.is_alive() is False

        status = module.get_status()
        assert status["modality"] == "system"
        assert status["running"] is False
        assert status["events_emitted"] == 0

    def test_register_and_emit(self):
        class EmittingModule(ModalityModule):
            def __init__(self):
                self._callback = None
                self._running = False

            def get_modality(self):
                return Modality.VOICE

            def register(self, event_callback):
                self._callback = event_callback

            def start(self):
                self._running = True

            def stop(self):
                self._running = False

            def is_alive(self):
                return self._running

            def get_status(self):
                return {"modality": "voice", "running": self._running,
                        "events_emitted": 0, "last_event_timestamp": None,
                        "error": None}

            def get_config_schema(self):
                return {}

            def emit_test_event(self):
                if self._callback:
                    event = ContextEvent(
                        modality=Modality.VOICE,
                        event_type=EventType.TRANSCRIPTION,
                        payload={"transcript": "hello"},
                    )
                    self._callback(event)

        received = []
        module = EmittingModule()
        module.register(lambda e: received.append(e))
        module.emit_test_event()
        assert len(received) == 1
        assert received[0].modality == Modality.VOICE

    def test_get_config_schema_required(self):
        """get_config_schema is required per spec section 7."""
        class NoConfigSchema(ModalityModule):
            def get_modality(self): return Modality.SYSTEM
            def register(self, cb): pass
            def start(self): pass
            def stop(self): pass
            def is_alive(self): return False
            def get_status(self): return {}
            # get_config_schema intentionally omitted

        with pytest.raises(TypeError):
            NoConfigSchema()

    def test_get_config_schema_returns_dict(self):
        class FullModule(ModalityModule):
            def get_modality(self): return Modality.SIGHT
            def register(self, cb): pass
            def start(self): pass
            def stop(self): pass
            def is_alive(self): return False
            def get_status(self):
                return {"modality": "sight", "running": False,
                        "events_emitted": 0, "last_event_timestamp": None,
                        "error": None}
            def get_config_schema(self):
                return {"capture_interval_seconds": {"type": "number", "default": 5.0}}

        module = FullModule()
        schema = module.get_config_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0
