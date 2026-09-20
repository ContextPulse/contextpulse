# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""T19 from .internal/audit-2026-09-19/spec-config-unification.md section 5.

`get_touch_config()` was already wired to `load_config()`, which is why the
four touch controls looked alive. They were not: the four `touch_*` keys were
absent from `_DEFAULTS`, so `cfg.get(key, <fallback>)` always missed and every
value came from the module constant in this package. A user who set
`touch_burst_timeout` in the Settings dialog wrote it to config.json and the
daemon read 1.5 anyway.

These tests go through the REAL config file -- save_config to an isolated
%APPDATA%, then call the real get_touch_config with nothing patched -- because
patching get_touch_config (which every other touch test does, correctly, to
avoid touching the user's file) cannot see this class of defect at all.
"""

from __future__ import annotations

import pytest
from contextpulse_core.config import _DEFAULTS, _ENV_MAP, save_config
from contextpulse_touch.config import get_touch_config

# The `isolated_config` fixture is registered by this package's conftest.py,
# not imported here: importing it into a test MODULE puts the name at module
# scope, where every test taking it as a parameter shadows it and ruff flags
# F811. conftest is where contextpulse_core.testing's own docstring points.

# key in config.json -> key in the dict get_touch_config returns
KEY_PAIRS = [
    ("touch_burst_timeout", "burst_timeout", 0.7, "0.9"),
    ("touch_correction_window", "correction_window", 3.5, "4.25"),
    ("touch_min_burst_chars", "min_burst_chars", 8, "11"),
    ("touch_mouse_debounce", "mouse_debounce", 0.25, "0.5"),
]


class TestTouchKeysAreDeclaredOnce:
    def test_all_four_keys_are_in_defaults(self):
        for cfg_key, _, _, _ in KEY_PAIRS:
            assert cfg_key in _DEFAULTS, f"{cfg_key} is not declared in _DEFAULTS"

    def test_all_four_keys_have_an_env_var(self):
        for cfg_key, _, _, _ in KEY_PAIRS:
            assert cfg_key in _ENV_MAP, f"{cfg_key} has no CONTEXTPULSE_* variable"

    def test_the_module_constants_are_gone(self):
        """They were the value the daemon actually used, from 2 layers down."""
        import contextpulse_touch.config as touch_cfg
        for name in ("BURST_TIMEOUT", "CORRECTION_WINDOW", "MIN_BURST_CHARS", "MOUSE_DEBOUNCE"):
            assert not hasattr(touch_cfg, name), (
                f"contextpulse_touch.config.{name} still exists -- it is a second "
                "declaration of a key contextpulse_core.config._DEFAULTS owns"
            )

    def test_the_non_tunable_constant_is_kept(self):
        """Positive control: not everything in the module was swept away.

        CORRECTION_CONFIDENCE_THRESHOLD has no config key, no env var and no
        Settings control, so it is deliberately still a module constant.
        """
        import contextpulse_touch.config as touch_cfg
        assert touch_cfg.CORRECTION_CONFIDENCE_THRESHOLD == 0.7


class TestTouchConfigReadsTheRealFile:
    @pytest.mark.parametrize(("cfg_key", "out_key", "saved", "env_raw"), KEY_PAIRS)
    def test_a_saved_value_reaches_get_touch_config(
        self, isolated_config, cfg_key, out_key, saved, env_raw
    ):
        assert get_touch_config()[out_key] == _DEFAULTS[cfg_key]  # baseline
        save_config({cfg_key: saved})
        assert get_touch_config()[out_key] == saved

    @pytest.mark.parametrize(("cfg_key", "out_key", "saved", "env_raw"), KEY_PAIRS)
    def test_env_beats_the_saved_value(
        self, isolated_config, monkeypatch, cfg_key, out_key, saved, env_raw
    ):
        """The CONTEXTPULSE_TOUCH_* variables must survive the move.

        They used to be read here, in the `cfg.get(key, <fallback>)` default.
        Once the keys are declared in _DEFAULTS that fallback is unreachable,
        so leaving the old code in place would have silently killed all four
        variables. They live in _ENV_MAP now; this is the test that says so.
        """
        save_config({cfg_key: saved})
        monkeypatch.setenv(_ENV_MAP[cfg_key], env_raw)
        expected = type(_DEFAULTS[cfg_key])(env_raw)
        assert get_touch_config()[out_key] == expected

    def test_a_change_takes_effect_without_reimporting(self, isolated_config):
        """Set, degrade, restore -- the same value must give the same answer.

        The module used to freeze these at import; an idempotence round trip
        is what distinguishes a live read from a cached one.
        """
        save_config({"touch_burst_timeout": 0.7})
        assert get_touch_config()["burst_timeout"] == 0.7
        save_config({"touch_burst_timeout": 2.5})
        assert get_touch_config()["burst_timeout"] == 2.5
        save_config({"touch_burst_timeout": 0.7})
        assert get_touch_config()["burst_timeout"] == 0.7

    def test_returns_exactly_the_four_keys(self, isolated_config):
        assert set(get_touch_config()) == {p[1] for p in KEY_PAIRS}
