# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""VoiceModule — ModalityModule implementation for voice capture and transcription.

Wraps the recorder, transcriber, cleanup, vocabulary, and paster pipeline
to emit ContextEvents through the EventBus.
with a spine-compatible lifecycle.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from contextpulse_core.config import APPDATA_DIR
from contextpulse_core.spine import (
    ContextEvent,
    EventType,
    Modality,
    ModalityModule,
)
from pynput import keyboard as kb

from contextpulse_voice.cleanup import clean
from contextpulse_voice.config import get_voice_config, has_api_key
from contextpulse_voice.paster import paste_text
from contextpulse_voice.recorder import Recorder, save_wav_bytes
from contextpulse_voice.vocabulary import apply_punctuation, apply_vocabulary

logger = logging.getLogger(__name__)

# ── Crash-safe audio persistence ─────────────────────────────────────
# Every captured WAV is written to RECORDINGS_DIR before transcribe
# runs and deleted only after a successful TRANSCRIPTION event lands.
# If the daemon dies mid-flight, the leftover WAV is recovered by
# `contextpulse_voice.orphan_recovery` (CLI: `transcribe-orphans`).
RECORDINGS_DIR: Path = APPDATA_DIR / "voice" / "recordings"
_MAX_RECORDINGS = 50
_MAX_RECORDING_AGE_DAYS = 7


def _new_recording_path(recordings_dir: Path | None = None) -> Path:
    """Build a unique WAV path: dict_<unixms>_<pid>_<short>.wav.

    Reads the module-level RECORDINGS_DIR at call time (not at function
    definition time) so test fixtures can monkeypatch it.
    """
    if recordings_dir is None:
        recordings_dir = RECORDINGS_DIR
    stamp = int(time.time() * 1000)
    short = uuid.uuid4().hex[:8]
    return recordings_dir / f"dict_{stamp}_{os.getpid()}_{short}.wav"


def _cleanup_old_recordings(
    recordings_dir: Path,
    max_files: int = _MAX_RECORDINGS,
    max_age_days: int = _MAX_RECORDING_AGE_DAYS,
) -> None:
    """Prune the recordings directory.

    Drops anything older than `max_age_days`, then keeps only the
    `max_files` newest WAVs. Best-effort: any IO error is logged at
    debug level and otherwise swallowed — pruning must never fail the
    voice pipeline.
    """
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.is_dir():
        return
    try:
        wavs = list(recordings_dir.glob("*.wav"))
    except OSError:
        logger.debug("Could not enumerate %s", recordings_dir, exc_info=True)
        return

    cutoff = time.time() - max_age_days * 86400
    survivors: list[tuple[float, Path]] = []
    for p in wavs:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                p.unlink()
                # Drop sidecar too
                sidecar = p.with_suffix(".txt")
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                logger.debug("Could not unlink old %s", p, exc_info=True)
            continue
        survivors.append((mtime, p))

    if len(survivors) <= max_files:
        return
    survivors.sort(key=lambda t: t[0])  # oldest first
    for _mtime, p in survivors[: len(survivors) - max_files]:
        try:
            p.unlink()
            sidecar = p.with_suffix(".txt")
            if sidecar.exists():
                sidecar.unlink()
        except OSError:
            logger.debug("Could not unlink overflow %s", p, exc_info=True)


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
        self._last_stop_time: float = 0.0  # debounce: prevent double stop-recording

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
        # Warm up PortAudio device so the first hotkey press isn't delayed
        # by 100-500ms of WASAPI initialization (which would happen on the
        # keyboard hook thread and prevent the recording overlay from
        # appearing until the user releases the hotkey).
        self._recorder.warm_start()

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
        # On Windows, pynput's keyboard Listener thread can report is_alive()=False
        # even while the OS-level hook is still installed and processing events.
        # Using _listener.is_alive() here causes false negatives and a permanent
        # voice=OFF tray status. _running is set True by start() and False only
        # by stop() or a genuine exception, so it is the reliable source of truth.
        return self._running

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
                "type": "string",
                "default": "ctrl+space",
                "description": "Hold to dictate",
            },
            "voice_fix_hotkey": {
                "type": "string",
                "default": "ctrl+shift+space",
                "description": "Re-transcribe last dictation with higher quality",
            },
            "voice_whisper_model": {
                "type": "string",
                "default": "base",
                "description": "Whisper model size (base/small/medium/large)",
            },
            "voice_always_use_llm": {
                "type": "boolean",
                "default": False,
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
            from contextpulse_core.platform import get_platform_provider

            platform = get_platform_provider()
            return (platform.get_foreground_process_name(), platform.get_foreground_window_title())
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
            self._emit(
                ContextEvent(
                    modality=Modality.VOICE,
                    event_type=EventType.SPEECH_START,
                    app_name=app_name,
                    window_title=window_title,
                )
            )
            logger.info("Recording...")

    def _on_release(self, key: kb.Key | kb.KeyCode | None) -> None:
        try:
            self._on_release_inner(key)
        except Exception:
            logger.exception(
                "Error in voice _on_release handler (swallowed to keep listener alive)"
            )

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
            # Debounce: on Windows, multiple pynput keyboard hooks (Voice,
            # Touch, Sight) can cause duplicate release events for the same
            # physical key release.  Guard against firing twice within 1s.
            now = time.time()
            if now - self._last_stop_time < 1.0:
                logger.warning(
                    "Duplicate stop-recording within %.2fs — ignoring",
                    now - self._last_stop_time,
                )
                self._recording = False
                return
            self._last_stop_time = now

            self._recording = False
            if self._overlay:
                self._overlay.show_transcribing()
            app_name, window_title = self._get_foreground_info()
            self._emit(
                ContextEvent(
                    modality=Modality.VOICE,
                    event_type=EventType.SPEECH_END,
                    app_name=app_name,
                    window_title=window_title,
                )
            )

            # Stop recording and transcribe in a background thread.
            # The thread adds a brief tail delay before stopping the
            # stream so trailing speech is captured — this MUST NOT
            # happen on the pynput listener thread or it blocks key
            # event processing and causes runaway recording loops.
            threading.Thread(
                target=self._stop_and_transcribe,
                args=(app_name, window_title),
                daemon=True,
            ).start()

    # ── Transcription Pipeline ───────────────────────────────────────

    _TAIL_BUFFER_MS = 700  # minimum tail before silence detection kicks in

    def _stop_and_transcribe(self, app_name: str, window_title: str) -> None:
        """Stop recorder with energy-based tail extension.

        Called in a background thread so the tail delay doesn't block
        the pynput listener.  Records until silence is detected (up to
        2s) so trailing words are never cut off.

        Persists the captured WAV to disk BEFORE invoking transcribe so
        that a daemon crash mid-transcribe leaves the audio recoverable
        via `contextpulse_voice.orphan_recovery`.
        """
        try:
            # Brief minimum delay, then let silence detection decide when to stop
            if self._TAIL_BUFFER_MS > 0:
                time.sleep(self._TAIL_BUFFER_MS / 1000)
            wav_bytes = self._recorder.stop_after_silence()

            if not wav_bytes:
                logger.warning("No audio captured")
                if self._overlay:
                    self._overlay.hide()
                return

            # _transcribe_and_paste handles WAV persistence itself.
            self._transcribe_and_paste(wav_bytes, app_name, window_title)

            # Best-effort prune. If the success path already deleted
            # this WAV, the prune is just a noop pass over the dir.
            try:
                _cleanup_old_recordings(RECORDINGS_DIR)
            except Exception:
                logger.debug("Cleanup pass failed", exc_info=True)
        except Exception:
            logger.exception("stop_and_transcribe failed")
            if self._overlay:
                self._overlay.hide()

    def _transcribe_and_paste(
        self,
        wav_bytes: bytes,
        app_name: str,
        window_title: str,
    ) -> None:
        """Run full pipeline: transcribe → cleanup → vocabulary → paste → emit.

        Persists the WAV to disk BEFORE invoking the transcriber and
        deletes it ONLY after a successful TRANSCRIPTION event is
        emitted. Any failure path (exception, empty transcript) leaves
        the WAV on disk so `contextpulse_voice.orphan_recovery` can
        salvage the audio.
        """
        # Guard: skip if this exact audio was already transcribed (duplicate
        # thread spawn from keyboard hook re-delivery on Windows). Run this
        # BEFORE persistence so duplicate spawns don't pile up WAVs.
        audio_hash = hashlib.sha256(wav_bytes).hexdigest()[:16]
        if audio_hash == getattr(self, "_last_audio_hash", None):
            logger.warning("Duplicate audio hash %s — skipping transcription", audio_hash)
            return
        self._last_audio_hash = audio_hash

        # Crash-safe persistence: write WAV BEFORE invoking transcribe so
        # a daemon crash mid-flight leaves audio recoverable.
        wav_path: Path | None = None
        try:
            wav_path = _new_recording_path()
            save_wav_bytes(wav_bytes, wav_path)
            logger.info(
                "Persisted recording (%d bytes) -> %s",
                len(wav_bytes),
                wav_path.name,
            )
        except OSError:
            logger.exception("Could not persist recording — running without crash safety")
            wav_path = None

        try:
            # Build Whisper initial_prompt from screen OCR hot-words
            whisper_prompt = ""
            try:
                from contextpulse_voice.context_vocab import get_known_proper_nouns
                from contextpulse_voice.hot_words import (
                    build_whisper_prompt,
                    extract_hot_words,
                )

                hot_words = extract_hot_words()
                whisper_prompt = build_whisper_prompt(
                    hot_words,
                    get_known_proper_nouns(),
                )
            except Exception:
                logger.debug("Hot-word extraction failed (non-fatal)", exc_info=True)

            raw_text = self._transcriber.transcribe(
                wav_bytes,
                initial_prompt=whisper_prompt,
            )
            if not raw_text or len(raw_text.strip()) < 2:
                logger.warning("Empty or too-short transcription — skipping")
                if self._overlay:
                    self._overlay.hide()
                return

            raw_text = apply_punctuation(raw_text)
            use_llm = self._always_use_llm and has_api_key()
            if use_llm and self._overlay:
                self._overlay.show_cleaning()
            profile_context = self._build_profile_context(app_name, window_title)
            text = clean(raw_text, use_llm=use_llm, profile_context=profile_context)
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
            self._emit(
                ContextEvent(
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
                )
            )
            logger.info("Dictated: %s", text[:100])

            # Crash-safe persistence: delete the on-disk WAV only after
            # the TRANSCRIPTION event has been emitted successfully.
            # Any earlier `return` path leaves the file in place so
            # orphan recovery can salvage the audio.
            if wav_path is not None:
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug(
                        "Could not delete persisted WAV %s",
                        wav_path,
                        exc_info=True,
                    )

            # Schedule background screen correction harvesting.
            # Wait a few seconds for Claude to respond, then check if
            # screen OCR contains corrected versions of what was dictated.
            def _delayed_harvest(rt: str = raw_text) -> None:
                time.sleep(8)
                self._harvest_screen_corrections(rt)

            threading.Thread(target=_delayed_harvest, daemon=True).start()
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

            # Build Whisper initial_prompt from screen OCR hot-words
            whisper_prompt = ""
            try:
                from contextpulse_voice.context_vocab import get_known_proper_nouns
                from contextpulse_voice.hot_words import (
                    build_whisper_prompt,
                    extract_hot_words,
                )

                hot_words = extract_hot_words()
                whisper_prompt = build_whisper_prompt(
                    hot_words,
                    get_known_proper_nouns(),
                )
            except Exception:
                logger.debug("Hot-word extraction failed in fix-last (non-fatal)", exc_info=True)

            raw_text = self._transcriber.transcribe(
                self._last_wav_bytes,
                beam_size=10,
                initial_prompt=whisper_prompt,
            )
            raw_text = apply_punctuation(raw_text)
            app_name_fl, window_title_fl = self._get_foreground_info()
            profile_context = self._build_profile_context(app_name_fl, window_title_fl)
            text = clean(raw_text, use_llm=True, profile_context=profile_context)
            text = apply_vocabulary(text)

            if text:
                time.sleep(0.15)
                pag.hotkey("ctrl", "a")
                time.sleep(0.05)
                paste_timestamp, paste_hash = paste_text(text)

                app_name, window_title = self._get_foreground_info()
                self._emit(
                    ContextEvent(
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
                    )
                )
                logger.info("Fix-last replaced: %s", text[:100])
        except Exception:
            self._error = "Fix-last failed"
            logger.exception("Fix-last failed")

    # ── Context-aware helpers ────────────────────────────────────────

    def _build_profile_context(self, app_name: str = "", window_title: str = "") -> str:
        """Build a context string for LLM cleanup from screen context.

        Uses recent window titles from Sight events (last 60s) to identify
        what the user was working on. The active window during dictation
        is usually 'Claude', so recent windows are more informative.
        """
        parts: list[str] = []

        # Gather recent window titles from Sight events
        try:
            import sqlite3

            from contextpulse_core.config import ACTIVITY_DB_PATH

            if ACTIVITY_DB_PATH.exists():
                conn = sqlite3.connect(str(ACTIVITY_DB_PATH), timeout=1)
                conn.row_factory = sqlite3.Row
                cutoff = time.time() - 120  # last 2 minutes
                rows = conn.execute(
                    "SELECT DISTINCT window_title FROM events "
                    "WHERE modality = 'sight' AND window_title != '' "
                    "AND timestamp > ? ORDER BY timestamp DESC LIMIT 10",
                    (cutoff,),
                ).fetchall()
                conn.close()

                titles = [r["window_title"] for r in rows if r["window_title"]]
                # Filter out generic titles
                titles = [t for t in titles if t not in ("Claude", "Search", "") and len(t) > 3]
                if titles:
                    parts.append("Recent windows: " + ", ".join(titles[:5]))
        except Exception:
            pass

        # Add known proper nouns from context vocabulary
        try:
            from contextpulse_voice.context_vocab import get_known_proper_nouns

            nouns = get_known_proper_nouns()
            if nouns:
                parts.append("Known terms: " + ", ".join(nouns[:15]))
        except Exception:
            pass

        context = ". ".join(parts)
        # Truncate to keep the LLM prompt manageable
        return context[:300] if context else ""

    def _harvest_screen_corrections(self, raw_text: str) -> None:
        """Background: check screen OCR for corrected versions of dictated terms.

        When the user dictates into Claude Code, Claude's responses often
        contain properly-capitalized versions of what was said (e.g., Claude
        writes "ContextPulse" when the user said "context pulse"). This method
        harvests those corrections from screen OCR events.

        Runs in a background thread after a short delay to allow Claude
        to respond and Sight to capture the screen.
        """
        try:
            import json as _json
            import re as _re
            import sqlite3

            from contextpulse_core.config import ACTIVITY_DB_PATH
            from contextpulse_touch.correction_detector import VocabularyBridge

            if not ACTIVITY_DB_PATH.exists():
                return

            conn = sqlite3.connect(str(ACTIVITY_DB_PATH), timeout=2)
            conn.row_factory = sqlite3.Row
            cutoff = time.time() - 180  # last 3 minutes
            rows = conn.execute(
                "SELECT payload FROM events "
                "WHERE modality = 'sight' AND event_type = 'ocr_result' "
                "AND timestamp > ? ORDER BY timestamp DESC LIMIT 5",
                (cutoff,),
            ).fetchall()
            conn.close()

            if not rows:
                return

            # Combine OCR text from recent screens
            ocr_text = ""
            for r in rows:
                payload = _json.loads(r["payload"])
                confidence = payload.get("ocr_confidence", 0)
                if confidence >= 0.75:
                    ocr_text += " " + payload.get("ocr_text", "")

            if len(ocr_text) < 20:
                return

            # Find CamelCase words in OCR text
            camel_words = set(_re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", ocr_text))
            if not camel_words:
                return

            bridge = VocabularyBridge()
            raw_lower = raw_text.lower()

            for word in camel_words:
                # Split CamelCase into space-separated phrase
                phrase = _re.sub(r"([a-z])([A-Z])", r"\1 \2", word)
                phrase = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", phrase)
                phrase_lower = phrase.lower()

                # Check if the raw dictation contains this phrase
                if phrase_lower in raw_lower and len(phrase_lower) >= 6:
                    added = bridge.add_correction(phrase_lower, word)
                    if added:
                        logger.info(
                            "Screen-learned correction: %r -> %r",
                            phrase_lower,
                            word,
                        )
        except Exception:
            logger.debug("Screen correction harvesting failed", exc_info=True)
