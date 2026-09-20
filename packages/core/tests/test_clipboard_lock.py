# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""cp-daemon-heap-corruption-after-paste — the read side of the invariant.

The daemon exited 0xC0000374 STATUS_HEAP_CORRUPTION twice (2026-09-05 and
2026-09-19), both times within one second of a dictation paste, with no
Python traceback: RtlpHeapHandleError raises it through ``__fastfail``, so
nothing is ever delivered to Python and no ``finally`` runs.

Two independent defects can produce that, and these tests hold both shut:

1. The voice paster's ``pyperclip.copy()`` calls EmptyClipboard, which FREES
   every handle on the clipboard, while the sight poller may be holding a
   GlobalLock'd pointer into one of them. Read-after-free plus
   GlobalUnlock-after-free is a heap-metadata write on a dead block.
2. ``ctypes.wstring_at(ptr)`` with no length scans for a NUL terminator with
   no upper bound. Clipboard text written by another application is not
   guaranteed to carry one.

Windows-only: the module under test binds ``ctypes.windll`` at import.
"""

import ctypes
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32 clipboard read path is Windows-only"
)


def _unterminated_block(payload: str, trailing: int = 59):
    """A buffer whose first ``len(payload)`` chars are the 'real' contents.

    Everything after them is non-NUL, so an UNBOUNDED read walks straight
    past the end of the logical block. A single NUL is placed at the very
    end of the allocation so the pre-patch behaviour is deterministic and
    stays inside memory this test owns — the point is to prove the read
    stops at GlobalSize, not to actually run off a heap block in a test.
    """
    filler = "X" * trailing
    return ctypes.create_unicode_buffer(payload + filler, len(payload) + trailing + 1)


def _install_fake_clipboard(monkeypatch, win, buf, size_bytes, hooks=None):
    """Point the module's Win32 bindings at an in-process fake."""
    hooks = hooks or {}

    monkeypatch.setattr(win._u32, "OpenClipboard", hooks.get("open", lambda _h: True))
    monkeypatch.setattr(win._u32, "CloseClipboard", hooks.get("close", lambda: True))
    monkeypatch.setattr(win._u32, "IsClipboardFormatAvailable", lambda _f: True)
    monkeypatch.setattr(win._u32, "GetClipboardData", lambda _f: 0xDEADBEEF)
    monkeypatch.setattr(win._k32, "GlobalSize", lambda _h: size_bytes)
    monkeypatch.setattr(
        win._k32, "GlobalLock", hooks.get("lock", lambda _h: ctypes.addressof(buf))
    )
    monkeypatch.setattr(win._k32, "GlobalUnlock", hooks.get("unlock", lambda _h: True))


class TestReadIsBounded:
    def test_read_stops_at_globalsize(self, monkeypatch):
        """An unterminated block must yield exactly its own contents.

        Pre-patch this returns ``"ABCD" + "X" * 59`` — the read walked 59
        characters past the end of the block GlobalSize describes.
        """
        from contextpulse_core.platform import windows as win

        payload = "ABCD"
        buf = _unterminated_block(payload)
        size_bytes = len(payload) * ctypes.sizeof(ctypes.c_wchar)
        _install_fake_clipboard(monkeypatch, win, buf, size_bytes)

        assert win.WindowsPlatformProvider().get_clipboard_text() == payload

    def test_trailing_nul_padding_is_trimmed(self, monkeypatch):
        """A normal, NUL-terminated payload reads back exactly as before.

        GlobalSize on a real CF_UNICODETEXT block includes the terminator (and
        the allocator may round the block up), so the bounded read has to trim
        rather than hand back embedded NULs.
        """
        from contextpulse_core.platform import windows as win

        payload = "hello world"
        buf = ctypes.create_unicode_buffer(payload, len(payload) + 5)
        size_bytes = ctypes.sizeof(buf)
        _install_fake_clipboard(monkeypatch, win, buf, size_bytes)

        assert win.WindowsPlatformProvider().get_clipboard_text() == payload

    def test_an_enormous_block_is_capped(self, monkeypatch, caplog):
        """GlobalSize bounds the read for SAFETY; _READ_CAP_CHARS for SIZE.

        A 200MB text copy would otherwise allocate 400MB+ in the daemon and
        hold both the clipboard lock and the Win32 clipboard open long enough
        for the paster's acquire to time out, dropping a dictation — a read
        that is memory-safe and still takes the feature down. Sight truncates
        to 10,000 chars immediately afterwards anyway.
        """
        import logging

        from contextpulse_core.platform import windows as win

        # Real memory for what the cap allows, and a GlobalSize that lies far
        # beyond it — the cap is what keeps the read inside the buffer.
        buf = ctypes.create_unicode_buffer("A" * win._READ_CAP_CHARS, win._READ_CAP_CHARS + 1)
        _install_fake_clipboard(monkeypatch, win, buf, 200 * 1024 * 1024)
        win.WindowsPlatformProvider._warned_errors.discard("clipboard_read_capped")

        with caplog.at_level(logging.WARNING, logger="contextpulse.platform.windows"):
            got = win.WindowsPlatformProvider().get_clipboard_text()
            # Second read: the warning must not repeat once a second forever.
            win.WindowsPlatformProvider().get_clipboard_text()

        assert len(got) == win._READ_CAP_CHARS
        capped = [r for r in caplog.records if "reading the first" in r.getMessage()]
        assert len(capped) == 1, "the cap must be logged once, not per poll"

    def test_zero_sized_block_reads_nothing(self, monkeypatch):
        """GlobalSize of 0 means there is nothing safe to read."""
        from contextpulse_core.platform import windows as win

        buf = ctypes.create_unicode_buffer("unused", 16)
        _install_fake_clipboard(monkeypatch, win, buf, 0)

        assert win.WindowsPlatformProvider().get_clipboard_text() is None


class TestReadHoldsTheLock:
    def test_whole_open_read_close_window_is_exclusive(self, monkeypatch):
        """No other thread may hold the lock at ANY point inside the read.

        Holding the lock only around individual API calls would not help: the
        hazard is another thread's EmptyClipboard freeing the block between
        our GlobalLock and our GlobalUnlock. So the assertion is made from
        inside each phase of the read, not before or after it.
        """
        from contextpulse_core.clipboard_lock import clipboard_lock
        from contextpulse_core.platform import windows as win

        buf = ctypes.create_unicode_buffer("payload", 16)
        seen_free: list[str] = []

        def _probe(phase):
            """True if a DIFFERENT thread could take the lock right now."""
            got = [False]

            def attempt():
                if clipboard_lock.acquire(blocking=False):
                    got[0] = True
                    clipboard_lock.release()

            # A separate thread, because clipboard_lock is re-entrant: this
            # thread would succeed trivially.
            t = threading.Thread(target=attempt)
            t.start()
            t.join(timeout=5)
            if got[0]:
                seen_free.append(phase)

        hooks = {
            "open": lambda _h: (_probe("OpenClipboard"), True)[1],
            "lock": lambda _h: (_probe("GlobalLock"), ctypes.addressof(buf))[1],
            "unlock": lambda _h: (_probe("GlobalUnlock"), True)[1],
            "close": lambda: (_probe("CloseClipboard"), True)[1],
        }
        _install_fake_clipboard(monkeypatch, win, buf, ctypes.sizeof(buf), hooks)

        assert win.WindowsPlatformProvider().get_clipboard_text() == "payload"
        assert seen_free == [], f"lock was available during: {seen_free}"

    def test_read_is_skipped_while_a_paste_holds_the_lock(self, monkeypatch):
        """A poll that cannot get the lock must not touch the clipboard at all.

        Skipping one poll costs a single second of clipboard history. Racing
        the paster costs the daemon.
        """
        from contextpulse_core.clipboard_lock import clipboard_lock
        from contextpulse_core.platform import windows as win

        opened: list[int] = []
        buf = ctypes.create_unicode_buffer("payload", 16)
        _install_fake_clipboard(
            monkeypatch,
            win,
            buf,
            ctypes.sizeof(buf),
            {"open": lambda _h: (opened.append(1), True)[1]},
        )
        monkeypatch.setattr(win, "CLIPBOARD_LOCK_TIMEOUT", 0.05)

        held = threading.Event()
        release = threading.Event()

        def holder():
            with clipboard_lock:
                held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert held.wait(timeout=5)
            assert win.WindowsPlatformProvider().get_clipboard_text() is None
            assert opened == [], "must not open the clipboard without the lock"
        finally:
            release.set()
            t.join(timeout=5)

    def test_lock_is_released_on_the_failure_path(self, monkeypatch):
        """A read that raises must not strand the lock and wedge every paste."""
        from contextpulse_core.clipboard_lock import clipboard_lock
        from contextpulse_core.platform import windows as win

        def boom(_h):
            raise OSError("boom")

        monkeypatch.setattr(win._u32, "OpenClipboard", boom)

        assert win.WindowsPlatformProvider().get_clipboard_text() is None

        # From another thread, because the lock is re-entrant.
        got = []
        def attempt():
            if clipboard_lock.acquire(timeout=1.0):
                got.append(True)
                clipboard_lock.release()

        t = threading.Thread(target=attempt)
        t.start()
        t.join(timeout=5)
        assert got == [True], "clipboard_lock was stranded by a failed read"

    def test_lock_is_released_when_the_clipboard_holds_no_text(self, monkeypatch):
        """The quiet path returns early — it must still release the lock."""
        from contextpulse_core.clipboard_lock import clipboard_lock
        from contextpulse_core.platform import windows as win

        monkeypatch.setattr(win._u32, "OpenClipboard", lambda _h: True)
        monkeypatch.setattr(win._u32, "CloseClipboard", lambda: True)
        monkeypatch.setattr(win._u32, "IsClipboardFormatAvailable", lambda _f: False)

        assert win.WindowsPlatformProvider().get_clipboard_text() is None

        got = []
        def attempt():
            if clipboard_lock.acquire(timeout=1.0):
                got.append(True)
                clipboard_lock.release()

        t = threading.Thread(target=attempt)
        t.start()
        t.join(timeout=5)
        assert got == [True], "clipboard_lock was stranded by a no-text read"
