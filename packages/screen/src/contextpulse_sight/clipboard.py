# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Clipboard context capture — monitors clipboard for text content.

Captures clipboard text (error messages, URLs, stack traces, code snippets)
alongside screenshots. Stores in the activity database for searchable history.

Filters noise: ignores rapid copy-paste loops, very short clips (<5 chars),
and duplicate consecutive content.

Secret redaction is ALWAYS ON for clipboard text and has no opt-out. The
clipboard is the highest-secret channel this daemon touches -- it is where a
password manager, a `kubectl get secret`, or a copied API key lands -- and the
product advertises pre-storage redaction. Unlike OCR (gated on
`redact_ocr_text`), there is no legitimate reason to store a clipboard secret
verbatim, so no setting can turn this off.
"""

import hashlib
import logging
import threading
import time

from contextpulse_core.platform import get_platform_provider

from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.redact import redact_sensitive

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

    def is_alive(self) -> bool:
        """Return True if the clipboard polling thread is running."""
        return self._thread.is_alive()

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

        # Redact BEFORE anything else touches the text. Two reasons this has
        # to come first and not just "before the DB write":
        #   1. Before truncation -- every pattern has a minimum length
        #      (ghp_ needs 36 trailing chars, sk- needs 20), so a token the
        #      10,000-char cut splits in half no longer matches anything and
        #      its leading half would be stored verbatim.
        #   2. Before the dedupe compare -- _last_text lives for the life of
        #      the process, and holding a raw secret there defeats the point
        #      of redacting the copy that reaches disk.
        # Both persistence paths below (ActivityDB.record_clipboard and
        # SightModule.emit_clipboard, which lands in `events`/`events_fts`)
        # read this one redacted value, so neither can drift from the other.
        text = redact_sensitive(text)

        # Skip duplicate consecutive content. The dedupe key is the
        # pre-truncation value: comparing against the truncated copy meant a
        # paste over _MAX_LENGTH never matched itself (full text vs stored
        # text + "[... truncated]" marker) and was re-captured on every tick.
        if text == self._last_text:
            return
        self._last_text = text

        # Truncate very large pastes
        if len(text) > _MAX_LENGTH:
            text = text[:_MAX_LENGTH] + f"\n[... truncated at {_MAX_LENGTH} chars]"

        self._last_capture_time = now

        # Store in activity DB
        self._activity_db.record_clipboard(
            timestamp=now,
            text=text,
        )
        # Dual-write: emit clipboard event to EventBus. The hash is taken over
        # the redacted text on purpose -- hashing the raw value would leave a
        # brute-forceable digest of a short secret (a PIN, a 4-digit code)
        # sitting in a table the redaction exists to keep clean.
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
