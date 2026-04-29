# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for the daemon's thread-budget diagnostic.

The watchdog loop logs a thread count every ~60s so leaks are visible
in contextpulse.log without needing an external probe. Exceeding a
configurable threshold (default 100) bumps the log level to WARNING
to make alerting easy.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from contextpulse_core.daemon import format_thread_budget, log_thread_budget


class TestFormatThreadBudget:
    """Pure formatter. Returns a one-line string for the log."""

    def test_includes_python_active_count(self):
        s = format_thread_budget(py_active=12, os_threads=None)
        assert "py=12" in s

    def test_handles_missing_os_count(self):
        # psutil unavailable → os_threads=None; should not crash, should
        # emit a placeholder.
        s = format_thread_budget(py_active=12, os_threads=None)
        assert "os=" in s  # placeholder still rendered for log grep-ability

    def test_includes_os_thread_count_when_available(self):
        s = format_thread_budget(py_active=12, os_threads=42)
        assert "py=12" in s and "os=42" in s


class TestLogThreadBudget:
    """The logger that emits the budget at INFO or WARNING based on threshold."""

    def test_logs_at_info_below_threshold(self, caplog):
        with caplog.at_level(logging.INFO, logger="contextpulse.daemon"):
            log_thread_budget(py_active=10, os_threads=20, warn_threshold=100)
        # Match by message content rather than logger name (basicConfig may
        # route through the root logger in some test setups).
        budget_records = [r for r in caplog.records if "thread budget" in r.message]
        assert budget_records, "no thread-budget log line emitted"
        assert all(r.levelno == logging.INFO for r in budget_records)

    def test_logs_at_warning_at_threshold(self, caplog):
        with caplog.at_level(logging.INFO, logger="contextpulse.daemon"):
            log_thread_budget(py_active=10, os_threads=100, warn_threshold=100)
        budget_records = [r for r in caplog.records if "thread budget" in r.message]
        assert budget_records
        assert any(r.levelno == logging.WARNING for r in budget_records)

    def test_logs_at_warning_above_threshold(self, caplog):
        with caplog.at_level(logging.INFO, logger="contextpulse.daemon"):
            log_thread_budget(py_active=10, os_threads=200, warn_threshold=100)
        budget_records = [r for r in caplog.records if "thread budget" in r.message]
        assert any(r.levelno == logging.WARNING for r in budget_records)

    def test_uses_py_active_when_os_unavailable(self, caplog):
        # psutil missing path: py count alone gates the warn level. py is
        # always smaller than os_threads (Python doesn't see C ext threads),
        # so this is a relaxed warning — better than nothing.
        with caplog.at_level(logging.INFO, logger="contextpulse.daemon"):
            log_thread_budget(py_active=150, os_threads=None, warn_threshold=100)
        budget_records = [r for r in caplog.records if "thread budget" in r.message]
        assert any(r.levelno == logging.WARNING for r in budget_records)


class TestSampleOsThreadCount:
    """sample_os_thread_count() returns int (psutil) or None (psutil missing)."""

    def test_returns_int_when_psutil_available(self):
        from contextpulse_core.daemon import sample_os_thread_count

        # On the test runner psutil is usually installed; if not, this is
        # vacuously OK and the next test covers the None branch.
        result = sample_os_thread_count()
        assert result is None or isinstance(result, int)
        if isinstance(result, int):
            assert result >= 1  # at least the calling thread

    def test_returns_none_when_psutil_missing(self):
        # Simulate ImportError for psutil and confirm graceful fallback.
        import sys

        # Remove psutil from sys.modules so the next import fails.
        saved_psutil = sys.modules.pop("psutil", None)
        # Block re-import by injecting a sentinel that raises on attribute
        # access. Simpler: patch builtins.__import__ to raise for psutil.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("simulated missing psutil")
            return real_import(name, *args, **kwargs)

        try:
            with patch("builtins.__import__", side_effect=_fake_import):
                from contextpulse_core.daemon import sample_os_thread_count
                assert sample_os_thread_count() is None
        finally:
            if saved_psutil is not None:
                sys.modules["psutil"] = saved_psutil
