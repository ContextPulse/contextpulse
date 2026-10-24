# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for the Phase-1 knowledge-graph MCP tools + the mcp_unified gate.

Covers: the four recall tools over a real backfilled knowledge.db, graceful
behaviour when the db is absent/empty, time parsing, and that
mcp_unified._register_all serves exactly one of {probe, knowledge} depending on
config.knowledge_enabled (they share facts_about / context_at names).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from contextpulse_knowledge import bridge, mcp_tools
from contextpulse_knowledge.cp_core import CONTEXT_PREDICATES
from contextpulse_knowledge.store_sqlite import KnowledgeStore

BASE = 1_750_000_000.0

_EVENTS_DDL = """
CREATE TABLE events (
  event_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, modality TEXT NOT NULL,
  event_type TEXT NOT NULL, app_name TEXT DEFAULT '', window_title TEXT DEFAULT '',
  monitor_index INTEGER DEFAULT 0, payload TEXT NOT NULL, correlation_id TEXT,
  attention_score REAL DEFAULT 0.0, cognitive_load REAL DEFAULT 0.0,
  created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""


def _row(event_id, ts, etype, *, app="", title="", payload=None, modality="system"):
    return {
        "event_id": event_id, "timestamp": ts, "modality": modality,
        "event_type": etype, "app_name": app, "window_title": title,
        "monitor_index": 0, "payload": json.dumps(payload or {}),
        "correlation_id": None, "attention_score": 0.0, "cognitive_load": 0.0,
    }


def _make_events_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(_EVENTS_DDL)
    conn.executemany(
        "INSERT INTO events(event_id,timestamp,modality,event_type,app_name,window_title,"
        "monitor_index,payload,correlation_id,attention_score,cognitive_load) "
        "VALUES(:event_id,:timestamp,:modality,:event_type,:app_name,:window_title,"
        ":monitor_index,:payload,:correlation_id,:attention_score,:cognitive_load)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def kg_db(tmp_path):
    """Backfill a knowledge.db from synthetic events; return (path, probe_facts)."""
    events_db = tmp_path / "activity.db"
    _make_events_db(events_db, [
        _row("e-ocr", BASE, "ocr_result", app="Code.exe", title="mcp_tools.py",
             payload={"ocr_text": "quokka onboarding checklist draft"}),
        _row("e-type", BASE + 4, "typing_burst", app="Code.exe",
             payload={"burst_text": "wiring the quokka importer"}),
        _row("e-lock", BASE + 30, "session_lock"),
    ])
    kdb = tmp_path / "knowledge.db"
    store = KnowledgeStore(str(kdb))
    try:
        stats = bridge.backfill(str(events_db), store, since_days=10, now_s=BASE + 100)
        assert stats["ingested"] >= 2  # observations landed
        # discover a subject that actually has a fact + a context-predicate time
        subj = store.conn.execute("SELECT subject_id FROM facts LIMIT 1").fetchone()
        assert subj is not None, "backfill produced no facts"
        preds = ",".join("?" * len(CONTEXT_PREDICATES))
        ctx = store.conn.execute(
            f"SELECT valid_from FROM facts WHERE predicate IN ({preds}) "
            "ORDER BY valid_from LIMIT 1", tuple(CONTEXT_PREDICATES),
        ).fetchone()
        assert ctx is not None, "backfill produced no context-predicate facts"
    finally:
        store.close()
    return {"path": str(kdb), "subject": subj["subject_id"], "ctx_ms": ctx["valid_from"]}


def _point_at(monkeypatch, path):
    monkeypatch.setenv("CONTEXTPULSE_KNOWLEDGE_DB", path)


@pytest.fixture(autouse=True)
def _isolate_probe_db(tmp_path, monkeypatch):
    """Keep every recall call in this file out of the live probe.db.

    facts_about / context_at record each call in probe.db's tool_usage table,
    which the Phase 0 gate reads; unredirected, a test run would add rows to the
    live counter beside activity.db. Autouse so no test here can forget it. (Not
    a conftest.py: every package's tests dir is the module ``tests``, and a
    second ``tests.conftest`` breaks collection of the combined suite.)
    """
    path = tmp_path / "probe_usage.db"
    monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(path))
    return path


# ── tool behaviour over a populated store ────────────────────────────

def test_search_knowledge_finds_observation(kg_db, monkeypatch):
    _point_at(monkeypatch, kg_db["path"])
    out = mcp_tools.search_knowledge("quokka")
    assert "quokka" in out.lower()
    assert "Search results" in out


def test_facts_about_returns_facts(kg_db, monkeypatch):
    _point_at(monkeypatch, kg_db["path"])
    out = mcp_tools.facts_about(kg_db["subject"])
    assert out.startswith(f"Facts about '{kg_db['subject']}'")
    assert "- (" in out  # at least one formatted fact line


def test_context_at_returns_context(kg_db, monkeypatch):
    _point_at(monkeypatch, kg_db["path"])
    # query at the exact validity time of a context fact -> must return facts
    ts_seconds = kg_db["ctx_ms"] / 1000.0
    out = mcp_tools.context_at(str(ts_seconds))
    assert "Context around" in out
    assert "Facts:" in out


def test_kg_timeline_returns_history(kg_db, monkeypatch):
    _point_at(monkeypatch, kg_db["path"])
    # window must reach back to the 2025 BASE fixture
    out = mcp_tools.kg_timeline(kg_db["subject"], days=100000)
    assert f"Timeline for '{kg_db['subject']}'" in out
    assert "- (" in out


# ── graceful degradation ─────────────────────────────────────────────

def test_absent_db_message(tmp_path, monkeypatch):
    _point_at(monkeypatch, str(tmp_path / "does_not_exist.db"))
    assert "not built yet" in mcp_tools.facts_about("anything")
    assert "not built yet" in mcp_tools.context_at("now")
    assert "not built yet" in mcp_tools.search_knowledge("x")
    assert "not built yet" in mcp_tools.kg_timeline("anything")


def test_empty_db_graceful(tmp_path, monkeypatch):
    empty = tmp_path / "empty_knowledge.db"
    KnowledgeStore(str(empty)).close()  # creates schema, no facts
    _point_at(monkeypatch, str(empty))
    assert "No knowledge-graph facts" in mcp_tools.facts_about("nobody")
    assert "No knowledge-graph matches" in mcp_tools.search_knowledge("nothing")
    assert "No knowledge-graph timeline" in mcp_tools.kg_timeline("nobody")


def test_context_at_bad_time(tmp_path, monkeypatch):
    # bad time is rejected before the store is even opened
    _point_at(monkeypatch, str(tmp_path / "irrelevant.db"))
    out = mcp_tools.context_at("not-a-time")
    assert "Could not parse time" in out


def test_parse_when_ms_forms():
    assert mcp_tools._parse_when_ms("now") is not None
    assert mcp_tools._parse_when_ms("1750000000") == 1_750_000_000_000
    assert mcp_tools._parse_when_ms("2025-06-15 12:00") is not None
    assert mcp_tools._parse_when_ms("garbage") is None
    assert mcp_tools._parse_when_ms("") is None


# ── mcp_unified registration gate ────────────────────────────────────

def _run_gate(monkeypatch, enabled):
    import contextpulse_core.config as cfg
    import contextpulse_core.mcp_unified as u
    from mcp.server.fastmcp import FastMCP

    called: list[str] = []
    monkeypatch.setattr(u, "mcp_app", FastMCP("test"))
    for fn in ("_register_sight_tools", "_register_project_tools",
               "_register_voice_tools", "_register_touch_tools", "_register_memory_tools"):
        monkeypatch.setattr(u, fn, lambda: None)
    monkeypatch.setattr(u, "_register_probe_tools", lambda: called.append("probe"))
    monkeypatch.setattr(u, "_register_knowledge_tools", lambda: called.append("knowledge"))
    monkeypatch.setattr(cfg, "get", lambda k, d=None: enabled if k == "knowledge_enabled" else d)
    u._register_all()
    return called


def test_gate_disabled_serves_probe(monkeypatch):
    called = _run_gate(monkeypatch, enabled=False)
    assert called == ["probe"]


def test_gate_enabled_serves_knowledge_not_probe(monkeypatch):
    called = _run_gate(monkeypatch, enabled=True)
    assert called == ["knowledge"]


# ── usage recording: the Phase 0 gate's counter must survive the switch ──
#
# When knowledge_enabled is on, these tools REPLACE the probe's facts_about /
# context_at. The gate (probe_usage_report.py) and targets S3 / fn-cooper-recall-hits
# read probe.db's tool_usage table. If these tools record nothing, the day the
# switch flips the counter reads 0 while agents are calling recall, and the
# report prints "STAGE 1 NOT MET -- fix routing": a false verdict.

def _usage_rows(probe_db):
    conn = sqlite3.connect(str(probe_db))
    try:
        return conn.execute(
            "SELECT tool, query, hit_count FROM tool_usage ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_facts_about_records_usage_with_hit_count(kg_db, monkeypatch, _isolate_probe_db):
    _point_at(monkeypatch, kg_db["path"])
    out = mcp_tools.facts_about(kg_db["subject"])
    n_facts = out.count("\n- (")
    assert n_facts >= 1
    assert _usage_rows(_isolate_probe_db) == [("facts_about", kg_db["subject"], n_facts)]


def test_context_at_records_usage_with_hits(kg_db, monkeypatch, _isolate_probe_db):
    _point_at(monkeypatch, kg_db["path"])
    ts_seconds = kg_db["ctx_ms"] / 1000.0
    mcp_tools.context_at(str(ts_seconds), window_minutes=20)
    rows = _usage_rows(_isolate_probe_db)
    assert len(rows) == 1
    tool, query, hits = rows[0]
    assert tool == "context_at"
    assert query == f"{ts_seconds} +/-20min"
    assert hits >= 1


def test_no_hits_records_zero(tmp_path, monkeypatch, _isolate_probe_db):
    empty = tmp_path / "empty_knowledge.db"
    KnowledgeStore(str(empty)).close()
    _point_at(monkeypatch, str(empty))
    mcp_tools.facts_about("nobody")
    assert _usage_rows(_isolate_probe_db) == [("facts_about", "nobody", 0)]


def test_absent_store_still_records_the_call(tmp_path, monkeypatch, _isolate_probe_db):
    # The tool was reached; that is what stage 1 counts. Zero hits, so S3 is not inflated.
    _point_at(monkeypatch, str(tmp_path / "does_not_exist.db"))
    mcp_tools.facts_about("anything")
    mcp_tools.context_at("now")
    assert [(t, h) for t, _q, h in _usage_rows(_isolate_probe_db)] == [
        ("facts_about", 0), ("context_at", 0),
    ]


def test_unparseable_time_records_nothing(tmp_path, monkeypatch, _isolate_probe_db):
    # Mirrors probe_mcp: a rejected input never reached recall.
    _point_at(monkeypatch, str(tmp_path / "irrelevant.db"))
    mcp_tools.context_at("not-a-time")
    assert not _isolate_probe_db.exists() or _usage_rows(_isolate_probe_db) == []


def test_recording_failure_never_breaks_recall(kg_db, monkeypatch):
    from contextpulse_core import probe

    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(probe, "record_usage", boom)
    _point_at(monkeypatch, kg_db["path"])
    out = mcp_tools.facts_about(kg_db["subject"])
    assert calls == [1], "the failing recorder was never reached; this test proved nothing"
    assert out.startswith(f"Facts about '{kg_db['subject']}'")


def test_unopenable_probe_db_never_breaks_recall(kg_db, monkeypatch, tmp_path):
    # A probe.db path whose parent does not exist: connect itself fails.
    monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(tmp_path / "no_such_dir" / "probe.db"))
    _point_at(monkeypatch, kg_db["path"])
    out = mcp_tools.facts_about(kg_db["subject"])
    assert out.startswith(f"Facts about '{kg_db['subject']}'")


def test_gate_report_counts_knowledge_calls(kg_db, monkeypatch, _isolate_probe_db):
    # The consumer, not just the table: probe.usage_summary is what the report reads.
    from contextpulse_core import probe

    _point_at(monkeypatch, kg_db["path"])
    mcp_tools.facts_about(kg_db["subject"])
    mcp_tools.context_at(str(kg_db["ctx_ms"] / 1000.0))
    conn = probe.connect_probe(_isolate_probe_db)
    try:
        summary = probe.usage_summary(conn)
    finally:
        conn.close()
    assert summary["total_calls"] == 2
    assert summary["calls_with_hits"] == 2
