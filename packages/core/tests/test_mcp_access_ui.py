# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for the MCP Access UI surfaces (tray, settings dialog, first run).

tkinter and pystray are MagicMocks in this package's conftest, so a built
dialog cannot be inspected. The clipboard path is therefore tested for real
and the rest are source guards -- weaker, but they catch the failure that
actually matters here: a token the user has no way to reach, which looks
exactly like a working build until an agent gets a 401.
"""

import inspect
import sys
import threading
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from contextpulse_core import clipboard_lock, daemon, first_run, mcp_auth, settings

pytestmark = pytest.mark.unit


def test_copy_mcp_token_puts_the_snippet_on_the_clipboard(monkeypatch):
    copied: list[str] = []
    fake = types.ModuleType("pyperclip")
    fake.copy = copied.append
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    monkeypatch.setattr(mcp_auth, "load_or_create_token", lambda *a: "tok-from-file")

    daemon._copy_mcp_token()

    assert len(copied) == 1
    assert "Bearer tok-from-file" in copied[0]
    assert "127.0.0.1:8420/mcp" in copied[0]


def test_copy_mcp_token_survives_a_missing_clipboard(monkeypatch):
    """A clipboard failure must never take the tray callback's thread down."""
    fake = types.ModuleType("pyperclip")

    def boom(_):
        raise RuntimeError("no clipboard on this box")

    fake.copy = boom
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    monkeypatch.setattr(mcp_auth, "load_or_create_token", lambda *a: "tok")

    daemon._copy_mcp_token()  # must not raise


# ── B1-8: every copy site holds the clipboard lock ───────────────────

class TestEveryCopySiteHoldsTheClipboardLock:
    """B1-8. The clipboard lock is only sufficient while EVERY in-process
    clipboard writer takes it. paster.py was the sole pyperclip caller when
    the lock shipped; the MCP Access UI added three more, and the merge of
    the two branches was clean, so nothing warned. pyperclip.copy calls
    EmptyClipboard, which frees handles the sight poller may be holding --
    the 0xC0000374 heap corruption, which exits the daemon with no traceback.
    """

    @staticmethod
    def _fake_clipboard(monkeypatch):
        copied: list[str] = []
        fake = types.ModuleType("pyperclip")
        fake.copy = copied.append
        monkeypatch.setitem(sys.modules, "pyperclip", fake)
        monkeypatch.setattr(mcp_auth, "load_or_create_token", lambda *a: "tok")
        return copied

    @staticmethod
    @contextmanager
    def _lock_held_by_another_thread():
        """Hold the real lock on a DIFFERENT thread for the body's duration.

        It has to be another thread: clipboard_lock is an RLock, so taking it
        on this one would be re-entered happily and the test would prove
        nothing about exclusion.
        """
        acquired = threading.Event()
        release = threading.Event()

        def holder():
            with clipboard_lock.clipboard_lock:
                acquired.set()
                release.wait(timeout=30)

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        assert acquired.wait(timeout=5), "helper thread never took the lock"
        try:
            yield
        finally:
            release.set()
            thread.join(timeout=5)

    def test_the_tray_item_copies_nothing_while_the_lock_is_held(self, monkeypatch, caplog):
        copied = self._fake_clipboard(monkeypatch)
        monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_TIMEOUT", 0.15)
        notified: list[tuple] = []
        caplog.set_level("ERROR")

        with self._lock_held_by_another_thread():
            daemon._copy_mcp_token(notify=lambda *a: notified.append(a))

        assert copied == [], "tray copy raced the lock holder"
        assert notified, "a dropped copy reached nobody but the log"
        assert any("Clipboard busy" in r.message for r in caplog.records)

    def test_the_first_run_button_copies_nothing_while_the_lock_is_held(self, monkeypatch):
        copied = self._fake_clipboard(monkeypatch)
        monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_TIMEOUT", 0.15)
        shown: list[str] = []

        # first_run builds its callback inside the dialog, which cannot be
        # constructed headless. Drive the same helper the callback calls, and
        # pin the wiring separately (test_first_run_copies_under_the_lock).
        with self._lock_held_by_another_thread():
            ok = clipboard_lock.copy_text("snippet", what="the MCP client config")
        if not ok:
            shown.append("Clipboard busy — not copied. Try again.")

        assert copied == []
        assert shown, "the dialog would have said nothing"

    def test_the_settings_button_copies_nothing_while_the_lock_is_held(self, monkeypatch):
        copied = self._fake_clipboard(monkeypatch)
        monkeypatch.setattr(clipboard_lock, "CLIPBOARD_LOCK_TIMEOUT", 0.15)

        with self._lock_held_by_another_thread():
            assert clipboard_lock.copy_text("snippet") is False

        assert copied == []

    def test_a_free_lock_still_copies(self, monkeypatch):
        """Positive control: the tests above must fail for exclusion, not
        because copy_text never copies anything."""
        copied = self._fake_clipboard(monkeypatch)
        assert clipboard_lock.copy_text("snippet") is True
        assert copied == ["snippet"]

    def test_the_lock_is_released_after_a_copy(self, monkeypatch):
        self._fake_clipboard(monkeypatch)
        clipboard_lock.copy_text("snippet")
        assert clipboard_lock.clipboard_lock.acquire(timeout=0.5), "lock leaked"
        clipboard_lock.clipboard_lock.release()

    def test_the_lock_is_released_when_the_copy_raises(self, monkeypatch):
        fake = types.ModuleType("pyperclip")

        def boom(_):
            raise RuntimeError("no clipboard")

        fake.copy = boom
        monkeypatch.setitem(sys.modules, "pyperclip", fake)
        assert clipboard_lock.copy_text("snippet") is False
        assert clipboard_lock.clipboard_lock.acquire(timeout=0.5), "lock leaked on the error path"
        clipboard_lock.clipboard_lock.release()

    def test_no_bare_pyperclip_copy_outside_the_helper(self):
        """The property that makes the lock sufficient, checked against the
        whole tree rather than against the three sites I happen to know."""
        root = Path(daemon.__file__).parents[4]
        allowed = {
            # The helper itself.
            "clipboard_lock.py",
            # The paster holds the lock across an entire copy/paste/restore
            # region including the Ctrl+V, so it cannot delegate to a helper
            # that releases between calls.
            "paster.py",
        }
        offenders = []
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if parts & {".venv", "build", "dist", "__pycache__", "tests"}:
                continue
            if path.name in allowed:
                continue
            if "pyperclip.copy(" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(str(path.relative_to(root)))
        assert offenders == [], (
            f"bare pyperclip.copy() outside clipboard_lock.copy_text: {offenders}. "
            "Every in-process clipboard write must hold the lock or the lock "
            "protects nothing."
        )

    def test_the_guard_above_can_actually_see_a_violation(self, tmp_path):
        """Verifying the verifier: the scan must not be vacuous."""
        root = Path(daemon.__file__).parents[4]
        scanned = [
            p for p in root.rglob("*.py")
            if not (set(p.parts) & {".venv", "build", "dist", "__pycache__", "tests"})
        ]
        assert len(scanned) > 50, f"the guard only looked at {len(scanned)} files"
        assert any(p.name == "paster.py" for p in scanned), "the known caller was not scanned"


def test_first_run_copies_under_the_lock():
    """The wiring the headless test above cannot exercise."""
    src = Path(first_run.__file__).read_text(encoding="utf-8")
    assert "clipboard_lock import copy_text" in src
    assert "pyperclip.copy(" not in src


def test_settings_copies_under_the_lock():
    src = Path(settings.__file__).read_text(encoding="utf-8")
    assert "clipboard_lock.copy_text(" in src
    assert "pyperclip.copy(" not in src


def test_tray_menu_offers_the_token_and_does_not_block_the_pump():
    src = inspect.getsource(daemon.ContextPulseDaemon._create_tray_menu)
    assert "Copy MCP Token" in src
    assert "_copy_mcp_token" in src
    compact = " ".join(src.split())
    assert "threading.Thread( target=_copy_mcp_token" in compact or (
        "threading.Thread(target=_copy_mcp_token" in compact
    ), (
        "clipboard work must run on a spawned thread -- blocking a pystray "
        "menu callback blocks the whole message pump"
    )
    assert "self._notify_tray" in compact, (
        "a dropped copy must reach the tray, not only the log"
    )


def test_macos_menu_offers_the_token():
    # Read the source rather than importing: tray_macos imports rumps at
    # module level, which is macOS-only and absent from the Windows venv.
    src = Path(daemon.__file__).with_name("tray_macos.py").read_text(encoding="utf-8")
    assert "Copy MCP Token" in src
    assert "def copy_mcp_token" in src


def test_settings_has_an_mcp_access_section_that_can_regenerate():
    src = Path(settings.__file__).read_text(encoding="utf-8")
    assert '_section_header(frame, "MCP Access")' in src
    assert "mcp_auth.regenerate_token()" in src
    assert 'mcp_auth.config_snippet("claude-code"' in src


def test_settings_never_writes_the_token_into_config_json():
    """The token lives in its own restricted file, never in config.json."""
    save = inspect.getsource(settings._build_and_run)
    body = save[save.index("def save_and_close"):save.index("# Bottom button bar")]
    assert "token" not in body.lower(), (
        "save_and_close must not persist the token -- config.json is not "
        "permission-restricted and is read by every package"
    )


def test_first_run_tells_the_user_the_agent_needs_a_token():
    src = Path(first_run.__file__).read_text(encoding="utf-8")
    assert "MCP Access" in src
    assert "contextpulse --setup claude-code" in src
    assert "Copy MCP config" in src
