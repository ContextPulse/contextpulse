# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for contextpulse_core.probe — Phase 0 wedge-probe fused-recall logic.

THROWAWAY per cp-implementation-plan-FINAL.md §Phase 0. These cover the pure,
deterministic surface: probe.db schema, event-text extraction, extraction-prompt
assembly, tolerant LLM-output parsing, and the entity/temporal query paths. The
Claude-CLI subprocess call and the live-DB copy are I/O and live in the thin
scripts/probe_consolidator.py CLI, not here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from contextpulse_core import probe

# ── probe.db schema + connection ────────────────────────────────────


def test_connect_probe_creates_facts_table(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)")}
    assert {"entity", "fact", "valid_from", "source_event_ids", "confidence"} <= cols


def test_connect_probe_is_idempotent(tmp_path):
    p = tmp_path / "probe.db"
    probe.connect_probe(p).close()
    # second open must not raise (CREATE ... IF NOT EXISTS)
    conn = probe.connect_probe(p)
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


# ── write + query facts ─────────────────────────────────────────────


def _fact(entity, fact, valid_from, ids=("e1",), conf=0.9):
    return {
        "entity": entity,
        "fact": fact,
        "valid_from": valid_from,
        "source_event_ids": list(ids),
        "confidence": conf,
    }


def test_write_facts_returns_count_and_persists(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    n = probe.write_facts(conn, [_fact("AcmeTrader", "swing bot is disabled", 1000.0)])
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1


def test_source_event_ids_roundtrip_as_json(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(conn, [_fact("X", "y", 1.0, ids=["a", "b", "c"])])
    hits = probe.query_facts_about(conn, "X")
    assert hits[0]["source_event_ids"] == ["a", "b", "c"]


def test_query_facts_about_is_case_insensitive_partial(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(conn, [_fact("ContextPulse", "Phase 0 started", 5.0)])
    assert probe.query_facts_about(conn, "contextpulse")
    assert probe.query_facts_about(conn, "Pulse")  # partial substring


def test_query_facts_about_empty_when_no_match(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(conn, [_fact("A", "b", 1.0)])
    assert probe.query_facts_about(conn, "Nonexistent") == []


# ── temporal recall (context_at) ────────────────────────────────────


def test_query_context_at_returns_facts_in_window(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(
        conn,
        [
            _fact("morning", "reviewed briefing", 1_000.0),
            _fact("noon", "edgelab work", 5_000.0),
            _fact("evening", "career campaign", 9_000.0),
        ],
    )
    hits = probe.query_context_at(conn, 5_000.0, window_s=600)
    facts = {h["fact"] for h in hits}
    assert "edgelab work" in facts
    assert "career campaign" not in facts  # outside +/- 600s window


def test_query_context_at_orders_by_proximity(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(
        conn,
        [_fact("near", "n", 1_010.0), _fact("far", "f", 1_500.0)],
    )
    hits = probe.query_context_at(conn, 1_000.0, window_s=3600)
    assert hits[0]["fact"] == "n"  # closest to target first


# ── event-text extraction ───────────────────────────────────────────


def _events_db(tmp_path, rows):
    """Build a minimal events table matching spine/bus.py schema."""
    p = tmp_path / "activity.db"
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, timestamp REAL, modality TEXT,"
        " event_type TEXT, app_name TEXT, window_title TEXT, monitor_index INT,"
        " payload TEXT, correlation_id TEXT, attention_score REAL, cognitive_load REAL,"
        " created_at REAL)"
    )
    c.executemany(
        "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
        " window_title, payload) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    c.row_factory = sqlite3.Row
    return c


def test_read_recent_events_extracts_ocr_transcript_text(tmp_path):
    conn = _events_db(
        tmp_path,
        [
            (
                "e1",
                100.0,
                "sight",
                "capture",
                "Code",
                "app.py",
                json.dumps({"ocr_text": "def main"}),
            ),
            ("e2", 200.0, "voice", "transcript", "", "", json.dumps({"transcript": "hello world"})),
            ("e3", 300.0, "touch", "type", "Chrome", "gmail", json.dumps({"text": "draft"})),
            ("e4", 50.0, "sight", "capture", "old", "old", json.dumps({"ocr_text": "too old"})),
        ],
    )
    evs = probe.read_recent_events(conn, since_ts=90.0)
    texts = {e["text"] for e in evs}
    assert texts == {"def main", "hello world", "draft"}  # e4 excluded (before cutoff)


def test_read_recent_events_extracts_burst_and_correction_text(tmp_path):
    # burst_text (typed) and correction_text (voice corrections) are the person's
    # own words — the live FTS trigger misses them; the probe must not.
    conn = _events_db(
        tmp_path,
        [
            ("e1", 100.0, "touch", "burst", "Code", "x", json.dumps({"burst_text": "typed this"})),
            (
                "e2",
                200.0,
                "voice",
                "correct",
                "",
                "",
                json.dumps({"correction_text": "fixed that"}),
            ),
        ],
    )
    texts = {e["text"] for e in probe.read_recent_events(conn, since_ts=0.0)}
    assert texts == {"typed this", "fixed that"}


def test_read_recent_events_handles_missing_and_bad_payload(tmp_path):
    conn = _events_db(
        tmp_path,
        [
            ("e1", 100.0, "sight", "capture", "A", "w", "not json"),
            ("e2", 110.0, "sight", "capture", "B", "w", json.dumps({"other": "x"})),
        ],
    )
    evs = probe.read_recent_events(conn, since_ts=0.0)
    # must not raise; text falls back to empty string
    assert all(e["text"] == "" for e in evs)
    assert len(evs) == 2


def test_read_recent_events_takes_most_recent_chronologically(tmp_path):
    conn = _events_db(
        tmp_path,
        [
            ("old", 100.0, "sight", "capture", "A", "w", json.dumps({"text": "old"})),
            ("mid", 200.0, "sight", "capture", "B", "w", json.dumps({"text": "mid"})),
            ("new", 300.0, "sight", "capture", "C", "w", json.dumps({"text": "new"})),
        ],
    )
    evs = probe.read_recent_events(conn, since_ts=0.0, limit=2)
    # newest 2 selected, returned oldest-first (chronological) for the prompt
    assert [e["event_id"] for e in evs] == ["mid", "new"]


# ── prompt assembly ─────────────────────────────────────────────────


def test_build_extraction_prompt_mentions_json_and_events():
    evs = [
        {
            "event_id": "e1",
            "timestamp": 100.0,
            "modality": "sight",
            "app_name": "Code",
            "window_title": "probe.py",
            "text": "hello",
        },
    ]
    prompt = probe.build_extraction_prompt(evs)
    assert "json" in prompt.lower()
    assert "entity" in prompt.lower() and "fact" in prompt.lower()
    assert "Code" in prompt  # event content is embedded


def test_build_extraction_prompt_empty_events_is_safe():
    prompt = probe.build_extraction_prompt([])
    assert isinstance(prompt, str) and len(prompt) > 0


# ── tolerant LLM-output parsing ─────────────────────────────────────


def test_parse_facts_plain_json_array():
    out = json.dumps([_fact("A", "b", 1.0)])
    facts = probe.parse_facts(out)
    assert len(facts) == 1 and facts[0]["entity"] == "A"


def test_parse_facts_strips_markdown_fences_and_prose():
    out = "Here are the facts:\n```json\n" + json.dumps([_fact("A", "b", 1.0)]) + "\n```\nDone."
    facts = probe.parse_facts(out)
    assert len(facts) == 1


def test_parse_facts_skips_entries_missing_required_fields():
    out = json.dumps(
        [
            {"entity": "A", "fact": "keep"},
            {"entity": "B"},  # missing fact — drop
            {"fact": "no entity"},  # missing entity — drop
        ]
    )
    facts = probe.parse_facts(out)
    assert [f["fact"] for f in facts] == ["keep"]


def test_parse_facts_returns_empty_on_garbage():
    assert probe.parse_facts("the model refused, no json here") == []
    assert probe.parse_facts("") == []


def test_parse_facts_defaults_optional_fields():
    out = json.dumps([{"entity": "A", "fact": "b"}])
    facts = probe.parse_facts(out)
    assert facts[0]["confidence"] == pytest.approx(0.5)
    assert facts[0]["source_event_ids"] == []


# ── outcome-discriminating parse (cp-consolidator-silent-zero-fact-runs) ──
#
# probe.parse_facts() alone cannot tell a legitimate empty `[]` (the prompt
# explicitly allows this) apart from unparseable non-answer output — both
# collapse to []. That collapse is exactly what let a scheduled run that got
# a fast non-answer from the CLI report "OK: ... -> 0 new facts" identically
# to a genuine quiet window. parse_facts_with_outcome() is the discriminator.


def test_outcome_facts_on_nonempty_valid_array():
    out = json.dumps([_fact("A", "b", 1.0)])
    facts, outcome = probe.parse_facts_with_outcome(out)
    assert outcome is probe.ParseOutcome.FACTS
    assert len(facts) == 1


def test_outcome_empty_on_valid_empty_array():
    facts, outcome = probe.parse_facts_with_outcome("[]")
    assert outcome is probe.ParseOutcome.EMPTY
    assert facts == []


def test_outcome_empty_on_valid_empty_array_with_surrounding_prose():
    # The prompt's own escape hatch: "If nothing durable happened, return []."
    out = "I reviewed the log and found nothing durable to record.\n[]\nDone."
    facts, outcome = probe.parse_facts_with_outcome(out)
    assert outcome is probe.ParseOutcome.EMPTY
    assert facts == []


def test_outcome_fault_on_no_array_found():
    facts, outcome = probe.parse_facts_with_outcome("the model refused, no json here")
    assert outcome is probe.ParseOutcome.FAULT
    assert facts == []


def test_outcome_fault_on_empty_string():
    facts, outcome = probe.parse_facts_with_outcome("")
    assert outcome is probe.ParseOutcome.FAULT
    assert facts == []


def test_outcome_fault_on_whitespace_only():
    facts, outcome = probe.parse_facts_with_outcome("   \n\t  ")
    assert outcome is probe.ParseOutcome.FAULT
    assert facts == []


def test_outcome_fault_on_malformed_json():
    # A truncated/garbled array — brackets present, contents not valid JSON.
    facts, outcome = probe.parse_facts_with_outcome('[{"entity": "A", "fact": ')
    assert outcome is probe.ParseOutcome.FAULT
    assert facts == []


def test_outcome_facts_when_array_wrapped_in_an_object():
    # find("[")/rfind("]") bracket-extraction (inherited unchanged from
    # parse_facts, same mechanism test_parse_facts_strips_markdown_fences_
    # and_prose relies on) means an object-wrapped array still extracts the
    # inner array successfully -- this is the SAME tolerance already
    # exercised for markdown fences/prose, not a new behavior, and is
    # deliberately not classified FAULT.
    out = '{"facts": [{"entity": "A", "fact": "b"}]}'
    facts, outcome = probe.parse_facts_with_outcome(out)
    assert outcome is probe.ParseOutcome.FACTS
    assert len(facts) == 1


def test_outcome_fault_when_no_bracket_pair_exists_at_all():
    # A prose-only refusal with no array anywhere — the actually-reachable
    # "no array found" FAULT path (find("[")/rfind("]") both return -1).
    facts, outcome = probe.parse_facts_with_outcome('{"result": "nothing to report"}')
    assert outcome is probe.ParseOutcome.FAULT
    assert facts == []


def test_outcome_facts_not_fault_when_array_valid_but_entries_all_invalid():
    # A structurally valid, non-empty array is FACTS-shaped output even if
    # every entry fails the entity/fact validation below it (the model
    # answered, just answered badly) -- NOT the same failure as no answer
    # at all. This is a deliberate design choice, not an oversight: see the
    # ParseOutcome docstring.
    out = json.dumps([{"entity": "A"}, {"fact": "no entity"}])
    facts, outcome = probe.parse_facts_with_outcome(out)
    assert outcome is probe.ParseOutcome.FACTS
    assert facts == []  # both entries dropped by validation


def test_parse_facts_and_parse_facts_with_outcome_agree_on_returned_facts():
    # The two must never diverge on WHAT they return, only on whether the
    # caller also gets to see WHY it was empty.
    for out in (
        json.dumps([_fact("A", "b", 1.0)]),
        "[]",
        "garbage",
        "",
        '[{"entity": "A", "fact": ',
    ):
        assert probe.parse_facts(out) == probe.parse_facts_with_outcome(out)[0]


# ── valid_from coercion (red-team M2) ───────────────────────────────


def test_parse_facts_keeps_numeric_valid_from_as_float():
    out = json.dumps([{"entity": "A", "fact": "b", "valid_from": 1751499000}])
    assert probe.parse_facts(out)[0]["valid_from"] == pytest.approx(1751499000.0)


def test_parse_facts_coerces_iso_valid_from_to_epoch():
    # An ISO string stored as TEXT is invisible to context_at's numeric BETWEEN
    # and sorts above REAL timestamps in facts_about — coerce to epoch.
    out = json.dumps([{"entity": "A", "fact": "b", "valid_from": "2026-07-07T06:00:00"}])
    vf = probe.parse_facts(out)[0]["valid_from"]
    assert isinstance(vf, float)
    assert vf == pytest.approx(datetime(2026, 7, 7, 6, 0, 0).timestamp())


def test_parse_facts_downscales_millisecond_epoch():
    out = json.dumps([{"entity": "A", "fact": "b", "valid_from": 1751499000000}])
    assert probe.parse_facts(out)[0]["valid_from"] == pytest.approx(1751499000.0)


def test_parse_facts_unparseable_valid_from_becomes_none():
    out = json.dumps([{"entity": "A", "fact": "b", "valid_from": "sometime yesterday"}])
    assert probe.parse_facts(out)[0]["valid_from"] is None


# ── duplicate accumulation (red-team M4) ────────────────────────────


def test_write_facts_dedupes_identical_entity_fact(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.write_facts(conn, [_fact("A", "same fact", 1.0)])
    probe.write_facts(conn, [_fact("A", "same fact", 2.0)])  # rerun re-extracts it
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1


# ── claude CLI env sanitizing (auth-source leak) ────────────────────


def test_claude_cli_env_strips_api_key(monkeypatch):
    # A User-scope ANTHROPIC_API_KEY leaking into the scheduled task made
    # `claude -p` bill/fail on the Console wallet instead of the free Max login.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-leaked")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-oat01-leaked")
    monkeypatch.setenv("PATH", "keep-me")
    env = probe.claude_cli_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["PATH"] == "keep-me"  # unrelated vars pass through


def test_claude_cli_env_is_noop_when_key_absent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    env = probe.claude_cli_env()
    assert "ANTHROPIC_API_KEY" not in env


# ── run ledger (red-team M1) ────────────────────────────────────────


def test_record_run_appends_ledger_row(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.record_run(conn, events=1500, facts=3, error=None)
    probe.record_run(conn, events=0, facts=0, error="claude timeout")
    rows = conn.execute("SELECT events, facts, error FROM probe_runs ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1500, 3, None), (0, 0, "claude timeout")]


def test_record_run_elapsed_s_defaults_to_null(tmp_path):
    # Old call sites (and the ledger's historical rows) never passed timing.
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.record_run(conn, events=1500, facts=3, error=None)
    row = conn.execute("SELECT elapsed_s FROM probe_runs").fetchone()
    assert row["elapsed_s"] is None


def test_record_run_persists_elapsed_s():
    # The 6.4s-vs-42s gap IS the signal cp-consolidator-silent-zero-fact-runs
    # needed and nothing was recording — this is the fix.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(probe._SCHEMA_SQL)
    probe._ensure_probe_runs_elapsed_column(conn)
    probe.record_run(conn, events=1500, facts=0, error="unparseable output", elapsed_s=6.4)
    probe.record_run(conn, events=1500, facts=17, error=None, elapsed_s=42.1)
    rows = conn.execute("SELECT elapsed_s FROM probe_runs ORDER BY id").fetchall()
    assert [r["elapsed_s"] for r in rows] == [pytest.approx(6.4), pytest.approx(42.1)]


def test_ensure_probe_runs_elapsed_column_migrates_pre_existing_db(tmp_path):
    # Simulate a probe.db created before this fix: schema WITHOUT elapsed_s,
    # already holding real rows (this repo's probe.db has 280+). Migration
    # must add the column without touching existing data.
    p = tmp_path / "probe.db"
    pre_migration = sqlite3.connect(str(p))
    pre_migration.executescript(
        """
        CREATE TABLE probe_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
            events INTEGER NOT NULL DEFAULT 0,
            facts INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );
        """
    )
    pre_migration.execute(
        "INSERT INTO probe_runs (events, facts, error) VALUES (1500, 0, NULL)"
    )
    pre_migration.commit()
    pre_migration.close()

    # connect_probe() must not raise on a probe_runs table that predates
    # elapsed_s, and the pre-existing row must survive with elapsed_s NULL.
    conn = probe.connect_probe(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(probe_runs)")}
    assert "elapsed_s" in cols
    row = conn.execute("SELECT events, facts, elapsed_s FROM probe_runs").fetchone()
    assert tuple(row) == (1500, 0, None)

    # Re-opening an already-migrated probe.db must not raise either.
    conn2 = probe.connect_probe(p)
    assert conn2.execute("SELECT COUNT(*) FROM probe_runs").fetchone()[0] == 1


# ── automatic tool-usage log (cp-savegate-attribution-instrument) ──
#
# Fixes the ambiguity a 0-of-3 save count carried: "no value" (tools were
# called, nothing qualified as novel) vs "nothing was watching" (probe_save.py
# was simply never invoked). record_usage() is called on EVERY real
# facts_about/context_at call, unconditionally — not a manual logger.


def test_record_usage_inserts_row(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.record_usage(conn, "facts_about", "ContextPulse", hit_count=3)
    row = conn.execute("SELECT tool, query, hit_count FROM tool_usage").fetchone()
    assert tuple(row) == ("facts_about", "ContextPulse", 3)


def test_record_usage_persists_across_connections(tmp_path):
    p = tmp_path / "probe.db"
    conn1 = probe.connect_probe(p)
    probe.record_usage(conn1, "context_at", "now +/-30min", hit_count=0)
    conn1.close()
    conn2 = probe.connect_probe(p)
    assert conn2.execute("SELECT COUNT(*) FROM tool_usage").fetchone()[0] == 1


def test_record_usage_zero_hits_is_a_valid_call(tmp_path):
    """A call that returned nothing is still a call — must be logged, not skipped."""
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.record_usage(conn, "facts_about", "Nonexistent", hit_count=0)
    assert conn.execute("SELECT COUNT(*) FROM tool_usage").fetchone()[0] == 1


def test_usage_summary_empty_db_reports_zero_not_absence(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    summary = probe.usage_summary(conn)
    assert summary == {"total_calls": 0, "calls_with_hits": 0, "by_tool": {}}


def test_usage_summary_counts_and_breaks_down_by_tool(tmp_path):
    conn = probe.connect_probe(tmp_path / "probe.db")
    probe.record_usage(conn, "facts_about", "A", hit_count=2)
    probe.record_usage(conn, "facts_about", "B", hit_count=0)
    probe.record_usage(conn, "context_at", "now", hit_count=5)
    summary = probe.usage_summary(conn)
    assert summary["total_calls"] == 3
    assert summary["calls_with_hits"] == 2
    assert summary["by_tool"] == {
        "facts_about": {"calls": 2, "with_hits": 1},
        "context_at": {"calls": 1, "with_hits": 1},
    }
