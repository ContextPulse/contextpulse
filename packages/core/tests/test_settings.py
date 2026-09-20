# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""T20 from .internal/audit-2026-09-19/spec-config-unification.md section 5.

The Settings dialog used to hold a second declaration of every default
(`cfg.get("jpeg_quality", 75)` beside `_DEFAULTS["jpeg_quality"] == 90`), and
a restart notice keyed on a hand-maintained seven-element tuple whose text
said "hotkey" whatever had actually changed. Both are the same defect --
a value declared twice drifts, and nothing was comparing the two copies.

These tests pin the declaration site and the pairing, not the widgets: tkinter
is a MagicMock here (packages/core/tests/conftest.py mocks it at the
sys.modules level before any import), so building the real dialog would assert
nothing about Tk. What IS worth pinning is that every literal the dialog can
fall back to comes from `_DEFAULTS`, and that `_RESTART_KEYS` and
`_RESTART_LABELS` cannot drift apart.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from contextpulse_core import settings
from contextpulse_core.config import _DEFAULTS

# The exact membership the spec asks for: the four sight hotkeys, the three
# voice keys, and the two touch keys this dialog actually exposes a control
# for. Spelled out rather than derived, so a key silently dropped from
# _RESTART_KEYS fails here instead of quietly making the notice lie again.
EXPECTED_RESTART_KEYS = {
    "hotkey_capture",
    "hotkey_all_monitors",
    "hotkey_region",
    "hotkey_pause",
    "voice_hotkey",
    "voice_fix_hotkey",
    "voice_whisper_model",
    "touch_burst_timeout",
    "touch_correction_window",
}


class TestRestartKeys:
    def test_restart_keys_are_the_four_sight_three_voice_and_two_touch_keys(self):
        assert set(settings._RESTART_KEYS) == EXPECTED_RESTART_KEYS

    def test_restart_keys_has_no_duplicates(self):
        keys = settings._RESTART_KEYS
        assert len(keys) == len(set(keys)), f"duplicate entries in _RESTART_KEYS: {keys}"

    def test_every_restart_key_is_a_real_config_key(self):
        """A typo here would make the notice raise KeyError at save time."""
        unknown = sorted(set(settings._RESTART_KEYS) - set(_DEFAULTS))
        assert not unknown, f"_RESTART_KEYS names keys _DEFAULTS does not declare: {unknown}"

    def test_every_restart_key_has_a_label(self):
        """The notice renders _RESTART_LABELS[k]; an unlabelled key crashes it.

        Derived artefact + hand-maintained twin: the tuple decides what the
        notice fires on, the dict decides what it says. Nothing else pairs
        them, so this test does.
        """
        missing = sorted(set(settings._RESTART_KEYS) - set(settings._RESTART_LABELS))
        assert not missing, f"_RESTART_KEYS entries with no _RESTART_LABELS text: {missing}"

    def test_no_label_without_a_key(self):
        orphans = sorted(set(settings._RESTART_LABELS) - set(settings._RESTART_KEYS))
        assert not orphans, f"_RESTART_LABELS describes keys that cannot fire: {orphans}"

    def test_the_two_unexposed_touch_keys_are_not_in_the_tuple(self):
        """They are startup-bound too, but the dialog has no control for them.

        Including them would make the notice fire for something the user
        cannot have touched -- the mirror image of the bug this replaces.
        """
        for key in ("touch_min_burst_chars", "touch_mouse_debounce"):
            assert key not in settings._RESTART_KEYS

    def test_live_keys_are_not_claimed_to_need_a_restart(self):
        """Keys the spec classifies LIVE must never appear in the notice."""
        live = (
            "blocklist_patterns",
            "always_both_apps",
            "clipboard_enabled",
            "redact_ocr_text",
            "storage_mode",
            "jpeg_quality",
            "buffer_max_age",
            "auto_interval",
            "voice_always_use_llm",
        )
        wrong = [k for k in live if k in settings._RESTART_KEYS]
        assert not wrong, f"LIVE keys listed as restart-bound: {wrong}"


class TestNoSecondDeclarationSite:
    """The dialog must not re-declare a default that _DEFAULTS already holds.

    Measured before this change: `cfg.get("jpeg_quality", 75)` against
    `_DEFAULTS["jpeg_quality"] == 90`, and `cfg.get("voice_whisper_model",
    "base")` twice against `"small"` -- opening Settings and pressing Save
    silently rewrote both to the dialog's stale copy.
    """

    def _module_tree(self) -> ast.AST:
        source = Path(inspect.getfile(settings)).read_text(encoding="utf-8")
        return ast.parse(source)

    def test_no_cfg_get_call_supplies_a_fallback_default(self):
        """Find `cfg.get("some_key", <anything>)` anywhere in the module.

        A two-argument .get() on the config dict is the shape of the defect:
        load_config() fills every key in _DEFAULTS, so the second argument can
        only ever be a duplicate declaration or dead code.
        """
        offenders = []
        for node in ast.walk(self._module_tree()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "cfg"):
                continue
            if len(node.args) < 2:
                continue
            key = node.args[0]
            key_text = key.value if isinstance(key, ast.Constant) else ast.dump(key)
            offenders.append(f"line {node.lineno}: cfg.get({key_text!r}, ...)")
        assert not offenders, (
            "the Settings dialog re-declares defaults that contextpulse_core.config."
            f"_DEFAULTS already owns: {offenders}"
        )

    def test_the_scan_is_not_vacuous(self):
        """It must actually be reading settings.py, not an empty parse."""
        tree = self._module_tree()
        names = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        assert {"_build_and_run", "show_settings", "_as_float"} <= names, names
        subscripts = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name)
            and n.value.id == "cfg"
        ]
        assert len(subscripts) >= 15, (
            f"only {len(subscripts)} cfg[...] reads found; the dialog should read "
            "every control's value straight out of the merged config"
        )


class TestAsFloat:
    """A bad touch value must not discard the whole save.

    show_settings() catches every exception to keep a Tk error from taking the
    daemon down, so a ValueError out of float() used to swallow the entire
    save_and_close -- blocklist, hotkeys and all -- with no message.
    """

    def test_blank_field_uses_the_declared_default(self):
        assert settings._as_float("", "touch_burst_timeout") == _DEFAULTS["touch_burst_timeout"]

    def test_garbage_uses_the_declared_default_instead_of_raising(self):
        assert settings._as_float("1,5", "touch_correction_window") == _DEFAULTS["touch_correction_window"]

    def test_a_real_number_is_honoured(self):
        assert settings._as_float(" 0.75 ", "touch_burst_timeout") == 0.75

    def test_the_fallback_is_not_a_hardcoded_literal(self, monkeypatch):
        """Mutation check: move the default, and the fallback must move too."""
        monkeypatch.setitem(_DEFAULTS, "touch_burst_timeout", 9.25)
        assert settings._as_float("nonsense", "touch_burst_timeout") == 9.25
