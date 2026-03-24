"""Tests for VoiceModule — ModalityModule lifecycle and event emission."""

import time
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_core.spine import ContextEvent, EventType, Modality


class TestVoiceModuleLifecycle:
    @pytest.fixture
    def module(self):
        with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
            mock_cfg.return_value = {
                "hotkey": "ctrl+space",
                "fix_hotkey": "ctrl+shift+space",
                "whisper_model": "base",
                "always_use_llm": False,
                "anthropic_api_key": "",
            }
            from contextpulse_voice.voice_module import VoiceModule
            m = VoiceModule(model_size="base")
            yield m

    def test_get_modality(self, module):
        assert module.get_modality() == Modality.VOICE

    def test_initial_state(self, module):
        assert not module.is_alive()
        status = module.get_status()
        assert status["running"] is False
        assert status["events_emitted"] == 0
        assert status["error"] is None

    def test_register_callback(self, module):
        cb = MagicMock()
        module.register(cb)
        assert module._callback is cb

    def test_stop_without_start(self, module):
        # Should not raise
        module.stop()
        assert not module.is_alive()

    def test_get_config_schema(self, module):
        schema = module.get_config_schema()
        assert "voice_hotkey" in schema
        assert "voice_whisper_model" in schema
        assert schema["voice_hotkey"]["type"] == "string"

    def test_emit_increments_counter(self, module):
        received = []
        module.register(lambda e: received.append(e))
        module._running = True
        event = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.SPEECH_START,
        )
        module._emit(event)
        assert module._events_emitted == 1
        assert len(received) == 1

    def test_emit_without_callback(self, module):
        module._running = True
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        # Should not raise
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_when_not_running(self, module):
        module.register(MagicMock())
        module._running = False
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        module._emit(event)
        assert module._events_emitted == 0

    def test_emit_callback_error_captured(self, module):
        def bad_callback(event):
            raise ValueError("test error")
        module.register(bad_callback)
        module._running = True
        event = ContextEvent(modality=Modality.VOICE, event_type=EventType.SPEECH_START)
        module._emit(event)
        assert module._error == "test error"

    def test_status_after_events(self, module):
        module.register(MagicMock())
        module._running = True

        for _ in range(3):
            module._emit(ContextEvent(
                modality=Modality.VOICE,
                event_type=EventType.SPEECH_START,
            ))

        status = module.get_status()
        assert status["events_emitted"] == 3
        assert status["last_event_timestamp"] is not None


class TestVoiceModuleTranscription:
    @pytest.fixture
    def module_with_mocks(self):
        with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
            mock_cfg.return_value = {
                "hotkey": "ctrl+space",
                "fix_hotkey": "ctrl+shift+space",
                "whisper_model": "base",
                "always_use_llm": False,
                "anthropic_api_key": "",
            }
            from contextpulse_voice.voice_module import VoiceModule
            m = VoiceModule(model_size="base")

            # Mock transcriber
            m._transcriber = MagicMock()
            m._transcriber.transcribe.return_value = "hello world"

            received = []
            m.register(lambda e: received.append(e))
            m._running = True

            yield m, received

    def test_transcribe_and_paste_emits_event(self, module_with_mocks):
        module, received = module_with_mocks
        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")

        # Should have emitted a TRANSCRIPTION event
        transcription_events = [
            e for e in received if e.event_type == EventType.TRANSCRIPTION
        ]
        assert len(transcription_events) == 1
        evt = transcription_events[0]
        assert evt.modality == Modality.VOICE
        assert "transcript" in evt.payload
        assert "raw_transcript" in evt.payload
        assert "paste_text_hash" in evt.payload

    def test_empty_transcription_skipped(self, module_with_mocks):
        module, received = module_with_mocks
        module._transcriber.transcribe.return_value = ""
        module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")
        assert len(received) == 0

    def test_short_transcription_skipped(self, module_with_mocks):
        module, received = module_with_mocks
        module._transcriber.transcribe.return_value = "a"
        module._transcribe_and_paste(b"fake_wav", "code.exe", "test.py")
        assert len(received) == 0
