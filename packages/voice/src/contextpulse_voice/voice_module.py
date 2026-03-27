"""VoiceModule — ModalityModule implementation for voice capture and transcription.

Wraps the recorder, transcriber, cleanup, vocabulary, and paster pipeline
to emit ContextEvents through the EventBus. Replaces Voiceasy's app.py
with a spine-compatible lifecycle.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any, Callable

from pynput import keyboard as kb

from contextpulse_core.spine import (
    ContextEvent,
    EventType,
    Modality,
    ModalityModule,
)

from contextpulse_voice.cleanup import clean
from contextpulse_voice.config import get_voice_config, has_api_key
from contextpulse_voice.paster import paste_text
from contextpulse_voice.recorder import Recorder
from contextpulse_voice.vocabulary import apply_punctuation, apply_vocabulary

logger = logging.getLogger(__name__)

# Parse hotkey string into pynput keys
_HOTKEY_MAP = {
    "ctrl": kb.Key.ctrl_l,
    "shift": kb.Key.shift_l,
    "alt": kb.Key.alt_l,
    "space": kb.Key.space,
}


def _parse_hotkey(hotkey_str: str) -> set:
    """Parse 'ctrl+space' into a set of pynput keys."""
    keys = set()
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if part in _HOTKEY_MAP:
            keys.add(_HOTKEY_MAP[part])
        else:
            keys.add(kb.KeyCode.from_char(part))
    return keys


class VoiceModule(ModalityModule):
    """Voice capture and transcription module for ContextPulse.

    Usage:
        module = VoiceModule()
        module.register(event_bus.emit)
        module.start()
        # ... module runs in background, emitting events on hotkey press/release
        module.stop()
    """

    def __init__(self, model_size: str | None = None) -> None:
        self._callback: Callable[[ContextEvent], None] | None = None
        self._running = False
        self._events_emitted = 0
        self._last_timestamp: float | None = None
        self._error: str | None = None

        # Lazy init — transcriber is heavy (loads model)
        self._recorder: Recorder | None = None
        self._transcriber = None
        self._listener: kb.Listener | None = None
        self._overlay = None  # Recording overlay (lazy init)

        self._recording = False
        self._fixing = False
        self._pressed_keys: set = set()
        self._last_wav_bytes: bytes | None = None

        # Config
        cfg = get_voice_config()
        self._model_size = model_size or cfg["whisper_model"]
        self._hotkey_keys = _parse_hotkey(cfg["hotkey"])
        self._fix_hotkey_keys = _parse_hotkey(cfg["fix_hotkey"])
        self._always_use_llm = cfg["always_use_llm"]

    def get_modality(self) -> Modality:
        return Modality.VOICE

    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        self._callback = event_callback

    def start(self) -> None:
        """Initialize recorder and transcriber, start hotkey listener."""
        if self._running:
            return

        self._recorder = Recorder()

        # Lazy-load transcriber (downloads model on first use)
        from contextpulse_voice.transcriber import LocalTranscriber
        self._transcriber = LocalTranscriber(model_size=self._model_size)

        # Initialize recording overlay (visual feedback)
        try:
            from contextpulse_voice.overlay import RecordingOverlay
            self._overlay = RecordingOverlay()
            logger.info("Recording overlay initialized")
        except Exception:
            logger.debug("Overlay failed to initialize — running headless")
            self._overlay = None

        self._running = True
        self._error = None

        # Start keyboard listener in a thread
        self._listener = kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

        logger.info("VoiceModule started (model=%s)", self._model_size)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        logger.info("VoiceModule stopped")

    def is_alive(self) -> bool:
        if not self._running:
            return False
        # Check actual listener thread liveness — the _running flag can stay
        # True even after the pynput listener thread dies from an unhandled
        # exception (e.g. PortAudioError when audio device disappears).
        if self._listener is not None and not self._listener.is_alive():
            logger.warning("Voice keyboard listener thread died — marking as not alive")
            self._running = False
            self._error = "Keyboard listener thread died unexpectedly"
            return False
        return True

    def get_status(self) -> dict[str, Any]:
        return {
            "modality": "voice",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_timestamp,
            "error": self._error,
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "voice_hotkey": {
                "type": "string", "default": "ctrl+space",
                "description": "Hold to dictate",
            },
            "voice_fix_hotkey": {
                "type": "string", "default": "ctrl+shift+space",
                "description": "Re-transcribe last dictation with higher quality",
            },
            "voice_whisper_model": {
                "type": "string", "default": "base",
                "description": "Whisper model size (base/small/medium/large)",
            },
            "voice_always_use_llm": {
                "type": "boolean", "default": False,
                "description": "Always use LLM for text cleanup",
            },
        }

    def _emit(self, event: ContextEvent) -> None:
        """Emit event via registered callback. Swallows errors."""
        if not self._callback or not self._running:
            return
        try:
            self._callback(event)
            self._events_emitted += 1
            self._last_timestamp = event.timestamp
        except Exception as exc:
            self._error = str(exc)
            logger.exception("VoiceModule emit error: %s", exc)

    def _get_foreground_info(self) -> tuple[str, str]:
        """Get current foreground app and window title."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            # Get process name
            import ctypes.wintypes
            pid = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
            if handle:
                buf2 = ctypes.create_unicode_buffer(260)
                size = ctypes.wintypes.DWORD(260)
                ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf2, ctypes.byref(size))
                ctypes.windll.kernel32.CloseHandle(handle)
                app_name = os.path.basename(buf2.value)
            else:
                app_name = ""
            return (app_name, title)
        except Exception:
            return ("", "")

    # ── Hotkey Handling ──────────────────────────────────────────────

    def _on_press(self, key: kb.Key | kb.KeyCode | None) -> None:
        try:
            self._on_press_inner(key)
        except Exception:
            # Catch ALL exceptions to prevent pynput listener thread from dying.
            # Common culprits: PortAudioError when audio device disconnects,
            # OSError on transient system issues. Log and continue.
            logger.exception("Error in voice _on_press handler (swallowed to keep listener alive)")

    def _on_press_inner(self, key: kb.Key | kb.KeyCode | None) -> None:
        self._pressed_keys.add(key)
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self._pressed_keys.add(kb.Key.ctrl_l)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self._pressed_keys.add(kb.Key.shift_l)
        if key in (kb.Key.alt_l, kb.Key.alt_r):
            self._pressed_keys.add(kb.Key.alt_l)

        # Fix-last hotkey (press, not hold)
        if (
            self._fix_hotkey_keys.issubset(self._pressed_keys)
            and not self._recording
            and not self._fixing
        ):
            self._fixing = True
            threading.Thread(target=self._fix_last, daemon=True).start()
            return

        # Main dictation hotkey (hold)
        if (
            self._hotkey_keys.issubset(self._pressed_keys)
            and not self._fix_hotkey_keys.issubset(self._pressed_keys)
            and not self._recording
        ):
            self._recording = True
            app_name, window_title = self._get_foreground_info()
            self._recorder.start()
            if self._overlay:
                self._overlay.show_recording()
            self._emit(ContextEvent(
                modality=Modality.VOICE,
                event_type=EventType.SPEECH_START,
                app_name=app_name,
                window_title=window_title,
            ))
            logger.info("Recording...")

    def _on_release(self, key: kb.Key | kb.KeyCode | None) -> None:
        try:
            self._on_release_inner(key)
        except Exception:
            logger.exception("Error in voice _on_release handler (swallowed to keep listener alive)")

    def _on_release_inner(self, key: kb.Key | kb.KeyCode | None) -> None:
        self._pressed_keys.discard(key)
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self._pressed_keys.discard(kb.Key.ctrl_l)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self._pressed_keys.discard(kb.Key.shift_l)
        if key in (kb.Key.alt_l, kb.Key.alt_r):
            self._pressed_keys.discard(kb.Key.alt_l)

        if self._fixing and not self._fix_hotkey_keys.issubset(self._pressed_keys):
            self._fixing = False

        if self._recording and not self._hotkey_keys.issubset(self._pressed_keys):
            self._recording = False
            wav_bytes = self._recorder.stop()
            if self._overlay:
                self._overlay.show_transcribing()
            app_name, window_title = self._get_foreground_info()
            self._emit(ContextEvent(
                modality=Modality.VOICE,
                event_type=EventType.SPEECH_END,
                app_name=app_name,
                window_title=window_title,
            ))

            if not wav_bytes:
                logger.warning("No audio captured")
                if self._overlay:
                    self._overlay.hide()
                return

            threading.Thread(
                target=self._transcribe_and_paste,
                args=(wav_bytes, app_name, window_title),
                daemon=True,
            ).start()

    # ── Transcription Pipeline ───────────────────────────────────────

    def _transcribe_and_paste(
        self, wav_bytes: bytes, app_name: str, window_title: str
    ) -> None:
        """Run full pipeline: transcribe → cleanup → vocabulary → paste → emit."""
        try:
            raw_text = self._transcriber.transcribe(wav_bytes)
            if not raw_text or len(raw_text.strip()) < 2:
                logger.warning("Empty or too-short transcription — skipping")
                if self._overlay:
                    self._overlay.hide()
                return

            raw_text = apply_punctuation(raw_text)
            use_llm = self._always_use_llm and has_api_key()
            if use_llm and self._overlay:
                self._overlay.show_cleaning()
            text = clean(raw_text, use_llm=use_llm)
            text = apply_vocabulary(text)

            if not text:
                logger.warning("Empty transcription — silence?")
                if self._overlay:
                    self._overlay.hide()
                return

            self._last_wav_bytes = wav_bytes
            paste_timestamp, paste_hash = paste_text(text)
            if self._overlay:
                self._overlay.show_ready()

            # Emit TRANSCRIPTION event with both raw and cleaned text
            self._emit(ContextEvent(
                modality=Modality.VOICE,
                event_type=EventType.TRANSCRIPTION,
                app_name=app_name,
                window_title=window_title,
                payload={
                    "transcript": text,
                    "raw_transcript": raw_text,
                    "confidence": 0.85,  # TODO: get from Whisper segments
                    "language": "en",
                    "duration_seconds": len(wav_bytes) / (16000 * 2),
                    "cleanup_applied": use_llm,
                    "paste_text_hash": paste_hash,
                    "paste_timestamp": paste_timestamp,
                },
            ))
            logger.info("Dictated: %s", text[:100])
        except Exception:
            self._error = "Transcription failed"
            logger.exception("Transcription failed")
            if self._overlay:
                self._overlay.hide()

    def _fix_last(self) -> None:
        """Re-transcribe last audio with higher quality and LLM cleanup."""
        if self._last_wav_bytes is None:
            logger.warning("Fix-last: no previous dictation to fix")
            return

        try:
            logger.info("Fix-last: re-transcribing with beam_size=10 + LLM cleanup")
            import pyautogui as pag

            raw_text = self._transcriber.transcribe(self._last_wav_bytes, beam_size=10)
            raw_text = apply_punctuation(raw_text)
            text = clean(raw_text, use_llm=True)
            text = apply_vocabulary(text)

            if text:
                time.sleep(0.15)
                pag.hotkey("ctrl", "a")
                time.sleep(0.05)
                paste_timestamp, paste_hash = paste_text(text)

                app_name, window_title = self._get_foreground_info()
                self._emit(ContextEvent(
                    modality=Modality.VOICE,
                    event_type=EventType.TRANSCRIPTION,
                    app_name=app_name,
                    window_title=window_title,
                    payload={
                        "transcript": text,
                        "raw_transcript": raw_text,
                        "confidence": 0.95,
                        "language": "en",
                        "duration_seconds": len(self._last_wav_bytes) / (16000 * 2),
                        "cleanup_applied": True,
                        "paste_text_hash": paste_hash,
                        "paste_timestamp": paste_timestamp,
                        "fix_last": True,
                    },
                ))
                logger.info("Fix-last replaced: %s", text[:100])
        except Exception:
            self._error = "Fix-last failed"
            logger.exception("Fix-last failed")
