# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for scripts/probe_usage_report.py (cp-savegate-attribution-instrument).

scripts/ is not a package (no __init__.py); loaded by file path, same pattern
as tests/test_retention_sweep.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from contextpulse_core import probe

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "probe_usage_report.py"


def _load_report_module():
    spec = importlib.util.spec_from_file_location("probe_usage_report_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="[]"):
        self.returncode = returncode
        self.stdout = stdout


class TestConfirmedSaveCount:
    def test_counts_only_phase0_save_category_rows(self):
        mod = _load_report_module()
        rows = [
            {"category": "phase0-save", "content": "PHASE0-SAVE | tool=facts_about ..."},
            {"category": "phase0-save", "content": "PHASE0-SAVE | tool=context_at ..."},
            {"category": "", "content": "unrelated observation"},
        ]
        import json

        with patch("subprocess.run", return_value=_FakeCompleted(0, json.dumps(rows))):
            assert mod.confirmed_save_count() == 2

    def test_journal_failure_returns_none_not_zero(self):
        mod = _load_report_module()
        with patch("subprocess.run", return_value=_FakeCompleted(returncode=1, stdout="")):
            assert mod.confirmed_save_count() is None

    def test_unparseable_json_returns_none_not_zero(self):
        mod = _load_report_module()
        with patch("subprocess.run", return_value=_FakeCompleted(0, "not json")):
            assert mod.confirmed_save_count() is None

    def test_subprocess_raising_returns_none_not_zero(self):
        mod = _load_report_module()
        with patch("subprocess.run", side_effect=OSError("no such file")):
            assert mod.confirmed_save_count() is None


class TestFormatReport:
    def test_zero_calls_reads_as_nothing_was_watching(self):
        mod = _load_report_module()
        summary = {"total_calls": 0, "calls_with_hits": 0, "by_tool": {}}
        text = mod.format_report(summary, saves=0, probe_db=Path("x.db"))
        assert "nothing was watching" in text

    def test_calls_with_zero_saves_reads_as_meaningful_zero(self):
        mod = _load_report_module()
        summary = {
            "total_calls": 5,
            "calls_with_hits": 2,
            "by_tool": {"facts_about": {"calls": 5, "with_hits": 2}},
        }
        text = mod.format_report(summary, saves=0, probe_db=Path("x.db"))
        assert "this 0 is now meaningful" in text
        assert "5 real tool call(s)" in text

    def test_unverifiable_saves_never_prints_a_zero(self):
        mod = _load_report_module()
        summary = {"total_calls": 3, "calls_with_hits": 1, "by_tool": {}}
        text = mod.format_report(summary, saves=None, probe_db=Path("x.db"))
        assert "COULD NOT VERIFY" in text
        assert "Confirmed saves (journal, category=phase0-save): 0" not in text


class TestMainEndToEnd:
    def test_missing_probe_db_reports_genuine_zero_and_exits_clean(self, tmp_path, capsys):
        mod = _load_report_module()
        missing = tmp_path / "does_not_exist" / "probe.db"
        rc = mod.main(["--probe-db", str(missing)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "never been called" in out

    def test_real_probe_db_with_usage_rows_reports_them(self, tmp_path, capsys):
        mod = _load_report_module()
        db = tmp_path / "probe.db"
        conn = probe.connect_probe(db)
        probe.record_usage(conn, "facts_about", "Foo", hit_count=2)
        probe.record_usage(conn, "context_at", "now +/-30min", hit_count=0)
        conn.close()

        with patch("subprocess.run", return_value=_FakeCompleted(0, "[]")):
            rc = mod.main(["--probe-db", str(db)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Tool calls total:      2" in out
        assert "facts_about: 1 calls, 1 with hits" in out
        assert "context_at: 1 calls, 0 with hits" in out
        assert "Confirmed saves (journal, category=phase0-save): 0" in out
