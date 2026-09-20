# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""T14-T17: the Settings controls app.py owns actually drive app.py.

Four of the MISLEADING rows in the 2026-09-19 dead-controls ledger live
here: the capture interval, its two idle companions, the OCR-skip threshold,
and the four hotkeys. All five were module constants read from environment
variables at import, so a value saved in Settings reached the file and
nothing else -- and for the interval, setting it to 0 was a one-way trip,
because both start sites declined to start the capture thread at all and a
thread that never starts cannot notice you changing your mind.
"""

import threading
import time
from unittest.mock import patch

from contextpulse_core.config import save_config
from PIL import Image


def _modifier_key(name: str):
    """The Key object app.py itself would compare against.

    Deliberately NOT `from pynput import keyboard` at module scope. This
    package's conftest replaces sys.modules["pynput"] with a MagicMock, but
    contextpulse_sight.app may already have been imported -- by
    packages/core/tests/test_daemon.py, which runs first in a full-suite run
    -- against the REAL pynput. The two Key.ctrl_l objects are then different
    and _pressed_modifiers() sees nothing held, so the test fails only when
    run after the core package. Reading the module app.py actually bound is
    correct under either ordering.
    """
    from contextpulse_sight import app as app_mod

    return getattr(app_mod.keyboard.Key, name)


class _LetterKey:
    """A pynput-shaped key carrying a real character.

    Under the conftest's MagicMock pynput, `keyboard.KeyCode` would hand
    _get_key_letter a MagicMock whose .char is also a MagicMock. Modifier
    keys come from the module (identity is all that matters for those);
    letter keys need a real .char.
    """

    def __init__(self, char: str) -> None:
        self.char = char


def _make_image(width=100, height=100, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


def _make_app(tmp_path, monkeypatch):
    import contextpulse_sight.activity as act
    import contextpulse_sight.buffer as buf_mod

    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir()
    monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
    monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

    from contextpulse_sight.app import ContextPulseSightApp
    return ContextPulseSightApp()


def _close(app):
    app.stop_event.set()
    app._sight_module.stop()
    app._event_bus.close()
    app.activity_db.close()


class TestOcrDiffThreshold:
    """T14: the OCR-skip gate reads the saved threshold, per capture."""

    def _run_capture(self, app, monkeypatch, force_ocr=False):
        enqueued = []
        monkeypatch.setattr(app._ocr_worker, "enqueue", lambda *a, **kw: enqueued.append(a))
        base = _make_image(color=(100, 100, 100))
        # ~10% pixel difference from the first frame
        nudged = _make_image(color=(126, 126, 126))
        with (
            patch("contextpulse_sight.app.capture.capture_all_monitors",
                  side_effect=[[(0, base, base)], [(0, nudged, nudged)]]),
            patch("contextpulse_sight.app.capture.find_monitor_at_cursor", return_value=(0, {})),
            patch("contextpulse_sight.app.capture.save_image"),
            patch("mss.mss"),
            patch("contextpulse_sight.app.get_foreground_window_title", return_value="Editor"),
            patch("contextpulse_sight.app.get_foreground_process_name", return_value="code.exe"),
            patch("contextpulse_sight.app.is_blocked", return_value=False),
        ):
            app._do_auto_capture()          # first frame: always stored, diff 100
            enqueued.clear()
            app._do_auto_capture(force_ocr=force_ocr)  # second: ~10% diff
        return enqueued

    def test_a_high_threshold_skips_ocr(self, tmp_path, monkeypatch, isolated_config):
        save_config({"ocr_diff_threshold": 50, "change_threshold": 0})
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert self._run_capture(app, monkeypatch) == []
        finally:
            _close(app)

    def test_a_low_threshold_runs_ocr(self, tmp_path, monkeypatch, isolated_config):
        """Positive control: the frame IS enqueued once the gate allows it."""
        save_config({"ocr_diff_threshold": 1, "change_threshold": 0})
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert len(self._run_capture(app, monkeypatch)) == 1
        finally:
            _close(app)

    def test_force_ocr_still_bypasses_a_high_threshold(
        self, tmp_path, monkeypatch, isolated_config
    ):
        save_config({"ocr_diff_threshold": 50, "change_threshold": 0})
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert len(self._run_capture(app, monkeypatch, force_ocr=True)) == 1
        finally:
            _close(app)


class TestIntervalsAreLive:
    """T15-T16: the three interval keys are read per loop iteration."""

    def test_intervals_come_from_the_saved_config(self, tmp_path, monkeypatch, isolated_config):
        save_config({
            "auto_interval": 7,
            "auto_interval_idle": 70,
            "auto_idle_threshold": 700,
        })
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._intervals() == (7.0, 70.0, 700.0)
        finally:
            _close(app)

    def test_defaults_apply_with_no_config_file(self, tmp_path, monkeypatch, isolated_config):
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._intervals() == (5.0, 30.0, 60.0)
        finally:
            _close(app)

    def test_env_overrides_the_saved_interval(self, tmp_path, monkeypatch, isolated_config):
        save_config({"auto_interval_idle": 70})
        monkeypatch.setenv("CONTEXTPULSE_AUTO_INTERVAL_IDLE", "120")
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._intervals()[1] == 120.0
        finally:
            _close(app)

    def test_zero_is_clamped_up_by_the_config_layer(self, tmp_path, monkeypatch, isolated_config):
        """auto_interval_idle 0 would mean 'recapture instantly'; floor is 1."""
        monkeypatch.setenv("CONTEXTPULSE_AUTO_INTERVAL_IDLE", "0")
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._intervals()[1] >= 1
        finally:
            _close(app)

    def test_auto_interval_zero_and_back_are_both_live(
        self, tmp_path, monkeypatch, isolated_config
    ):
        """T15, on the real thread -- the whole point of always starting it.

        Starts at 0 (no captures), switches to 1 (captures resume), switches
        back to 0 (captures stop), with no restart in between. Before this
        change the loop would not have been running at all at step one.
        """
        save_config({"auto_interval": 0})
        app = _make_app(tmp_path, monkeypatch)
        captures: list[float] = []
        monkeypatch.setattr(app, "_do_auto_capture", lambda force_ocr=False: captures.append(time.time()))

        thread = threading.Thread(target=app._auto_capture_loop, daemon=True)
        thread.start()
        try:
            time.sleep(1.0)
            assert captures == [], "auto_interval=0 must not capture"

            save_config({"auto_interval": 1})
            deadline = time.time() + 12  # the off-branch re-reads every 5s
            while not captures and time.time() < deadline:
                time.sleep(0.05)
            assert captures, (
                "auto_interval 0 -> 1 never took effect: the loop is not "
                "re-reading the interval (or was never started)"
            )

            save_config({"auto_interval": 0})
            time.sleep(2.5)
            frozen = len(captures)
            time.sleep(2.5)
            assert len(captures) == frozen, "auto_interval 1 -> 0 never took effect"
        finally:
            app.stop_event.set()
            thread.join(timeout=7)
            assert not thread.is_alive(), "the capture loop did not stop"
            _close(app)


class TestHotkeysComeFromConfig:
    """T17: the four hotkey_* keys drive dispatch AND the tray labels."""

    def _press(self, app, mods, letter):
        for mod in mods:
            app._on_press(_modifier_key(mod))
        app._on_press(_LetterKey(letter))

    def test_a_saved_pause_hotkey_replaces_the_default(
        self, tmp_path, monkeypatch, isolated_config
    ):
        save_config({"hotkey_pause": "ctrl+shift+q"})
        app = _make_app(tmp_path, monkeypatch)
        try:
            self._press(app, ["ctrl_l", "shift_l"], "q")
            assert app.paused is True, "the saved hotkey did not reach dispatch"

            app._pressed_keys.clear()
            self._press(app, ["ctrl_l", "shift_l"], "p")
            assert app.paused is True, (
                "ctrl+shift+p still toggles pause after being reconfigured "
                "away -- the dispatch table is hardcoded, not parsed"
            )
        finally:
            _close(app)

    def test_the_default_hotkey_still_works(self, tmp_path, monkeypatch, isolated_config):
        app = _make_app(tmp_path, monkeypatch)
        try:
            self._press(app, ["ctrl_l", "shift_l"], "p")
            assert app.paused is True
        finally:
            _close(app)

    def test_a_different_modifier_set_is_honoured(self, tmp_path, monkeypatch, isolated_config):
        save_config({"hotkey_pause": "ctrl+alt+p"})
        app = _make_app(tmp_path, monkeypatch)
        try:
            self._press(app, ["ctrl_l", "shift_l"], "p")
            assert app.paused is False, "ctrl+shift+p fired a ctrl+alt+p binding"

            app._pressed_keys.clear()
            self._press(app, ["ctrl_l", "alt_l"], "p")
            assert app.paused is True
        finally:
            _close(app)

    def test_an_unusable_hotkey_falls_back_to_the_default(
        self, tmp_path, monkeypatch, isolated_config, caplog
    ):
        save_config({"hotkey_pause": "ctrl+shift+nonsense"})
        with caplog.at_level("WARNING"):
            app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._hotkeys["hotkey_pause"] == (frozenset({"ctrl", "shift"}), "p")
            assert any("not usable" in r.message for r in caplog.records), (
                "a bad hotkey was silently swallowed"
            )
            self._press(app, ["ctrl_l", "shift_l"], "p")
            assert app.paused is True
        finally:
            _close(app)

    def test_a_bare_letter_is_rejected(self, tmp_path, monkeypatch, isolated_config):
        """A modifier-less hotkey would fire on every keystroke."""
        save_config({"hotkey_pause": "p"})
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._hotkeys["hotkey_pause"] == (frozenset({"ctrl", "shift"}), "p")
        finally:
            _close(app)

    def test_tray_labels_are_built_from_the_configured_hotkeys(
        self, tmp_path, monkeypatch, isolated_config
    ):
        save_config({"hotkey_capture": "ctrl+alt+k", "hotkey_pause": "ctrl+shift+q"})
        app = _make_app(tmp_path, monkeypatch)
        try:
            assert app._hotkey_label("hotkey_capture") == "(Ctrl+Alt+K)"
            assert app._hotkey_label("hotkey_pause") == "(Ctrl+Shift+Q)"
            assert app._hotkey_label("hotkey_region") == "(Ctrl+Shift+Z)"
        finally:
            _close(app)


class TestHotkeyParsing:
    """The pure parser, away from the app."""

    def test_parses_modifiers_and_letter(self):
        from contextpulse_sight.app import parse_hotkey

        assert parse_hotkey("ctrl+shift+s", "ctrl+shift+s") == (
            frozenset({"ctrl", "shift"}), "s",
        )

    def test_is_case_and_space_insensitive(self):
        from contextpulse_sight.app import parse_hotkey

        assert parse_hotkey(" CTRL + Alt + K ", "ctrl+shift+s") == (
            frozenset({"ctrl", "alt"}), "k",
        )

    def test_rejects_an_unknown_modifier(self):
        from contextpulse_sight.app import parse_hotkey

        assert parse_hotkey("meta+s", "ctrl+shift+s") == (frozenset({"ctrl", "shift"}), "s")

    def test_rejects_a_non_letter_key(self):
        from contextpulse_sight.app import parse_hotkey

        assert parse_hotkey("ctrl+shift+1", "ctrl+shift+s") == (frozenset({"ctrl", "shift"}), "s")
        assert parse_hotkey("", "ctrl+shift+s") == (frozenset({"ctrl", "shift"}), "s")

    def test_formats_in_a_fixed_order(self):
        from contextpulse_sight.app import format_hotkey

        assert format_hotkey(frozenset({"shift", "ctrl", "alt"}), "s") == "Ctrl+Shift+Alt+S"

    def test_every_default_hotkey_parses(self):
        from contextpulse_core.config import _DEFAULTS
        from contextpulse_sight.app import _HOTKEY_KEYS, _parse_hotkey_spec

        for key in _HOTKEY_KEYS:
            assert _parse_hotkey_spec(_DEFAULTS[key]) is not None, (
                f"_DEFAULTS[{key!r}] = {_DEFAULTS[key]!r} is not parseable, so "
                f"the fallback path would raise"
            )
