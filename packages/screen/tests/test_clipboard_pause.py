# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Pause Capture must stop clipboard capture too.

contextpulse-pause-skips-clipboard-20261110: the tray's Pause Capture and the
pause hotkey set ContextPulseSightApp.paused, which the screen capture paths
read, but ClipboardMonitor._check_clipboard never looked at it. A password
copied while paused was stored, and redact_sensitive matches vendor key shapes,
not an ordinary password.

Two properties matter, and the second is the one a naive fix misses:
  1. Nothing is recorded while paused, and the text is not even read.
  2. A clip copied while paused is NOT captured once capture resumes. The
     clipboard still holds it after resume, so a guard that only skips the
     tick would store the password on the first tick after Resume.
"""

from unittest.mock import MagicMock

import pytest
from contextpulse_sight import clipboard as clipboard_mod
from contextpulse_sight.clipboard import ClipboardMonitor


class _Clipboard:
    """A fake clipboard: each copy bumps the sequence, as Windows does."""

    def __init__(self):
        self.seq = 0
        self.text = None
        self.reads = 0

    def copy(self, text):
        self.seq += 1
        self.text = text

    def read(self):
        self.reads += 1
        return self.text


@pytest.fixture
def board(monkeypatch):
    b = _Clipboard()
    monkeypatch.setattr(clipboard_mod, "_get_clipboard_sequence", lambda: b.seq)
    monkeypatch.setattr(clipboard_mod, "_get_clipboard_text", b.read)
    # The 1s debounce would swallow the second capture in a fast test.
    monkeypatch.setattr(clipboard_mod, "_MIN_INTERVAL", 0.0)
    return b


def _monitor(state):
    m = ClipboardMonitor(activity_db=MagicMock(), is_paused=lambda: state["paused"])
    m.set_sight_module(MagicMock())
    return m


def _recorded(m):
    return [c.kwargs["text"] for c in m._activity_db.record_clipboard.call_args_list]


class TestPausedMonitorStoresNothing:
    def test_a_clip_copied_while_paused_is_not_recorded(self, board):
        state = {"paused": True}
        m = _monitor(state)
        board.copy("hunter2-correct-horse")

        m._check_clipboard()

        assert _recorded(m) == []
        m._sight_module.emit_clipboard.assert_not_called()

    def test_the_clipboard_is_not_even_read_while_paused(self, board):
        """Not reading it keeps the secret out of process memory (_last_text)."""
        m = _monitor({"paused": True})
        board.copy("hunter2-correct-horse")

        m._check_clipboard()

        assert board.reads == 0
        assert m._last_text == ""

    def test_a_clip_copied_while_paused_is_not_captured_after_resume(self, board):
        state = {"paused": True}
        m = _monitor(state)
        board.copy("hunter2-correct-horse")
        m._check_clipboard()

        state["paused"] = False
        m._check_clipboard()  # clipboard still holds the paused-time copy

        assert _recorded(m) == [], (
            "the password copied while paused was stored on the first tick "
            "after Resume"
        )

    def test_a_clip_copied_after_resume_is_captured(self, board):
        state = {"paused": True}
        m = _monitor(state)
        board.copy("hunter2-correct-horse")
        m._check_clipboard()

        state["paused"] = False
        board.copy("an ordinary note after resume")
        m._check_clipboard()

        assert _recorded(m) == ["an ordinary note after resume"]

    def test_a_failing_pause_check_fails_closed(self, board):
        def broken():
            raise RuntimeError("app torn down")

        m = ClipboardMonitor(activity_db=MagicMock(), is_paused=broken)
        board.copy("hunter2-correct-horse")

        m._check_clipboard()

        assert _recorded(m) == []

    def test_no_pause_check_means_capture_as_before(self, board):
        m = ClipboardMonitor(activity_db=MagicMock())
        board.copy("some copied text")

        m._check_clipboard()

        assert _recorded(m) == ["some copied text"]


def _make_app(tmp_path, monkeypatch):
    import contextpulse_sight.activity as act
    import contextpulse_sight.buffer as buf_mod
    import contextpulse_sight.config as cfg

    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir()
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
    monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

    from contextpulse_sight.app import ContextPulseSightApp

    return ContextPulseSightApp()


class TestEveryConstructionSiteIsWiredToThePause:
    """The app builds a monitor in three places; each must read app.paused."""

    def _assert_follows(self, app, monitor):
        app.paused = False
        assert monitor._paused() is False
        app.toggle_pause()
        assert monitor._paused() is True
        app.toggle_pause()
        assert monitor._paused() is False

    def test_init_site(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        assert app._clipboard_monitor is not None
        self._assert_follows(app, app._clipboard_monitor)

    def test_start_site(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        app._clipboard_monitor = None
        monkeypatch.setattr(ClipboardMonitor, "start", lambda self: None)
        monkeypatch.setattr(app, "_start_clipboard_reconcile_thread", lambda: None)
        app._start_clipboard_monitor()
        self._assert_follows(app, app._clipboard_monitor)

    def test_reconcile_restart_site(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        app._clipboard_monitor = None
        monkeypatch.setattr(ClipboardMonitor, "start", lambda self: None)
        app._reconcile_clipboard_monitor()
        self._assert_follows(app, app._clipboard_monitor)

    def test_session_lock_pauses_the_clipboard(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        monitor = app._clipboard_monitor
        app._on_session_lock()
        assert monitor._paused() is True
        app._on_session_unlock()
        assert monitor._paused() is False
