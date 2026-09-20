# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""cp-daemon-heap-corruption-after-paste — the write side of the invariant.

``pyperclip.copy()`` calls EmptyClipboard, which FREES the handles currently
on the clipboard. The sight module's 1s poller may be holding a GlobalLock'd
pointer into one of them, in the SAME process. The two windows must never
overlap.

This is a real concurrency test, not an assertion about code shape: it drives
the actual ``paste_text()`` on one thread and the actual Win32 read path
(``WindowsPlatformProvider.get_clipboard_text``, with the Win32 bindings
pointed at an in-process fake) on another, and fails if any phase of a read
is ever observed while a clipboard mutation is in flight.

The interleaving is repeated in-test rather than via ``pytest-repeat``: that
plugin is not a dependency of this project (checked — the venv has
pytest-timeout and pytest-threadleak only), and a race test that depends on
an absent plugin to be meaningful is a test that silently proves nothing.
"""

import os
import sys
import threading
import time

import pytest

# The real sleep, captured before any test monkeypatches the module attribute.
_real_sleep = time.sleep

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the Win32 clipboard read path against the paster",
)

# How many paste/poll interleavings to drive. A single pass of a concurrency
# test proves almost nothing; each iteration is ~5ms here because the paster's
# sleeps are compressed, so a few hundred is cheap.
INTERLEAVINGS = 200


@pytest.fixture
def fast_paster(monkeypatch):
    """The real paster with its sleeps compressed and its I/O faked."""
    from contextpulse_voice import paster

    monkeypatch.setattr(paster, "_BREADCRUMBS", False, raising=False)
    monkeypatch.setattr(paster.pyautogui, "hotkey", lambda *a: None)
    monkeypatch.setattr(paster, "_focused_is_terminal", lambda: False)
    # paster.time IS the time module, so the replacement must call the sleep
    # captured at import time or it recurses into itself.
    monkeypatch.setattr(paster.time, "sleep", lambda _s: _real_sleep(0.001))
    monkeypatch.setattr(paster, "_paste_lock", threading.Lock(), raising=False)
    paster._last_paste_time = 0.0
    paster._last_paste_hash = ""
    yield paster
    paster._last_paste_time = 0.0
    paster._last_paste_hash = ""


class TestClipboardRaceIsSerialised:
    def test_no_poller_read_overlaps_a_clipboard_mutation(self, monkeypatch, fast_paster):
        """No phase of a clipboard READ may run during a clipboard MUTATION."""
        import ctypes

        from contextpulse_core.platform import windows as win

        paster = fast_paster
        mutating = threading.Event()
        overlaps: list[str] = []
        reads = [0]

        def fake_copy(_text=""):
            # Stands in for EmptyClipboard + SetClipboardData: the window in
            # which the previous handle is freed and a new one installed.
            mutating.set()
            _real_sleep(0.002)
            mutating.clear()

        monkeypatch.setattr(paster.pyperclip, "copy", fake_copy)

        buf = ctypes.create_unicode_buffer("clipboard payload", 32)

        def guard(phase, result):
            if mutating.is_set():
                overlaps.append(phase)
            return result

        monkeypatch.setattr(win._u32, "OpenClipboard", lambda _h: guard("OpenClipboard", True))
        monkeypatch.setattr(win._u32, "IsClipboardFormatAvailable", lambda _f: True)
        monkeypatch.setattr(win._u32, "GetClipboardData", lambda _f: guard("GetClipboardData", 0xDEADBEEF))
        monkeypatch.setattr(win._k32, "GlobalSize", lambda _h: ctypes.sizeof(buf))

        def fake_global_lock(_h):
            guard("GlobalLock", None)
            # Widen the read window so an unserialised poller would land in a
            # mutation rather than slipping between two of them.
            _real_sleep(0.002)
            return ctypes.addressof(buf)

        monkeypatch.setattr(win._k32, "GlobalLock", fake_global_lock)
        monkeypatch.setattr(win._k32, "GlobalUnlock", lambda _h: guard("GlobalUnlock", True))
        monkeypatch.setattr(win._u32, "CloseClipboard", lambda: guard("CloseClipboard", True))

        provider = win.WindowsPlatformProvider()
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                provider.get_clipboard_text()
                reads[0] += 1
                _real_sleep(0.0005)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            for i in range(INTERLEAVINGS):
                paster._last_paste_time = 0.0
                paster._last_paste_hash = ""
                ts, digest = paster.paste_text(f"payload {i}")
                assert ts > 0.0 and digest, "the paste itself must still succeed"
        finally:
            stop.set()
            poller.join(timeout=10)

        assert reads[0] > 0, "the poller never ran — the test proved nothing"
        assert overlaps == [], f"read ran during a clipboard mutation: {set(overlaps)}"

    def test_paste_holds_the_lock_across_the_whole_sequence(self, monkeypatch, fast_paster):
        """The lock must span the trailing clear, not just the two copies.

        The 0.5s window between the hotkey and the final ``copy("")`` is when
        the target application is reading the clipboard; a poll landing in the
        middle of it is the same race.
        """
        from contextpulse_core.clipboard_lock import clipboard_lock

        paster = fast_paster
        free_at: list[str] = []

        def probe(phase):
            got = []

            def attempt():
                if clipboard_lock.acquire(blocking=False):
                    got.append(True)
                    clipboard_lock.release()

            t = threading.Thread(target=attempt)
            t.start()
            t.join(timeout=5)
            if got:
                free_at.append(phase)

        calls = [0]

        def fake_copy(_text=""):
            calls[0] += 1
            probe(f"copy#{calls[0]}")

        monkeypatch.setattr(paster.pyperclip, "copy", fake_copy)
        monkeypatch.setattr(paster.pyautogui, "hotkey", lambda *a: probe("hotkey"))

        ts, _ = paster.paste_text("some transcription")

        assert ts > 0.0
        assert calls[0] == 3, "expected clear, copy, clear"
        assert free_at == [], f"lock was not held during: {free_at}"

    def test_paste_drops_rather_than_racing_when_the_lock_is_unavailable(
        self, monkeypatch, fast_paster
    ):
        """Losing one dictation beats taking the daemon down mid-session."""
        from contextpulse_core.clipboard_lock import clipboard_lock

        paster = fast_paster
        copied: list[str] = []
        monkeypatch.setattr(paster.pyperclip, "copy", lambda t="": copied.append(t))
        monkeypatch.setattr(paster, "CLIPBOARD_LOCK_TIMEOUT", 0.05, raising=False)
        # The retry too, or the paste outlives the holder and succeeds.
        monkeypatch.setattr(paster, "CLIPBOARD_RETRY_TIMEOUT", 0.05, raising=False)

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
            ts, digest = paster.paste_text("some transcription")
            assert (ts, digest) == (0.0, "")
            assert copied == [], "must not touch the clipboard without the lock"
        finally:
            release.set()
            t.join(timeout=5)

        # And the drop must not have stranded anything.
        got = []

        def attempt():
            if clipboard_lock.acquire(timeout=1.0):
                got.append(True)
                clipboard_lock.release()

        t2 = threading.Thread(target=attempt)
        t2.start()
        t2.join(timeout=5)
        assert got == [True]


class TestDroppedPasteIsVisible:
    """A dropped paste used to be a logger.error in a file nobody watches.

    The transcription itself survives — the voice module emits the
    TRANSCRIPTION event before pasting, so the text is in the DB and
    reachable over MCP — but the user speaks, waits, and sees nothing
    appear. Dropping the paste is still the right call; dropping it
    silently is not.
    """

    @pytest.fixture
    def contended(self, monkeypatch, fast_paster):
        """The clipboard lock held by another thread, with short timeouts."""
        from contextpulse_core.clipboard_lock import clipboard_lock

        paster = fast_paster
        monkeypatch.setattr(paster, "CLIPBOARD_LOCK_TIMEOUT", 0.02, raising=False)
        monkeypatch.setattr(paster, "CLIPBOARD_RETRY_TIMEOUT", 0.05, raising=False)
        monkeypatch.setattr(paster, "_drop_notifier", None, raising=False)

        held = threading.Event()
        release = threading.Event()

        def holder():
            with clipboard_lock:
                held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert held.wait(timeout=5)
        yield paster, release
        release.set()
        t.join(timeout=5)

    def test_the_acquire_is_retried_before_giving_up(self, monkeypatch, contended, caplog):
        import logging

        paster, _release = contended
        attempts = []
        real_acquire = paster.clipboard_lock.acquire

        def counting_acquire(*a, **kw):
            attempts.append(kw.get("timeout"))
            return real_acquire(*a, **kw)

        monkeypatch.setattr(paster, "clipboard_lock", _LockProxy(paster.clipboard_lock, counting_acquire))

        with caplog.at_level(logging.WARNING, logger=paster.logger.name):
            ts, digest = paster.paste_text("some transcription")

        assert (ts, digest) == (0.0, "")
        assert attempts == [0.02, 0.05], "expected one retry at the longer timeout"
        assert any("retrying once" in r.getMessage() for r in caplog.records)

    def test_a_retry_that_succeeds_pastes_normally(self, monkeypatch, contended):
        paster, release = contended
        copied: list[str] = []
        monkeypatch.setattr(paster.pyperclip, "copy", lambda t="": copied.append(t))
        monkeypatch.setattr(paster, "CLIPBOARD_RETRY_TIMEOUT", 5.0, raising=False)

        # Free the lock while the retry is waiting on it.
        threading.Timer(0.15, release.set).start()

        ts, digest = paster.paste_text("some transcription")

        assert ts > 0.0 and digest
        assert copied == ["", "some transcription", ""]

    def test_the_user_is_told_through_the_registered_notifier(self, monkeypatch, contended):
        paster, _release = contended
        told: list[str] = []
        paster.set_drop_notifier(told.append)
        try:
            ts, _ = paster.paste_text("some transcription")
        finally:
            paster.set_drop_notifier(None)

        assert ts == 0.0
        assert len(told) == 1 and len(told[0]) == 16, "notified with the text hash"

    def test_a_broken_notifier_cannot_break_the_paste_path(self, monkeypatch, contended):
        paster, _release = contended

        def explode(_hash):
            raise RuntimeError("the UI is gone")

        paster.set_drop_notifier(explode)
        try:
            assert paster.paste_text("some transcription") == (0.0, "")
        finally:
            paster.set_drop_notifier(None)

    def test_no_notifier_registered_is_fine(self, contended):
        paster, _release = contended
        assert paster.paste_text("some transcription") == (0.0, "")


class _LockProxy:
    """An RLock wrapper that can count acquires (RLock attrs are read-only)."""

    def __init__(self, lock, acquire):
        self._lock = lock
        self.acquire = acquire

    def release(self):
        self._lock.release()


def _capture_fd2(tmp_path, fn):
    """Run fn() with fd 2 redirected to a file; return the file's lines.

    Redirects the raw file descriptor, not sys.stderr, because the whole
    point of the breadcrumb is that it bypasses Python's text layer and
    writes to the OS handle the watchdog redirects to daemon_stderr.log.
    A capsys-style capture would prove nothing about that.
    """
    target = tmp_path / "fd2.log"
    saved = os.dup(2)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.dup2(fd, 2)
        fn()
    finally:
        os.dup2(saved, 2)
        os.close(fd)
        os.close(saved)
    return target.read_text(encoding="utf-8", errors="replace").splitlines()


class TestPhaseBreadcrumbs:
    """The 09-19 crash left a five-second hole between "Pasted 100 characters"
    and 0xC0000374, covering the hotkey, a 0.5s sleep and the trailing
    pyperclip.copy(""). These name the phase instead.
    """

    def test_verbose_mode_writes_every_ordered_phase_to_fd_2(
        self, monkeypatch, fast_paster, tmp_path
    ):
        paster = fast_paster
        monkeypatch.setattr(paster, "_BREADCRUMBS", True, raising=False)
        monkeypatch.setattr(paster, "_VERBOSE_BREADCRUMBS", True, raising=False)
        monkeypatch.setattr(paster.pyperclip, "copy", lambda _t="": None)

        lines = _capture_fd2(tmp_path, lambda: paster.paste_text("hello"))

        phases = [ln.split()[-1] for ln in lines if ln.startswith("CLIPBOARD_PHASE")]
        assert phases == [
            "paste_copy_clear_enter",
            "paste_copy_clear_exit",
            "paste_copy_text_enter",
            "paste_copy_text_exit",
            "paste_hotkey_enter",
            "paste_hotkey_exit",
            "paste_final_clear_enter",
            "paste_final_clear_exit",
        ]

    def test_default_mode_writes_one_line_per_paste(
        self, monkeypatch, fast_paster, tmp_path
    ):
        """daemon_stderr.log is only rotated on daemon RESTART.

        Eight lines per dictation at ~30 dictations an hour accumulates in
        the one log family this branch did not bound. One line per paste
        keeps the forensics (it names the last phase reached) at an eighth
        of the volume.
        """
        paster = fast_paster
        monkeypatch.setattr(paster, "_BREADCRUMBS", True, raising=False)
        monkeypatch.setattr(paster, "_VERBOSE_BREADCRUMBS", False, raising=False)
        monkeypatch.setattr(paster.pyperclip, "copy", lambda _t="": None)

        lines = _capture_fd2(tmp_path, lambda: paster.paste_text("hello"))

        crumbs = [ln for ln in lines if ln.startswith("CLIPBOARD_PHASE")]
        assert len(crumbs) == 1
        assert "outcome=completed" in crumbs[0]
        assert "last_phase=paste_final_clear_exit" in crumbs[0]

    def test_a_dropped_paste_names_its_outcome(self, monkeypatch, fast_paster, tmp_path):
        from contextpulse_core.clipboard_lock import clipboard_lock

        paster = fast_paster
        monkeypatch.setattr(paster, "_BREADCRUMBS", True, raising=False)
        monkeypatch.setattr(paster, "_VERBOSE_BREADCRUMBS", False, raising=False)
        monkeypatch.setattr(paster, "CLIPBOARD_LOCK_TIMEOUT", 0.02, raising=False)
        monkeypatch.setattr(paster, "CLIPBOARD_RETRY_TIMEOUT", 0.02, raising=False)

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
            lines = _capture_fd2(tmp_path, lambda: paster.paste_text("hello"))
        finally:
            release.set()
            t.join(timeout=5)

        crumbs = [ln for ln in lines if ln.startswith("CLIPBOARD_PHASE")]
        assert len(crumbs) == 1
        assert "outcome=dropped_lock_timeout" in crumbs[0]

    def test_breadcrumbs_can_be_silenced(self, monkeypatch, fast_paster, tmp_path):
        paster = fast_paster
        monkeypatch.setattr(paster, "_BREADCRUMBS", False, raising=False)
        monkeypatch.setattr(paster.pyperclip, "copy", lambda _t="": None)

        lines = _capture_fd2(tmp_path, lambda: paster.paste_text("hello"))

        assert [ln for ln in lines if ln.startswith("CLIPBOARD_PHASE")] == []

    def test_a_contended_read_names_itself(self, monkeypatch, tmp_path):
        """The one read-side breadcrumb that is always on.

        Bounded by paste frequency rather than by the 1s poll, and it is the
        line that shows the poller and the paster met at all.
        """
        from contextpulse_core.clipboard_lock import clipboard_lock
        from contextpulse_core.platform import windows as win

        monkeypatch.setattr(win, "CLIPBOARD_LOCK_TIMEOUT", 0.05)
        provider = win.WindowsPlatformProvider()

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
            lines = _capture_fd2(tmp_path, lambda: provider.get_clipboard_text())
        finally:
            release.set()
            t.join(timeout=5)

        assert any(ln.endswith("read_skipped_lock_busy") for ln in lines), lines
