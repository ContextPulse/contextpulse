# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for adaptive auto-capture interval.

Background: a fixed 5s capture interval was driving 20-30% sustained CPU
even when the user wasn't at the keyboard. The pixel-diff buffer plus
OCR skipping (Phase A2) cut some of that, but the capture+diff+save work
still runs every 5s. Extending the interval to 30s during idle drops
that to ~5%, while a snap-back to 5s on the next event keeps active
sessions responsive.
"""

from __future__ import annotations


class TestEffectiveInterval:
    """The pure decision function.

    Public contract:
      - When (now - last_active_time) < idle_threshold: return base_interval
      - When (now - last_active_time) >= idle_threshold: return idle_interval
      - At exactly the threshold: switch to idle (>= semantics)
    """

    def test_recent_activity_uses_base_interval(self):
        from contextpulse_sight.app import effective_interval

        # Last active 10s ago, threshold 60s → still active
        assert effective_interval(
            now=100.0, last_active_time=90.0,
            base_interval=5.0, idle_interval=30.0, idle_threshold=60.0,
        ) == 5.0

    def test_no_activity_for_long_uses_idle_interval(self):
        from contextpulse_sight.app import effective_interval

        # Last active 200s ago, threshold 60s → idle
        assert effective_interval(
            now=300.0, last_active_time=100.0,
            base_interval=5.0, idle_interval=30.0, idle_threshold=60.0,
        ) == 30.0

    def test_at_threshold_switches_to_idle(self):
        # >= semantics: a session exactly at the threshold is considered idle.
        from contextpulse_sight.app import effective_interval

        assert effective_interval(
            now=160.0, last_active_time=100.0,
            base_interval=5.0, idle_interval=30.0, idle_threshold=60.0,
        ) == 30.0

    def test_just_below_threshold_stays_active(self):
        from contextpulse_sight.app import effective_interval

        assert effective_interval(
            now=159.99, last_active_time=100.0,
            base_interval=5.0, idle_interval=30.0, idle_threshold=60.0,
        ) == 5.0

    def test_idle_interval_lower_than_base_returns_base(self):
        # Defensive: if a misconfigured idle_interval is LOWER than base,
        # we should fall back to base — extending interval only makes sense.
        from contextpulse_sight.app import effective_interval

        assert effective_interval(
            now=300.0, last_active_time=100.0,
            base_interval=5.0, idle_interval=2.0, idle_threshold=60.0,
        ) == 5.0


class TestAutoIntervalIdleConfig:
    """The three interval keys, as the daemon resolves them.

    These tests used to reload contextpulse_sight.config and read its module
    constants. That module no longer declares a tunable: the keys live in
    contextpulse_core.config, and what matters is what the capture loop gets
    from them, which is covered end-to-end in
    test_app_config_controls.py::TestIntervalsAreLive (including the loop
    honouring a change with no restart). Kept here, at the declaration level,
    because this file is where someone looks for the idle-interval contract.
    """

    def test_defaults(self, isolated_config):
        from contextpulse_core.config import load_config

        cfg = load_config()
        assert cfg["auto_interval"] == 5
        assert cfg["auto_interval_idle"] == 30
        assert cfg["auto_idle_threshold"] == 60

    def test_saved_values_win_over_defaults(self, isolated_config):
        from contextpulse_core.config import load_config, save_config

        save_config({"auto_interval_idle": 120, "auto_idle_threshold": 300})
        cfg = load_config()
        assert cfg["auto_interval_idle"] == 120
        assert cfg["auto_idle_threshold"] == 300

    def test_env_wins_over_a_saved_value(self, isolated_config, monkeypatch):
        from contextpulse_core.config import load_config, save_config

        save_config({"auto_interval_idle": 120})
        monkeypatch.setenv("CONTEXTPULSE_AUTO_INTERVAL_IDLE", "45")
        assert load_config()["auto_interval_idle"] == 45

    def test_idle_interval_clamped_above_zero(self, isolated_config, monkeypatch):
        # 0 or negative would mean "always re-capture instantly", which is
        # a footgun. Floor at 1 like other interval configs. The clamp now
        # applies to a hand-edited config.json too, not only to env.
        from contextpulse_core.config import load_config, save_config

        monkeypatch.setenv("CONTEXTPULSE_AUTO_INTERVAL_IDLE", "0")
        assert load_config()["auto_interval_idle"] >= 1
        monkeypatch.delenv("CONTEXTPULSE_AUTO_INTERVAL_IDLE")
        save_config({"auto_interval_idle": 0})
        assert load_config()["auto_interval_idle"] >= 1
