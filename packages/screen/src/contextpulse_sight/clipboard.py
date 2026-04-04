# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Clipboard context capture — monitors clipboard for text content.

Captures clipboard text (error messages, URLs, stack traces, code snippets)
alongside screenshots. Stores in the activity database for searchable history.

Filters noise: ignores rapid copy-paste loops, very short clips (<5 chars),
and duplicate consecutive content.
"""

import hashlib
import logging
import threading
import time

from contextpulse_core.platform import get_platform_provider

from contextpulse_sight.activity import ActivityDB

logger = logging.getLogger("contextpulse.sight.clipboard")

# Minimum interval between captures (seconds) — debounce rapid copy-paste
_MIN_INTERVAL = 1.0
# Minimum text length to capture
_MIN_LENGTH = 5
# Maximum text length to store (truncate very large pastes)
_MAX_LENGTH = 10_000


class ClipboardMonitor:
    """Monitors the Windows clipboard for text changes.

    Uses a polling approach (checks every 1s) rather than WM_CLIPBOARDUPDATE
    messages, to avoid needing a hidden window and message loop.
    """

    def __init__(self, activity_db: ActivityDB):
        self._activity_db = activity_db
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._last_text: str = ""
        self._last_capture_time: float = 0.0
        self._sequence_number: int = 0
        self._sight_module = None  # optional dual-write to EventBus

    def set_sight_module(self, module) -> None:
        """Attach a SightModule for dual-write EventBus emission."""
        self._sight_module = module

    def start(self):
        """Start the clipboard monitoring thread."""
        self._thread.start()
        logger.info("Clipboard monitor started")

    def stop(self):
        """Stop the clipboard monitoring thread."""
        self._stop.set()

    def _poll_loop(self):
        """Poll clipboard for text changes."""
        while not self._stop.wait(1.0):
            try:
                self._check_clipboard()
            except Exception:
                logger.debug("Clipboard poll error", exc_info=True)

    def _check_clipboard(self):
        """Check if clipboard text has changed and record it."""
        # Check sequence number first (cheap) to avoid reading clipboard unnecessarily
        seq = _get_clipboard_sequence()
        if seq == self._sequence_number:
            return
        self._sequence_number = seq

        text = _get_clipboard_text()
        if not text:
            return

        # Debounce: skip if too soon after last capture
        now = time.time()
        if now - self._last_capture_time < _MIN_INTERVAL:
            return

        # Filter noise
        text = text.strip()
        if len(text) < _MIN_LENGTH:
            return

        # Skip duplicate consecutive content
        if text == self._last_text:
            return

        # Truncate very large pastes
        if len(text) > _MAX_LENGTH:
            text = text[:_MAX_LENGTH] + f"\n[... truncated at {_MAX_LENGTH} chars]"

        self._last_text = text
        self._last_capture_time = now

        # Store in activity DB
        self._activity_db.record_clipboard(
            timestamp=now,
            text=text,
        )
        # Dual-write: emit clipboard event to EventBus
        if self._sight_module:
            hash_val = hashlib.sha256(text.encode()).hexdigest()[:16]
            self._sight_module.emit_clipboard(
                timestamp=now,
                text=text,
                hash_val=hash_val,
            )
        logger.debug("Clipboard captured: %d chars", len(text))

    def get_recent(self, count: int = 10) -> list[dict]:
        """Get recent clipboard entries."""
        return self._activity_db.get_clipboard_history(count)


def _get_clipboard_sequence() -> int:
    """Get the clipboard sequence number (changes on every clipboard update)."""
    try:
        return get_platform_provider().get_clipboard_sequence()
    except Exception:
        return 0


def _get_clipboard_text() -> str | None:
    """Read text from the clipboard via platform provider."""
    try:
        return get_platform_provider().get_clipboard_text()
    except Exception:
        return None
