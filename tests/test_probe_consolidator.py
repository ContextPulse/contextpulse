# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for scripts/probe_consolidator.py (cp-consolidator-silent-zero-fact-runs).

Before this fix, probe.parse_facts() collapsed three distinct outcomes into
one empty list: a genuinely quiet window (`[]`), and total extraction
failure (no parseable array at all). The consolidator logged "Parsed 0
facts", recorded the run with error=None, and printed OK for BOTH — so a
scheduled run that got a fast non-answer from the Claude CLI (probe_runs id
286: 1500 events, 6.4s, 0 facts) was indistinguishable in the ledger from a
legitimate quiet window, while the identical workload run by hand 2.5 hours
later (probe_runs id 287: 1500 events, 42s, 17 facts) succeeded.

These tests drive scripts/probe_consolidator.py's main() end-to-end against
a real on-disk activity.db and probe.db, mocking only the Claude CLI call
(the one genuine network/subprocess boundary) — same "mock only the outer
I/O boundary" discipline as tests/test_probe_usage_report.py.

scripts/ is not a package (no __init__.py); loaded by file path, same
pattern as tests/test_retention_sweep.py and tests/test_nightly_learning.py.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from contextpulse_core import probe

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "probe_consolidator.py"


def _load_consolidator():
    spec = importlib.util.spec_from_file_location("probe_consolidator_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def consolidator():
    return _load_consolidator()


@pytest.fixture
def activity_db(tmp_path):
    """A minimal real activity.db with the exact columns read_recent_events
    queries, holding one recent event so the "no events in window" early
    exit isn't what the outcome tests are accidentally exercising."""
    p = tmp_path / "activity.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE events (event_id TEXT, timestamp REAL, modality TEXT, "
        "event_type TEXT, app_name TEXT, window_title TEXT, payload TEXT)"
    )
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "e1",
            9_999_999_999.0,  # far future — always "recent" regardless of test run time
            "touch",
            "text",
            "TestApp",
            "Test Window",
            json.dumps({"text": "shipped the fix"}),
        ),
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def probe_db(tmp_path):
    return tmp_path / "probe.db"


def _run(consolidator, activity_db, probe_db, extra_argv=()):
    argv = [
        "--hours",
        "999999",  # wide enough to always include the fixture's far-future row
        "--activity-db",
        str(activity_db),
        "--probe-db",
        str(probe_db),
        *extra_argv,
    ]
    return consolidator.main(argv)


def _probe_runs_rows(probe_db):
    conn = probe.connect_probe(probe_db)
    rows = conn.execute(
        "SELECT events, facts, error, elapsed_s FROM probe_runs ORDER BY id"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


class TestOutcomeFacts:
    """(a) Valid non-empty array — success, as before this change."""

    def test_exit_zero_and_facts_written(self, consolidator, activity_db, probe_db):
        canned = json.dumps(
            [{"entity": "TestApp", "fact": "shipped the fix", "valid_from": 9_999_999_999.0}]
        )
        with patch.object(consolidator, "call_claude", return_value=(canned, 1.2)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        conn = probe.connect_probe(probe_db)
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        conn.close()
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert (events, facts, error) == (1, 1, None)
        assert elapsed_s == pytest.approx(1.2)


class TestOutcomeEmpty:
    """(b) Valid but EMPTY array — legitimate quiet window, still success.

    The elapsed times here are deliberately ABOVE the --min-elapsed floor
    (cp-consolidator-silent-zero-fact-runs, second half). A well-formed []
    returned in under a second on a non-empty window is no longer a quiet
    window; it is the failure shape TestFastEmptyIsAFault covers. These
    cases are the genuine article, so they have to look like it.
    """

    def test_exit_zero_and_error_stays_null(self, consolidator, activity_db, probe_db):
        with patch.object(consolidator, "call_claude", return_value=("[]", 42.0)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert (events, facts, error) == (1, 0, None)
        assert elapsed_s == pytest.approx(42.0)

    def test_logs_distinctly_from_a_fault(self, consolidator, activity_db, probe_db, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="probe.consolidator"):
            with patch.object(consolidator, "call_claude", return_value=("[]", 42.0)):
                rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        assert any("quiet window" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)


class TestOutcomeFault:
    """(c) No array found / unparseable / empty output — THE fix.

    This is the exact shape of probe_runs id 286: events=1500 (here: 1, to
    keep the fixture cheap), a fast CLI return, and output that parses to
    nothing. Before this change: error=None, exit 0, printed OK. Now: a
    non-null error, a non-zero exit, and the raw output logged.
    """

    @pytest.mark.parametrize(
        "raw_output",
        [
            "",  # the 12:30 shape: a fast, empty non-answer
            "   \n  ",
            "the model refused, no json here",
            '[{"entity": "A", "fact": ',  # truncated/malformed JSON
        ],
        ids=["empty", "whitespace", "no-array", "malformed-json"],
    )
    def test_exit_one_and_error_recorded(self, consolidator, activity_db, probe_db, raw_output):
        with patch.object(consolidator, "call_claude", return_value=(raw_output, 6.4)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 1
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert events == 1
        assert facts == 0
        assert error is not None  # the core regression: this used to be None
        assert elapsed_s == pytest.approx(6.4)

        conn = probe.connect_probe(probe_db)
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        conn.close()

    def test_logs_raw_output_and_byte_length(self, consolidator, activity_db, probe_db, caplog):
        import logging

        raw_output = "the model refused, no json here"
        with caplog.at_level(logging.ERROR, logger="probe.consolidator"):
            with patch.object(consolidator, "call_claude", return_value=(raw_output, 6.4)):
                rc = _run(consolidator, activity_db, probe_db)

        assert rc == 1
        fault_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(fault_logs) == 1
        message = fault_logs[0].getMessage()
        assert raw_output in message
        assert str(len(raw_output)) in message  # byte length is present
        assert "6.4" in message  # elapsed seconds is present

    def test_this_change_would_have_caught_the_12_30_failure(
        self, consolidator, activity_db, probe_db
    ):
        """Direct reproduction of the filed incident's own numbers: 1500
        events, 6.4s, output that parses to 0 facts, error previously
        recorded as None. Uses the real event count from the finding rather
        than the fixture's single row, via --limit, to make the
        correspondence to probe_runs id 286 exact."""
        # Pad the fixture DB to 1500 events so len(events) matches id 286.
        conn = sqlite3.connect(str(activity_db))
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (f"e{i}", 9_999_999_999.0 - i, "sight", "screenshot", "App", "Win", "{}")
                for i in range(1, 1500)
            ],
        )
        conn.commit()
        conn.close()

        with patch.object(consolidator, "call_claude", return_value=("", 6.4)):
            rc = _run(consolidator, activity_db, probe_db, extra_argv=["--limit", "1500"])

        assert rc == 1, "a 1500-event, 6.4s, unparseable run must now fail loud, not print OK"
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert events == 1500
        assert facts == 0
        assert error is not None
        assert elapsed_s == pytest.approx(6.4)


class TestUnaffectedPaths:
    """Regression guard: paths this change did not touch stay unchanged."""

    def test_no_events_in_window_still_exits_zero(self, consolidator, tmp_path, probe_db):
        empty_db = tmp_path / "empty_activity.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute(
            "CREATE TABLE events (event_id TEXT, timestamp REAL, modality TEXT, "
            "event_type TEXT, app_name TEXT, window_title TEXT, payload TEXT)"
        )
        conn.commit()
        conn.close()

        rc = _run(consolidator, empty_db, probe_db)

        assert rc == 0
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert (events, facts) == (0, 0)
        assert error == "no events in window"
        assert elapsed_s is None  # no CLI call was ever made

    def test_cli_subprocess_failure_still_exits_one(self, consolidator, activity_db, probe_db):
        with patch.object(
            consolidator, "call_claude", side_effect=RuntimeError("claude CLI exited 1: boom")
        ):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 1
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert facts == 0
        assert error is not None and "boom" in error


def _pad_to(activity_db, total: int) -> None:
    """Grow the fixture DB to `total` events so len(events) matches the incident."""
    conn = sqlite3.connect(str(activity_db))
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (f"e{i}", 9_999_999_999.0 - i, "sight", "screenshot", "App", "Win", "{}")
            for i in range(1, total)
        ],
    )
    conn.commit()
    conn.close()


class TestFastEmptyIsAFault:
    """The ONE case c5ff94c explicitly left open.

    Quoted from that work's own caveat: "If the CLI somehow returned a
    literal well-formed [] in 6.4s, the new code classifies it EMPTY and
    would NOT flip the exit code ... That second case cannot be ruled out
    from surviving evidence."

    It can be ruled out now, from evidence already on disk.
    logs/probe_consolidator.log holds eleven zero-fact runs on non-empty
    windows and every one landed in a 5.1-7.7s band; every run that produced
    facts landed at 31.8-63.7s. The bands are disjoint with no sample
    between them, because a genuine quiet window still costs the model a
    full read of a ~316KB / ~80K-token prompt before it can answer []. So
    latency is the instrument that separates "0 facts, extractor healthy"
    from "0 facts, extractor failed" in the exact case output SHAPE cannot.
    """

    def test_fast_empty_array_on_real_events_is_a_fault(
        self, consolidator, activity_db, probe_db
    ):
        """The incident's own numbers, with the output it could have had.

        1500 events, a well-formed `[]`, 6.4s. Against c5ff94c this exits 0
        with error=None — a silent zero-fact run reported as success. That is
        what makes this test worth writing.
        """
        _pad_to(activity_db, 1500)

        with patch.object(consolidator, "call_claude", return_value=("[]", 6.4)):
            rc = _run(consolidator, activity_db, probe_db, extra_argv=["--limit", "1500"])

        assert rc == 1, "a 1500-event window answered [] in 6.4s did not read its prompt"
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert events == 1500
        assert facts == 0
        assert error is not None, "the core regression: this used to be None"
        assert "implausibly fast" in error
        assert elapsed_s == pytest.approx(6.4)

    def test_raw_output_is_logged_at_error(self, consolidator, activity_db, probe_db, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="probe.consolidator"):
            with patch.object(consolidator, "call_claude", return_value=("[]", 6.4)):
                rc = _run(consolidator, activity_db, probe_db)

        assert rc == 1
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "6.4" in message
        assert "15.0" in message  # the floor it was measured against
        assert "'[]'" in message  # the raw output, so the next failure names itself

    def test_slow_empty_array_is_still_a_quiet_window(
        self, consolidator, activity_db, probe_db
    ):
        """The guard must not cry wolf on the case it was built to protect."""
        with patch.object(consolidator, "call_claude", return_value=("[]", 45.0)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert (events, facts, error) == (1, 0, None)
        assert elapsed_s == pytest.approx(45.0)

    def test_empty_window_is_exempt_from_the_floor(self, consolidator, tmp_path, probe_db):
        """Zero events means zero work — it never reaches the CLI at all."""
        empty_db = tmp_path / "empty_activity.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute(
            "CREATE TABLE events (event_id TEXT, timestamp REAL, modality TEXT, "
            "event_type TEXT, app_name TEXT, window_title TEXT, payload TEXT)"
        )
        conn.commit()
        conn.close()

        assert _run(consolidator, empty_db, probe_db) == 0

    @pytest.mark.parametrize(
        ("elapsed", "expected_exit"),
        [(0.1, 1), (14.9, 1), (15.0, 0), (60.0, 0)],
        ids=["instant", "just-under", "at-the-floor", "well-above"],
    )
    def test_floor_is_inclusive_at_or_above(
        self, consolidator, activity_db, probe_db, elapsed, expected_exit
    ):
        with patch.object(consolidator, "call_claude", return_value=("[]", elapsed)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == expected_exit

    def test_floor_is_overridable(self, consolidator, activity_db, probe_db):
        """The floor is a property of THIS prompt size and model; both change."""
        with patch.object(consolidator, "call_claude", return_value=("[]", 6.4)):
            rc = _run(consolidator, activity_db, probe_db, extra_argv=["--min-elapsed", "5"])
        assert rc == 0

    def test_floor_can_be_disabled(self, consolidator, activity_db, probe_db):
        with patch.object(consolidator, "call_claude", return_value=("[]", 0.1)):
            rc = _run(consolidator, activity_db, probe_db, extra_argv=["--min-elapsed", "0"])
        assert rc == 0

    def test_a_fast_run_that_produced_facts_is_untouched(
        self, consolidator, activity_db, probe_db
    ):
        """The floor keys on EMPTY, not on speed. Facts are facts."""
        canned = json.dumps(
            [{"entity": "TestApp", "fact": "shipped the fix", "valid_from": 9_999_999_999.0}]
        )
        with patch.object(consolidator, "call_claude", return_value=(canned, 1.2)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        [(events, facts, error, elapsed_s)] = _probe_runs_rows(probe_db)
        assert (facts, error) == (1, None)


class TestOperatorVisibleLine:
    """"OK: 0 new facts" is what let this defect hide for eight days.

    The line a scheduled wrapper's operator reads has to say WHICH zero-fact
    outcome this was, not just report a count.
    """

    def test_quiet_window_says_so_and_names_the_latency(
        self, consolidator, activity_db, probe_db, capsys
    ):
        with patch.object(consolidator, "call_claude", return_value=("[]", 42.0)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        out = capsys.readouterr().out
        assert "quiet window" in out
        assert "extractor healthy" in out
        assert "42.0s" in out

    def test_a_real_result_still_reads_as_before(
        self, consolidator, activity_db, probe_db, capsys
    ):
        canned = json.dumps(
            [{"entity": "TestApp", "fact": "shipped the fix", "valid_from": 9_999_999_999.0}]
        )
        with patch.object(consolidator, "call_claude", return_value=(canned, 33.0)):
            rc = _run(consolidator, activity_db, probe_db)

        assert rc == 0
        out = capsys.readouterr().out
        assert "OK: 1 events -> 1 new facts" in out
        assert "quiet window" not in out
