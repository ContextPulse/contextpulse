# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app must state the same Pro price as contextpulse.ai.

The site sells Pro as a $29 one-time founding licence (2026-11-10). The licence
dialog and the settings window carried the old $49/yr and $249 lifetime prices,
so a buyer who paid $29 then saw a subscription price inside the app.
"""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "contextpulse_core"
FILES = ["license_dialog.py", "settings.py"]


@pytest.mark.parametrize("name", FILES)
def test_price_strings_state_founding_licence(name: str) -> None:
    text = (SRC / name).read_text(encoding="utf-8")
    assert "$29 one-time" in text, f"{name} does not state the $29 one-time price"


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("stale", ["$49", "$249", "/yr", "Lifetime"])
def test_price_strings_drop_old_prices(name: str, stale: str) -> None:
    text = (SRC / name).read_text(encoding="utf-8")
    assert stale not in text, f"{name} still carries the old price text {stale!r}"
