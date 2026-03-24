"""CorrectionDetector — detects when users correct Voice-pasted text.

The self-improving loop:
1. Detect Ctrl+V → read clipboard → hash content
2. Cross-reference with recent Voice TRANSCRIPTION events
3. Enter watch window on BurstTracker for Voice pastes
4. Track backspaces, selections, undo
5. On window expiry or window switch: extract corrections
6. Emit CORRECTION_DETECTED event
7. Write corrections to vocabulary via VoiceasyBridge
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from contextpulse_voice.config import LEARNED_VOCAB_FILE, VOICE_DATA_DIR

from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.config import CORRECTION_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)


class VoiceasyBridge:
    """Writes corrections to Voice's vocabulary_learned.json.

    File-based integration — zero coupling between packages.
    Voice already hot-reloads on file mtime change.
    """

    def __init__(self, learned_file: Path | None = None) -> None:
        self._learned_file = learned_file or LEARNED_VOCAB_FILE
        self._write_lock = threading.Lock()

    def add_correction(self, original: str, corrected: str) -> bool:
        """Add a correction to vocabulary_learned.json.

        Returns True if the correction was added (not a duplicate).
        Uses atomic write (temp file + rename) for safety.
        """
        if not original or not corrected or original.strip() == corrected.strip():
            return False

        with self._write_lock:
            try:
                self._learned_file.parent.mkdir(parents=True, exist_ok=True)

                learned: dict[str, str] = {}
                if self._learned_file.exists():
                    try:
                        learned = json.loads(self._learned_file.read_text(encoding="utf-8"))
                        if not isinstance(learned, dict):
                            learned = {}
                    except (json.JSONDecodeError, OSError):
                        learned = {}

                key = original.lower().strip()
                if key in learned:
                    return False

                user_vocab_file = self._learned_file.parent / "vocabulary.json"
                if user_vocab_file.exists():
                    try:
                        user_vocab = json.loads(user_vocab_file.read_text(encoding="utf-8"))
                        if key in user_vocab:
                            return False
                    except (json.JSONDecodeError, OSError):
                        pass

                learned[key] = corrected
                # Write directly with lock held (atomic rename fails on Windows under contention)
                self._learned_file.write_text(
                    json.dumps(learned, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                logger.info("Learned correction: %r -> %r", original, corrected)
                return True
            except Exception:
                logger.exception("Failed to write correction")
                return False

    def get_recent_corrections(self, limit: int = 20) -> list[dict]:
        """Read recent corrections from the learned vocabulary file."""
        if not self._learned_file.exists():
            return []
        try:
            data = json.loads(self._learned_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return []
            entries = [{"original": k, "corrected": v} for k, v in data.items()]
            return entries[-limit:]
        except (json.JSONDecodeError, OSError):
            return []


class CorrectionDetector:
    """Detects corrections to Voice-pasted text by monitoring keyboard activity.

    Lifecycle:
    1. Keyboard listener calls on_paste_detected() when Ctrl+V is seen
    2. Detector reads clipboard, checks if it matches recent Voice output
    3. If Voice match: enter watch window, tell BurstTracker to capture text
    4. On window expiry or window change: extract corrections
    5. Emit CORRECTION_DETECTED via callback
    """

    def __init__(
        self,
        burst_tracker: BurstTracker,
        on_correction: Callable[[dict[str, Any]], None] | None = None,
        watch_seconds: float = 15.0,
        db_path: Path | None = None,
        bridge: VoiceasyBridge | None = None,
    ) -> None:
        self._burst_tracker = burst_tracker
        self._on_correction = on_correction
        self._watch_seconds = watch_seconds
        self._bridge = bridge or VoiceasyBridge()

        # EventBus DB path for querying Voice events
        if db_path is None:
            from contextpulse_core.config import APPDATA_DIR
            self._db_path = APPDATA_DIR / "activity.db"
        else:
            self._db_path = db_path

        # Watch window state
        self._watching = False
        self._watch_start: float = 0.0
        self._original_text: str = ""
        self._original_hash: str = ""
        self._paste_event_id: str = ""
        self._backspace_count = 0
        self._has_selection = False

        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

        # Stats
        self.corrections_detected = 0
        self.pastes_detected = 0

    def on_paste_detected(self, clipboard_text: str) -> None:
        """Called when Ctrl+V is detected. Check if paste is from Voice."""
        if not clipboard_text:
            return

        self.pastes_detected += 1
        text_hash = hashlib.sha256(clipboard_text.encode()).hexdigest()[:16]

        # Check if this paste matches a recent Voice transcription
        voice_match = self._find_voice_event(text_hash)
        if not voice_match:
            return

        logger.info("Voice paste detected: %r (hash=%s)", clipboard_text[:50], text_hash)

        with self._lock:
            # Cancel any existing watch window
            if self._timer:
                self._timer.cancel()

            # Enter watch mode
            self._watching = True
            self._watch_start = time.time()
            self._original_text = clipboard_text
            self._original_hash = text_hash
            self._paste_event_id = voice_match.get("event_id", "")
            self._backspace_count = 0
            self._has_selection = False

            self._burst_tracker.enter_watch_mode()

            # Set timer for watch window expiry
            self._timer = threading.Timer(self._watch_seconds, self._end_watch_window)
            self._timer.daemon = True
            self._timer.start()

    def on_key_event(self, is_backspace: bool = False, is_selection: bool = False) -> None:
        """Track edits during watch window."""
        if not self._watching:
            return
        with self._lock:
            if is_backspace:
                self._backspace_count += 1
            if is_selection:
                self._has_selection = True

    def on_window_change(self) -> None:
        """End watch window early when user switches windows."""
        if not self._watching:
            return
        logger.debug("Window changed during correction watch — ending early")
        self._end_watch_window()

    def _find_voice_event(self, text_hash: str) -> dict | None:
        """Check if a paste hash matches a recent Voice TRANSCRIPTION event."""
        if not self._db_path.exists():
            return None

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=2)
            conn.row_factory = sqlite3.Row
            # Look for transcriptions in the last 30 seconds
            cutoff = time.time() - 30
            rows = conn.execute(
                "SELECT event_id, payload FROM events "
                "WHERE modality = 'voice' AND event_type = 'transcription' "
                "AND timestamp > ? ORDER BY timestamp DESC LIMIT 5",
                (cutoff,),
            ).fetchall()
            conn.close()

            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("paste_text_hash") == text_hash:
                    return {"event_id": row["event_id"], **payload}
        except Exception:
            logger.exception("Failed to query Voice events")
        return None

    def _end_watch_window(self) -> None:
        """Extract corrections from the watch window and emit events."""
        with self._lock:
            if not self._watching:
                return
            self._watching = False

            if self._timer:
                self._timer.cancel()
                self._timer = None

            # Get the text typed during the watch window
            typed_text = self._burst_tracker.exit_watch_mode()
            seconds_elapsed = time.time() - self._watch_start

        # Extract corrections
        corrections = self._extract_corrections(
            self._original_text, typed_text,
            self._backspace_count, self._has_selection,
        )

        for correction in corrections:
            correction["seconds_after_paste"] = round(seconds_elapsed, 1)
            correction["paste_event_id"] = self._paste_event_id

            if correction["confidence"] >= CORRECTION_CONFIDENCE_THRESHOLD:
                self.corrections_detected += 1

                # Write to Voice vocabulary
                self._bridge.add_correction(
                    correction["original_word"],
                    correction["corrected_word"],
                )

                # Emit callback
                if self._on_correction:
                    try:
                        self._on_correction(correction)
                    except Exception:
                        logger.exception("Correction callback error")

    def _extract_corrections(
        self, original: str, typed: str,
        backspace_count: int, has_selection: bool,
    ) -> list[dict[str, Any]]:
        """Compare original paste with edits to find word-level corrections.

        Returns a list of correction dicts with:
        - original_word, corrected_word, correction_type, confidence
        """
        if not typed or not original:
            return []

        typed = typed.strip()
        if not typed:
            return []

        corrections = []

        # Strategy 1: Selection + retype (user selected a word and retyped it)
        if has_selection and typed:
            # The typed text is likely the replacement for a selected word
            # Try to find which word in the original was replaced
            original_words = original.lower().split()
            typed_lower = typed.lower().strip(".,!? ")

            # If typed text is a single word or short phrase, find what it replaced
            if len(typed_lower.split()) <= 3:
                for word in original_words:
                    clean_word = word.strip(".,!? ")
                    if clean_word and clean_word != typed_lower and len(clean_word) > 1:
                        # Check if this word is phonetically plausible as a mishearing
                        similarity = self._char_overlap(clean_word, typed_lower)
                        if similarity > 0.3:
                            corrections.append({
                                "original_word": clean_word,
                                "corrected_word": typed_lower,
                                "correction_type": "select_replace",
                                "confidence": min(0.9, 0.5 + similarity),
                            })
                            break  # Take the best match

        # Strategy 2: Backspace + retype (user backspaced part of the paste and retyped)
        elif backspace_count > 0 and typed:
            # The last N chars of the original were deleted and retyped
            if backspace_count <= len(original):
                deleted_text = original[-backspace_count:].strip()
                typed_clean = typed.strip(".,!? ")

                if deleted_text and typed_clean and deleted_text.lower() != typed_clean.lower():
                    # Extract word-level correction
                    deleted_words = deleted_text.lower().split()
                    typed_words = typed_clean.lower().split()

                    if len(deleted_words) == 1 and len(typed_words) == 1:
                        corrections.append({
                            "original_word": deleted_words[0],
                            "corrected_word": typed_words[0],
                            "correction_type": "backspace_retype",
                            "confidence": 0.8,
                        })

        return corrections

    @staticmethod
    def _char_overlap(a: str, b: str) -> float:
        """Calculate character overlap ratio between two strings."""
        if not a or not b:
            return 0.0
        overlap = len(set(a.lower()) & set(b.lower()))
        return overlap / max(len(set(a.lower())), len(set(b.lower())))

    @property
    def is_watching(self) -> bool:
        return self._watching

    def stop(self) -> None:
        """Clean up timers."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if self._watching:
                self._burst_tracker.exit_watch_mode()
            self._watching = False
