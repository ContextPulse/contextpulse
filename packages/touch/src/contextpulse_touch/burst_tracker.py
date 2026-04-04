# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""BurstTracker — aggregates individual key presses into privacy-safe typing bursts.

Privacy model:
- Normal mode: only counts chars/words, never stores content
- Watch mode (during correction window): temporarily stores chars in memory
  for correction extraction, then discards after extraction
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class BurstTracker:
    """Aggregates keystrokes into typing bursts with privacy-safe metrics."""

    def __init__(
        self,
        burst_timeout: float = 1.5,
        min_chars: int = 3,
        on_burst: callable = None,
    ) -> None:
        self._burst_timeout = burst_timeout
        self._min_chars = min_chars
        self._on_burst = on_burst

        # Current burst state
        self._char_count = 0
        self._backspace_count = 0
        self._burst_start: float | None = None
        self._last_key_time: float = 0.0
        self._has_selection = False

        # Watch mode (temporary content capture for correction detection)
        self._watch_mode = False
        self._watch_chars: list[str] = []

        # Timer for burst timeout
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_key_press(self, key_char: str | None, is_backspace: bool = False,
                     is_selection: bool = False) -> None:
        """Process a key press event.

        Args:
            key_char: The character typed, or None for non-printable keys.
            is_backspace: True if Backspace/Delete was pressed.
            is_selection: True if Shift+arrow or similar selection key combo.
        """
        now = time.time()

        with self._lock:
            # Check if this is a new burst (timeout elapsed since last key)
            if self._burst_start is not None and (now - self._last_key_time) > self._burst_timeout:
                self._flush_burst()

            # Start new burst if needed
            if self._burst_start is None:
                self._burst_start = now

            self._last_key_time = now

            if is_backspace:
                self._backspace_count += 1
                if self._watch_mode and self._watch_chars:
                    self._watch_chars.pop()
            elif is_selection:
                self._has_selection = True
            elif key_char is not None:
                self._char_count += 1
                if self._watch_mode:
                    self._watch_chars.append(key_char)

            # Reset/restart the timeout timer
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._burst_timeout, self._timeout_flush)
            self._timer.daemon = True
            self._timer.start()

    def _timeout_flush(self) -> None:
        """Called by timer when burst timeout elapses."""
        with self._lock:
            self._flush_burst()

    def _flush_burst(self) -> None:
        """Emit the current burst and reset state. Must be called with lock held."""
        if self._burst_start is None or self._char_count < self._min_chars:
            self._reset_burst()
            return

        now = time.time()
        duration_ms = int((now - self._burst_start) * 1000)
        duration_s = max(duration_ms / 1000, 0.001)

        # Estimate words (rough: avg 5 chars per word)
        word_count = max(1, self._char_count // 5)
        wpm = int((word_count / duration_s) * 60) if duration_s > 0 else 0

        burst_data = {
            "char_count": self._char_count,
            "word_count": word_count,
            "duration_ms": duration_ms,
            "wpm": wpm,
            "backspace_count": self._backspace_count,
            "has_selection": self._has_selection,
        }

        if self._on_burst:
            try:
                self._on_burst(burst_data)
            except Exception:
                logger.exception("Burst callback error")

        self._reset_burst()

    def _reset_burst(self) -> None:
        """Reset burst state. Must be called with lock held."""
        self._char_count = 0
        self._backspace_count = 0
        self._burst_start = None
        self._has_selection = False
        # Note: watch_chars are NOT reset here — they persist across bursts
        # during a correction window

    def enter_watch_mode(self) -> None:
        """Enter watch mode — temporarily capture actual characters in memory.

        Called by CorrectionDetector when a Voice paste is detected.
        Characters are held in memory only, never persisted.
        """
        with self._lock:
            self._watch_mode = True
            self._watch_chars = []
            logger.debug("BurstTracker: entered watch mode")

    def exit_watch_mode(self) -> str:
        """Exit watch mode and return collected text, then clear it.

        Returns the characters typed during the correction window.
        After this call, no content is retained in memory.
        """
        with self._lock:
            text = "".join(self._watch_chars)
            self._watch_mode = False
            self._watch_chars = []
            logger.debug("BurstTracker: exited watch mode (%d chars)", len(text))
            return text

    @property
    def is_watching(self) -> bool:
        """True if currently in watch mode (capturing content)."""
        return self._watch_mode

    def get_watch_text(self) -> str:
        """Peek at current watch text without exiting watch mode."""
        with self._lock:
            return "".join(self._watch_chars)

    def stop(self) -> None:
        """Cancel any pending timer and flush."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._flush_burst()
            self._watch_mode = False
            self._watch_chars = []
