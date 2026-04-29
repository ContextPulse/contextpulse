# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for _thread_caps — caps C-extension thread pools at import time."""

from __future__ import annotations

import os
from unittest.mock import patch

from contextpulse_core import _thread_caps


class TestGetCap:
    def test_default_cap_is_2(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTPULSE_CPU_THREADS", None)
            assert _thread_caps.get_cap() == 2

    def test_override_via_env_var(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "4"}):
            assert _thread_caps.get_cap() == 4

    def test_invalid_override_falls_back_to_default(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "not-a-number"}):
            assert _thread_caps.get_cap() == 2

    def test_minimum_is_1(self):
        # 0 or negative would mean "use system default" in some libs which
        # defeats the purpose of capping. Floor at 1.
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "0"}):
            assert _thread_caps.get_cap() == 1
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "-3"}):
            assert _thread_caps.get_cap() == 1


class TestApplyCaps:
    def test_sets_all_four_vars_when_unset(self):
        target: dict[str, str] = {}
        applied = _thread_caps.apply_caps(target)
        assert set(applied.keys()) == {
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        }
        assert applied["OMP_NUM_THREADS"] == "2"
        assert target["OMP_NUM_THREADS"] == "2"

    def test_respects_preexisting_value(self):
        # User has explicitly set OMP_NUM_THREADS=8 in their env — do not clobber.
        target = {"OMP_NUM_THREADS": "8"}
        applied = _thread_caps.apply_caps(target)
        assert "OMP_NUM_THREADS" not in applied  # not newly applied
        assert target["OMP_NUM_THREADS"] == "8"  # original preserved
        # Other vars still get the cap applied
        assert target["MKL_NUM_THREADS"] == "2"

    def test_uses_override_value(self):
        target: dict[str, str] = {}
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "4"}):
            _thread_caps.apply_caps(target)
        assert target["OMP_NUM_THREADS"] == "4"

    def test_module_import_applied_caps_to_real_environ(self):
        # The act of importing _thread_caps at the top of this file should
        # have populated these in os.environ already.
        for var in (
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            assert var in os.environ, f"{var} not set after _thread_caps import"
