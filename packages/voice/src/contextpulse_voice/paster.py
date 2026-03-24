"""Text injection module — pastes transcribed text into the active window.

Ported from Voiceasy with no functional changes.
"""

import hashlib
import logging
import threading
import time

import pyautogui
import pyperclip

logger = logging.getLogger(__name__)

_paste_lock = threading.Lock()
_last_paste_time = 0.0
_last_paste_hash = ""


def paste_text(text: str) -> tuple[float, str]:
    """Copy text to clipboard and paste into the currently focused window.

    Returns (paste_timestamp, text_hash) for correlation with Touch module.
    Uses clipboard + Ctrl+V rather than pyautogui.write() because:
    - write() is slow and doesn't handle unicode well
    - Ctrl+V works in virtually every app
    """
    global _last_paste_time, _last_paste_hash

    text = text.strip()
    if not text:
        logger.warning("Nothing to paste — empty transcription")
        return (0.0, "")

    if not _paste_lock.acquire(blocking=False):
        logger.warning("Paste already in progress — skipping duplicate")
        return (0.0, "")

    try:
        now = time.time()
        if now - _last_paste_time < 1.0:
            logger.warning("Paste too soon after last paste (%.2fs) — skipping", now - _last_paste_time)
            return (0.0, "")

        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        pyperclip.copy("")
        time.sleep(0.05)

        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        _last_paste_time = time.time()
        _last_paste_hash = text_hash
        logger.info("Pasted %d characters (hash=%s)", len(text), text_hash)

        time.sleep(0.5)
        pyperclip.copy("")

        return (_last_paste_time, text_hash)
    finally:
        _paste_lock.release()


def get_last_paste_info() -> tuple[float, str]:
    """Return (timestamp, hash) of the most recent paste. Used by Touch module."""
    return (_last_paste_time, _last_paste_hash)
