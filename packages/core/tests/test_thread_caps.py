# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for _thread_caps — caps C-extension thread pools at import time."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from contextpulse_core import _thread_caps


@pytest.fixture(autouse=True)
def clean_cap_env() -> Iterator[None]:
    """Isolate every test from an ambient CONTEXTPULSE_CPU_THREADS.

    David's user environment sets CONTEXTPULSE_CPU_THREADS=8, which made
    get_cap() return 8 and broke the two tests that assert the default cap
    of 2. Tests that want an override set it explicitly via patch.dict.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONTEXTPULSE_CPU_THREADS", None)
        yield


class TestGetCap:
    def test_default_cap_is_2(self):
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

    def test_warns_when_override_active(self, caplog):
        """Regression: cp-thread-count-157-above-baseline.

        A stray persistent CONTEXTPULSE_CPU_THREADS=8 sat in David's Windows
        user environment for months, quietly overriding the documented default
        of 2 on every ContextPulse process, undetected until a psutil-based
        monitor caught the live daemon near the original 163-thread incident
        baseline. apply_caps() must log so this class of override is
        diagnosable from the daemon's own logs, not just an ad-hoc process probe.
        """
        target: dict[str, str] = {}
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "8"}):
            with caplog.at_level("WARNING", logger="contextpulse_core._thread_caps"):
                _thread_caps.apply_caps(target)
        assert any("overridden to 8" in r.message for r in caplog.records)

    def test_no_warning_at_default_cap(self, caplog):
        target: dict[str, str] = {}
        with caplog.at_level("WARNING", logger="contextpulse_core._thread_caps"):
            _thread_caps.apply_caps(target)
        assert caplog.records == []

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


class TestGetWhisperCap:
    """Whisper gets its own, larger budget than the idle-pool cap.

    Measured 2026-09-20 on a 75.0s dictation (Whisper small/int8, CPU,
    AMD Ryzen AI MAX+ 395, 32 logical cores), median of 3 runs:

        cpu_threads=2  ->  8.74s   (ratio 0.117)   10 OS threads
        cpu_threads=6  ->  6.32s   (ratio 0.084)   18 OS threads
        cpu_threads=8  ->  6.33s   (ratio 0.084)   22 OS threads

    6 is the knee: 28% faster than 2, and 8 buys nothing more while
    costing 4 further threads. Transcript was byte-identical (1176 chars)
    at every setting, so this trades threads for latency and nothing else.
    """

    @pytest.fixture(autouse=True)
    def clean_whisper_env(self) -> Iterator[None]:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTPULSE_WHISPER_THREADS", None)
            yield

    def test_default_is_6(self):
        assert _thread_caps.get_whisper_cap() == 6

    def test_is_larger_than_the_idle_pool_cap(self):
        # The whole point: the 163-thread incident was four libraries each
        # allocating cpu_count() IDLE workers. Whisper is the one pool doing
        # user-visible latency-critical work, so it must not inherit the
        # idle-pool number.
        assert _thread_caps.get_whisper_cap() > _thread_caps.get_cap()

    def test_override_via_env_var(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_WHISPER_THREADS": "4"}):
            assert _thread_caps.get_whisper_cap() == 4

    def test_invalid_override_falls_back_to_default(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_WHISPER_THREADS": "not-a-number"}):
            assert _thread_caps.get_whisper_cap() == 6

    def test_minimum_is_1(self):
        for raw in ("0", "-3"):
            with patch.dict(os.environ, {"CONTEXTPULSE_WHISPER_THREADS": raw}):
                assert _thread_caps.get_whisper_cap() == 1

    def test_does_not_read_the_cpu_threads_var(self):
        # Two distinct knobs. CONTEXTPULSE_CPU_THREADS=8 sat as a stray
        # persistent Windows user env var for months (see apply_caps'
        # override warning); the whisper budget must not be steerable by
        # that same stale var.
        with patch.dict(os.environ, {"CONTEXTPULSE_CPU_THREADS": "16"}):
            assert _thread_caps.get_whisper_cap() == 6


class TestWhisperCapDoesNotLeakIntoIdlePools:
    """What could have BROKEN, not just what was added.

    Raising Whisper's budget must leave OMP/MKL/OPENBLAS/NUMEXPR at the
    idle-pool cap. If the new number leaked into apply_caps() it would
    re-inflate exactly the baseline the 2026-04-29 incident was about,
    and every one of the tests above would still pass.
    """

    def test_apply_caps_still_writes_the_idle_cap_not_the_whisper_cap(self):
        target: dict[str, str] = {}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTEXTPULSE_CPU_THREADS", None)
            os.environ.pop("CONTEXTPULSE_WHISPER_THREADS", None)
            _thread_caps.apply_caps(target)
        assert target == {
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "NUMEXPR_NUM_THREADS": "2",
        }

    def test_whisper_override_does_not_move_the_idle_pools(self):
        target: dict[str, str] = {}
        with patch.dict(os.environ, {"CONTEXTPULSE_WHISPER_THREADS": "12"}):
            os.environ.pop("CONTEXTPULSE_CPU_THREADS", None)
            _thread_caps.apply_caps(target)
        assert set(target.values()) == {"2"}
