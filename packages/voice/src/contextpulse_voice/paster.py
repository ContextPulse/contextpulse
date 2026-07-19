# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Text injection module — pastes transcribed text into the active window.

Clipboard paste automation.
"""

import hashlib
import logging
import sys
import threading
import time

import pyautogui
import pyperclip

# Disable pyautogui's failsafe — it raises FailSafeException if mouse is at
# (0,0) during hotkey(), which can crash the dictation pipeline.
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

# Terminal emulators do NOT treat Ctrl+V as paste (there it is a literal /
# no-op); their paste chord is Ctrl+Shift+V. Dictating into a terminal — e.g. a
# Claude Code prompt — therefore silently drops the text unless we send the
# terminal chord. We detect the focused window by its Win32 class name.
_TERMINAL_WINDOW_CLASSES = frozenset({
    "ConsoleWindowClass",             # legacy conhost: cmd.exe, powershell.exe
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "PseudoConsoleWindow",            # ConPTY host
    "mintty",                         # Git Bash / MSYS2 (see note in paste_text)
    "org.wezfurlong.wezterm",         # WezTerm
    "Alacritty",                      # Alacritty
})


def _foreground_window_class() -> str | None:
    """Win32 class name of the focused window, or None (non-Windows / failure)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or None
    except Exception:  # never let focus detection crash the paste path
        logger.debug("Foreground window class lookup failed", exc_info=True)
        return None


def _focused_is_terminal() -> bool:
    """True when the focused window is a known terminal emulator."""
    return _foreground_window_class() in _TERMINAL_WINDOW_CLASSES

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

        # Content dedup: reject pasting the exact same text twice in a row.
        # Protects against duplicate transcribe threads pasting the same
        # result even when they finish more than 1s apart.
        if text_hash == _last_paste_hash and now - _last_paste_time < 10.0:
            logger.warning(
                "Duplicate paste content (hash=%s, %.1fs ago) — skipping",
                text_hash, now - _last_paste_time,
            )
            return (0.0, "")

        pyperclip.copy("")
        time.sleep(0.05)

        pyperclip.copy(text)
        time.sleep(0.15)
        # Ctrl+V pastes in normal apps but is inert in terminals, where paste is
        # Ctrl+Shift+V. (mintty/Git Bash defaults to Shift+Insert and needs the
        # user to map Ctrl+Shift+V to paste; conhost/Windows Terminal/WezTerm all
        # accept Ctrl+Shift+V out of the box.)
        if _focused_is_terminal():
            pyautogui.hotkey("ctrl", "shift", "v")
        else:
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
