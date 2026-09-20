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
from collections.abc import Callable

import pyautogui
import pyperclip
from contextpulse_core.clipboard_lock import (
    CLIPBOARD_LOCK_TIMEOUT,
    breadcrumbs_enabled,
    clipboard_lock,
    write_breadcrumb,
)

# Disable pyautogui's failsafe — it raises FailSafeException if mouse is at
# (0,0) during hotkey(), which can crash the dictation pipeline.
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

# Paste-path breadcrumbs default ON, one os.write() per phase — they are the
# crash forensics for cp-daemon-heap-corruption-after-paste, and a defect that
# fired twice in two weeks needs the instrument running continuously to catch a
# third. daemon_stderr.log.1 ends at "Pasted 100 characters" (12:32:14) and the
# watchdog records 0xC0000374 at 12:32:19 — a five-second hole covering the
# hotkey, a 0.5s sleep and the trailing pyperclip.copy(""). These turn that
# hole into a named phase.
#
# Per-phase has to be the DEFAULT, not a mode someone switches on after the
# fact. A __fastfail abort delivers no exception, runs no finally and leaves
# only bytes already handed to the OS (see clipboard_lock.write_breadcrumb), so
# a design that buffers the phase name in memory and emits one summary line at
# the END of the paste writes NOTHING for the paste that crashed — the only
# paste whose phase anyone wants. "The current one named by its absence" says
# no more than the watchdog's own 0xC0000374 already does.
#
# The volume argument that motivated buffering was real but aimed at the wrong
# thing: 8 lines per dictation is bounded by how often a human speaks, while
# daemon_stderr.log was unbounded because nothing rotated it by size. That is
# fixed where it lives, in scripts/daemon-watchdog.ps1.
#
# CONTEXTPULSE_PASTE_BREADCRUMBS=0 silences the paste trace. It is the ONLY
# switch this module reads: the clipboard reader's per-poll trace has its own,
# CONTEXTPULSE_CLIPBOARD_READ_BREADCRUMBS (platform/windows.py), and sharing
# one variable meant that turning on the paste trace while hunting a
# reproduction also turned on ~86k lines a day of 1 Hz read tracing, into the
# same file.
_BREADCRUMBS = breadcrumbs_enabled("CONTEXTPULSE_PASTE_BREADCRUMBS")


def _phase(name: str) -> None:
    """Mark the native call now in flight. One os.write syscall, immediately.

    Crash-survivable by construction: the bytes are with the OS before the
    call they describe is made.
    """
    if not _BREADCRUMBS:
        return
    write_breadcrumb(name)


def _flush_phases(outcome: str) -> None:
    """Close the paste out with its outcome.

    Kept alongside the per-phase lines rather than replaced by them: a paste
    DROPPED on a lock timeout enters no phase at all, so this is the only line
    that ever names it. One more line on a path that already writes eight.
    """
    if not _BREADCRUMBS:
        return
    write_breadcrumb(f"paste_done outcome={outcome}")

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

# One retry, at a longer timeout, before a paste is given up on. The first
# 2s window can be lost to a single slow clipboard owner (a poll reading a
# large clip, another application holding the clipboard open); a second,
# longer wait costs the user a pause and saves the dictation.
CLIPBOARD_RETRY_TIMEOUT = 5.0

# Set by the voice module so a dropped paste reaches the user instead of only
# a log file nobody watches. Left as None everywhere else, which keeps the
# paster importable and testable with no UI at all.
_drop_notifier: Callable[[str], None] | None = None


def set_drop_notifier(notifier: Callable[[str], None] | None) -> None:
    """Register the callback that surfaces a dropped paste to the user.

    Takes the dropped text's hash. The paster deliberately does not know what
    the UI is: the voice module owns the recording overlay and wires it here.
    """
    global _drop_notifier
    _drop_notifier = notifier


def _acquire_clipboard_for_paste(text_hash: str) -> bool:
    """Take the clipboard lock, retrying once, or give up loudly.

    Dropping the paste is the right call — losing one dictation is
    recoverable, corrupting the heap takes the daemon down mid-session — but
    dropping it SILENTLY is not: the user speaks, waits, and sees nothing
    appear. The transcription itself survives (the voice module emits the
    TRANSCRIPTION event before pasting, so the text is in the DB and
    reachable over MCP); what is missing is any sign that it happened.
    """
    if clipboard_lock.acquire(timeout=CLIPBOARD_LOCK_TIMEOUT):
        return True

    logger.warning(
        "Clipboard busy after %.1fs (hash=%s) — retrying once at %.1fs",
        CLIPBOARD_LOCK_TIMEOUT,
        text_hash,
        CLIPBOARD_RETRY_TIMEOUT,
    )
    if clipboard_lock.acquire(timeout=CLIPBOARD_RETRY_TIMEOUT):
        logger.info("Clipboard lock acquired on retry (hash=%s)", text_hash)
        return True

    logger.error(
        "Could not acquire clipboard lock in %.1fs + %.1fs — dropping paste "
        "(hash=%s) rather than racing another clipboard user. The "
        "transcription is still recorded and searchable.",
        CLIPBOARD_LOCK_TIMEOUT,
        CLIPBOARD_RETRY_TIMEOUT,
        text_hash,
    )
    _flush_phases("dropped_lock_timeout")
    if _drop_notifier is not None:
        try:
            _drop_notifier(text_hash)
        except Exception:  # noqa: BLE001 — the UI must not break the paste path
            logger.debug("Drop notifier failed", exc_info=True)
    return False


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

        # Hold the process-wide clipboard lock across EVERY clipboard mutation
        # in this sequence. pyperclip.copy() calls EmptyClipboard, which frees
        # the handles currently on the clipboard; the sight module's 1s poller
        # may be holding a GlobalLock'd pointer into one of them, in this same
        # process. Freeing it under the reader is a heap-metadata write on a
        # dead block — 0xC0000374 in ntdll, raised via __fastfail, which is why
        # the crash leaves no Python traceback
        # (cp-daemon-heap-corruption-after-paste).
        #
        # Deliberately spans the hotkey and the trailing clear, not just the
        # copies: the 0.5s window before the final copy("") is when the target
        # application is reading the clipboard, and a poll landing in the
        # middle of it is the same race.
        if not _acquire_clipboard_for_paste(text_hash):
            return (0.0, "")
        try:
            _phase("paste_copy_clear_enter")
            pyperclip.copy("")
            _phase("paste_copy_clear_exit")
            time.sleep(0.05)

            _phase("paste_copy_text_enter")
            pyperclip.copy(text)
            _phase("paste_copy_text_exit")
            time.sleep(0.15)
            # macOS paste is always Cmd+V, in terminals and normal apps alike --
            # Terminal.app/iTerm2 interpret Cmd+V as paste natively, unlike Windows
            # conhost where Ctrl+V is a legacy console control character in some
            # contexts. Checked first and unconditionally: the terminal-detection
            # branch below is Windows-only (_focused_is_terminal() always returns
            # False off win32, via _foreground_window_class()'s own platform guard).
            #
            # Ctrl+V pastes in normal Windows apps but is inert in terminals, where
            # paste is Ctrl+Shift+V. (mintty/Git Bash defaults to Shift+Insert and
            # needs the user to map Ctrl+Shift+V to paste; conhost/Windows
            # Terminal/WezTerm all accept Ctrl+Shift+V out of the box.)
            _phase("paste_hotkey_enter")
            if sys.platform == "darwin":
                pyautogui.hotkey("command", "v")
            elif _focused_is_terminal():
                pyautogui.hotkey("ctrl", "shift", "v")
            else:
                pyautogui.hotkey("ctrl", "v")
            _phase("paste_hotkey_exit")
            _last_paste_time = time.time()
            _last_paste_hash = text_hash
            logger.info("Pasted %d characters (hash=%s)", len(text), text_hash)

            time.sleep(0.5)
            _phase("paste_final_clear_enter")
            pyperclip.copy("")
            _phase("paste_final_clear_exit")
        finally:
            clipboard_lock.release()
            _flush_phases("completed")

        return (_last_paste_time, text_hash)
    finally:
        _paste_lock.release()


def get_last_paste_info() -> tuple[float, str]:
    """Return (timestamp, hash) of the most recent paste. Used by Touch module."""
    return (_last_paste_time, _last_paste_hash)
