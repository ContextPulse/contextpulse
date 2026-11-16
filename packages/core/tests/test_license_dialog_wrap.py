# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Every sentence in the licence dialog must wrap inside the dialog.

Found 2026-11-15 on a clean Windows machine (installer smoke run 4): the
trial line ran off the right edge of the 500-pixel dialog and read
"... stay free for", because a tk.Label without ``wraplength`` never wraps.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from contextpulse_core import license_dialog

DIALOG_WIDTH = 500
FRAME_PADX = 30
CONTENT_WIDTH = DIALOG_WIDTH - 2 * FRAME_PADX
# Shorter than this fits on one line at the dialog's fonts; longer needs wrapping.
SHORT_TEXT = 45


@pytest.mark.parametrize("days_left", [12, 1, 0])
def test_every_long_label_wraps_inside_the_dialog(monkeypatch, days_left):
    created: list[tuple[str, dict]] = []

    def fake_label(parent, text, **kwargs):
        created.append((text, kwargs))
        return MagicMock()

    # conftest maps tk.Frame to the MagicMock CLASS, which reads a positional
    # parent as a spec; an instance accepts any call.
    monkeypatch.setattr(license_dialog, "tk", MagicMock())
    monkeypatch.setattr(license_dialog, "ttk", MagicMock())
    monkeypatch.setattr(license_dialog.gui_theme, "create_dialog", MagicMock())
    monkeypatch.setattr(license_dialog.gui_theme, "make_label", fake_label)
    monkeypatch.setattr(license_dialog, "get_trial_days_remaining", lambda: days_left)

    license_dialog.show_nag_dialog()

    long_labels = [(t, kw) for t, kw in created if len(t) > SHORT_TEXT]
    assert any("trial" in t.lower() for t, _ in long_labels), "trial line not exercised"
    for text, kwargs in long_labels:
        wrap = kwargs.get("wraplength")
        assert wrap and 0 < wrap <= CONTENT_WIDTH, f"label would be clipped: {text!r}"
