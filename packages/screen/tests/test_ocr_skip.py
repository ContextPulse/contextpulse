# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for the OCR-skip gate that bypasses OCR on near-identical frames.

Background: the auto-capture loop fires every 5 s on every monitor; with a
0.5% buffer change-threshold, idle screens still produce 2-3 OCR runs per
cycle (cursor blinks, clock ticks). OCR takes 0.2-0.7 s per frame, which
sustained 20-40% of one core. Gating OCR on a higher diff threshold
(default 5%) skips that work for trivially-changed frames while keeping
OCR on real content changes (window switch, scroll, edit).
"""

from __future__ import annotations


class TestShouldRunOcr:
    """The pure decision function.

    Public contract:
      - True when force_ocr is True (event-driven captures bypass the gate)
      - True when diff_pct >= threshold (real content change)
      - False when diff_pct < threshold AND force_ocr is False
      - First-frame case (diff_pct == 100.0) always passes
    """

    def test_force_ocr_true_always_runs(self):
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=0.0, force_ocr=True, threshold=5.0) is True
        assert should_run_ocr(diff_pct=0.1, force_ocr=True, threshold=5.0) is True

    def test_high_diff_runs_ocr(self):
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=10.0, force_ocr=False, threshold=5.0) is True
        assert should_run_ocr(diff_pct=50.0, force_ocr=False, threshold=5.0) is True
        assert should_run_ocr(diff_pct=100.0, force_ocr=False, threshold=5.0) is True

    def test_low_diff_skips_ocr(self):
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=0.0, force_ocr=False, threshold=5.0) is False
        assert should_run_ocr(diff_pct=2.5, force_ocr=False, threshold=5.0) is False
        assert should_run_ocr(diff_pct=4.99, force_ocr=False, threshold=5.0) is False

    def test_diff_at_threshold_runs_ocr(self):
        # >= semantics: a frame at exactly threshold passes the gate.
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=5.0, force_ocr=False, threshold=5.0) is True

    def test_threshold_zero_runs_ocr_always(self):
        # Operator escape hatch: setting threshold=0 disables the gate.
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=0.0, force_ocr=False, threshold=0.0) is True
        assert should_run_ocr(diff_pct=0.001, force_ocr=False, threshold=0.0) is True

    def test_first_frame_runs_ocr(self):
        # buffer.add returns diff_pct=100.0 for the first frame on a monitor.
        from contextpulse_sight.app import should_run_ocr

        assert should_run_ocr(diff_pct=100.0, force_ocr=False, threshold=5.0) is True


class TestOcrDiffThresholdConfig:
    """Config plumbing: OCR_DIFF_THRESHOLD is loaded from env with a default."""

    def test_default_value_is_5_percent(self, monkeypatch):
        # Ensure no override leaks in from the test runner's env.
        monkeypatch.delenv("CONTEXTPULSE_OCR_DIFF_THRESHOLD", raising=False)
        # Reload config module to pick up the env state.
        import importlib

        from contextpulse_sight import config as config_mod
        importlib.reload(config_mod)
        assert config_mod.OCR_DIFF_THRESHOLD == 5.0

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_OCR_DIFF_THRESHOLD", "12.5")
        import importlib

        from contextpulse_sight import config as config_mod
        importlib.reload(config_mod)
        assert config_mod.OCR_DIFF_THRESHOLD == 12.5

    def test_negative_clamped_to_zero(self, monkeypatch):
        # Sanity guard: a misconfigured negative threshold should clamp to 0,
        # which means "always OCR" — safe fallback to prior behavior.
        monkeypatch.setenv("CONTEXTPULSE_OCR_DIFF_THRESHOLD", "-1")
        import importlib

        from contextpulse_sight import config as config_mod
        importlib.reload(config_mod)
        assert config_mod.OCR_DIFF_THRESHOLD == 0.0
