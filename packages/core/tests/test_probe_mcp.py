# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for contextpulse_core.probe_mcp — the facts_about/context_at MCP tools.

Covers cp-savegate-attribution-instrument: every real call to either tool must
write an automatic tool_usage row (via probe.record_usage), independent of
whether the caller separately runs probe_save.py, and a failure in usage
logging must never break the tool's own return value (recall stays primary;
instrumentation is best-effort).

FastMCP's @mcp_app.tool() decorator leaves the underlying function directly
callable (confirmed via packages/screen/tests/test_mcp_tools.py's existing
convention: mcp_server.get_monitor_summary() is called directly there too).
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

from contextpulse_core import probe, probe_mcp


def _usage_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT tool, query, hit_count FROM tool_usage").fetchall()
    conn.close()
    return [tuple(r) for r in rows]


class TestFactsAboutRecordsUsage:
    def test_no_hits_still_logs_a_row(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        probe_mcp.facts_about("NothingLikeThisExists")
        assert _usage_rows(db) == [("facts_about", "NothingLikeThisExists", 0)]

    def test_hits_log_the_real_count(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        conn = probe.connect_probe(db)
        probe.write_facts(
            conn,
            [
                {"entity": "AcmeBot", "fact": "one", "valid_from": 1.0, "source_event_ids": []},
                {"entity": "AcmeBot", "fact": "two", "valid_from": 2.0, "source_event_ids": []},
            ],
        )
        conn.close()
        probe_mcp.facts_about("AcmeBot")
        assert _usage_rows(db) == [("facts_about", "AcmeBot", 2)]

    def test_two_calls_accumulate_two_rows(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        probe_mcp.facts_about("First")
        probe_mcp.facts_about("Second")
        assert len(_usage_rows(db)) == 2


class TestContextAtRecordsUsage:
    def test_now_logs_a_row_with_window_in_query(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        probe_mcp.context_at("now", window_minutes=15)
        rows = _usage_rows(db)
        assert len(rows) == 1
        tool, query, hit_count = rows[0]
        assert tool == "context_at"
        assert "15" in query
        assert hit_count == 0

    def test_unparseable_time_does_not_log_a_usage_row(self, tmp_path, monkeypatch):
        """An unparseable 'when' never reaches probe.db at all — nothing to log."""
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        result = probe_mcp.context_at("not-a-real-time")
        assert "Could not parse" in result
        assert not db.exists()


class TestUsageLoggingNeverBreaksRecall:
    def test_facts_about_still_returns_when_usage_logging_raises(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        with patch.object(probe, "record_usage", side_effect=RuntimeError("disk full")):
            result = probe_mcp.facts_about("Anything")
        assert "No probe facts about 'Anything'" in result

    def test_context_at_still_returns_when_usage_logging_raises(self, tmp_path, monkeypatch):
        db = tmp_path / "probe.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(db))
        with patch.object(probe, "record_usage", side_effect=RuntimeError("disk full")):
            result = probe_mcp.context_at(str(time.time()))
        assert "No probe facts within" in result
