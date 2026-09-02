"""Regression tests for KeyboardListener's Ctrl+V paste detection.

conftest.py mocks ``pynput`` globally for the rest of this package's test
suite (pynput needs a real OS hook to construct a ``Listener``, which is
unavailable/flaky in CI). That mock is precisely why a real defect in
``listeners.py`` was invisible to every other test in this repo: a
``MagicMock`` KeyCode compares equal to whatever a test configures it to, so
it never exercised pynput's actual ``KeyCode`` equality semantics.

These tests deliberately import the REAL, installed pynput package -- the one
that actually runs in production -- so assertions are against real behavior,
not a stand-in for it. No OS-level keyboard hook is started; ``_on_press`` is
called directly with ``KeyCode``/``Key`` objects built the same way pynput's
Windows backend actually builds them for a genuine Ctrl+V (verified live on
this machine: ``pyautogui.hotkey("ctrl", "v")`` observed by a real
``pynput.keyboard.Listener`` delivers ``KeyCode(vk=86, char='\\x16')`` for the
V key, never ``KeyCode.from_char('v')``).

Bug pinned here: ``listeners.py`` compared ``key == kb.KeyCode.from_char('v')``,
which is a KeyCode with ``vk=None, char='v'`` -- never equal to the real
``vk=86, char='\\x16'`` object pynput hands the callback when Ctrl is held.
The comparison could not match for ANY Ctrl+V, real keypress or this
project's own synthetic paste, so `on_paste_detected` never fired and the
whole correction-detection pipeline downstream (Touch's CorrectionDetector,
`correction_detected` events, cross-modal vocabulary mining) has recorded
zero corrections since at least 2026-04-09.
"""

import importlib
import sys
import time

import pytest


@pytest.fixture
def real_listeners_module():
    """Import contextpulse_touch.listeners bound to the REAL pynput package,
    not the module-wide mock from conftest.py, restoring the mock afterward
    so later tests in this session are unaffected.

    Popping sys.modules alone is not enough: once any earlier test file has
    imported `contextpulse_touch` (the parent package), that package object
    keeps a `listeners` attribute pointing at the stale (mock-bound) module.
    `from contextpulse_touch import listeners` then resolves via that
    attribute without re-executing the file, silently handing back the OLD
    module even though `sys.modules["contextpulse_touch.listeners"]` was
    just popped -- confirmed live: run in isolation this fixture worked, run
    after test_integration.py it silently rebound to the mock and two
    assertions on real Ctrl+V detection failed. `importlib.import_module`
    plus deleting the parent's attribute forces a genuine fresh import.
    """
    touched = (
        "pynput", "pynput.keyboard", "pynput.mouse",
        "contextpulse_touch.listeners",
    )
    saved = {k: sys.modules.get(k) for k in touched}
    for k in touched:
        sys.modules.pop(k, None)

    import contextpulse_touch
    had_attr = hasattr(contextpulse_touch, "listeners")
    if had_attr:
        delattr(contextpulse_touch, "listeners")

    import pynput.keyboard as real_kb  # the genuine installed dependency

    real_listeners = importlib.import_module("contextpulse_touch.listeners")
    assert real_listeners.kb is real_kb, (
        "fixture bug: contextpulse_touch.listeners rebound to a stale kb "
        "module -- these tests would silently exercise the mock, not the "
        "real pynput equality semantics they exist to check"
    )

    yield real_listeners, real_kb

    for k in touched:
        sys.modules.pop(k, None)
    for k, v in saved.items():
        if v is not None:
            sys.modules[k] = v
    if had_attr:
        contextpulse_touch.listeners = saved["contextpulse_touch.listeners"]
    elif hasattr(contextpulse_touch, "listeners"):
        delattr(contextpulse_touch, "listeners")


def test_old_from_char_comparison_never_matches_real_ctrl_v_keycode(real_listeners_module):
    """Pins the underlying platform fact that made the bug possible: pynput's
    real Ctrl+V KeyCode is never equal to KeyCode.from_char('v'). If this
    ever starts passing, pynput's behavior changed and the vk-based fix in
    listeners.py may no longer be necessary (or may need revisiting)."""
    _listeners, kb = real_listeners_module
    real_ctrl_v_keycode = kb.KeyCode(vk=86, char="\x16")
    assert real_ctrl_v_keycode != kb.KeyCode.from_char("v")


def test_ctrl_v_triggers_on_paste(real_listeners_module, monkeypatch):
    """The actual regression: Ctrl+V (ctrl_l held, then the real V KeyCode)
    must schedule and fire on_paste with the clipboard contents."""
    listeners, kb = real_listeners_module
    monkeypatch.setattr(listeners, "_get_clipboard_text", lambda: "clipboard contents")

    pasted: list[str] = []
    listener = listeners.KeyboardListener(on_paste=lambda text: pasted.append(text))

    listener._on_press(kb.Key.ctrl_l)
    listener._on_press(kb.KeyCode(vk=86, char="\x16"))  # real Ctrl+V, as delivered
    time.sleep(0.3)  # on_paste fires via a 0.1s Timer thread, not synchronously

    assert pasted == ["clipboard contents"]


def test_ctrl_shift_v_terminal_chord_also_triggers_on_paste(real_listeners_module, monkeypatch):
    """paster.py sends Ctrl+Shift+V (not Ctrl+V) when the focused window is a
    terminal. Verified live: Shift does not change the control-character
    mapping, so this must be detected the same way as plain Ctrl+V."""
    listeners, kb = real_listeners_module
    monkeypatch.setattr(listeners, "_get_clipboard_text", lambda: "terminal paste")

    pasted: list[str] = []
    listener = listeners.KeyboardListener(on_paste=lambda text: pasted.append(text))

    listener._on_press(kb.Key.ctrl_l)
    listener._on_press(kb.Key.shift)
    listener._on_press(kb.KeyCode(vk=86, char="\x16"))
    time.sleep(0.3)

    assert pasted == ["terminal paste"]


def test_plain_v_without_ctrl_does_not_trigger_paste(real_listeners_module, monkeypatch):
    """Regression guard on the fix itself: gating on vk==86 alone (without
    also requiring ctrl_l held) would misfire on ordinary typing, since a
    plain 'v' keypress is ALSO vk=86 (char='v' instead of '\\x16')."""
    listeners, kb = real_listeners_module
    monkeypatch.setattr(listeners, "_get_clipboard_text", lambda: "should not be read")

    pasted: list[str] = []
    chars: list[str | None] = []
    listener = listeners.KeyboardListener(
        on_paste=lambda text: pasted.append(text),
        on_char=lambda ch, is_bs, is_sel: chars.append(ch),
    )

    listener._on_press(kb.KeyCode(vk=86, char="v"))
    time.sleep(0.3)

    assert pasted == []
    assert chars == ["v"]


def test_ctrl_held_but_different_key_does_not_trigger_paste(real_listeners_module, monkeypatch):
    """Ctrl+C (copy) must not be mistaken for Ctrl+V."""
    listeners, kb = real_listeners_module
    monkeypatch.setattr(listeners, "_get_clipboard_text", lambda: "should not be read")

    pasted: list[str] = []
    listener = listeners.KeyboardListener(on_paste=lambda text: pasted.append(text))

    listener._on_press(kb.Key.ctrl_l)
    listener._on_press(kb.KeyCode(vk=67, char="\x03"))  # Ctrl+C: vk=67, char=ETX
    time.sleep(0.3)

    assert pasted == []
