# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The startup sweep: raw rows must not survive to be probed.

Review finding B1, second half. Matching against redacted text closes the
oracle for the search paths, but the raw rows are still on disk and every
future reader has to remember. ensure_migrated rewrites them once, at startup,
before anything can serve a query.

Three properties, each with its own failure mode:
  RUNS ONCE      a second call must do no work, or a daemon restart loop
                 re-scans the whole store every time
  IDEMPOTENT     running it twice must leave the store identical, which is the
                 definition of a sweep rather than a mutation
  NON-FATAL      it is a cleanup, not a precondition for capture; a broken or
                 missing store must not take the daemon down at startup

Every value below is SYNTHETIC.
"""

import json
import sqlite3
import time

import pytest
from contextpulse_core import purge
from contextpulse_core.redact import redact_sensitive

CONTROL_WORD = "zqcontrol"
CLIP_SECRET = "sk-zqmigrationneedle0123456789ABCD"
BURST_SECRET = "ghp_zqmigrationburst0123456789abcdefghij"
FACT_SECRET = "AKIAZQMIGRATIONFACT1"
OBS_SECRET = "password: zqmigrationobs42"


def _activity_db(tmp_path):
    from contextpulse_core.spine import EventBus
    from contextpulse_sight.activity import ActivityDB

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    db.record_clipboard(timestamp=time.time(), text=f"{CONTROL_WORD} {CLIP_SECRET}")
    db.close()

    bus = EventBus(db_path)
    bus.close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
        " window_title, monitor_index, payload, correlation_id, attention_score,"
        " cognitive_load) VALUES (?, ?, 'keys', 'typing_burst', 'Code.exe', ?, 0, ?,"
        " NULL, 0.0, 0.0)",
        (
            "e-mig-1", time.time(), f"{CONTROL_WORD} editor",
            json.dumps({"burst_text": f"{CONTROL_WORD} {BURST_SECRET}"}),
        ),
    )
    conn.commit()
    conn.close()
    return db_path


def _probe_db(tmp_path):
    path = tmp_path / "probe.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY, entity TEXT, fact TEXT, "
        "valid_from REAL, confidence REAL)"
    )
    conn.execute(
        "INSERT INTO facts (entity, fact, valid_from, confidence) VALUES (?,?,?,?)",
        (f"{CONTROL_WORD}-project", f"deploy key is {FACT_SECRET}", 1.0, 0.9),
    )
    conn.commit()
    conn.close()
    return path


def _knowledge_db(tmp_path):
    path = tmp_path / "knowledge.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE observations (id INTEGER PRIMARY KEY, content TEXT, "
        "window_title TEXT, url TEXT)"
    )
    conn.execute(
        "INSERT INTO observations (content, window_title, url) VALUES (?,?,?)",
        (f"{CONTROL_WORD} {OBS_SECRET}", f"{CONTROL_WORD} editor", None),
    )
    conn.commit()
    conn.close()
    return path


def _clipboard_text(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return " ".join(r[0] or "" for r in conn.execute("SELECT text FROM clipboard"))
    finally:
        conn.close()


def _event_payloads(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return " ".join(r[0] or "" for r in conn.execute("SELECT payload FROM events"))
    finally:
        conn.close()


class TestFixturesAreRedactable:
    @pytest.mark.parametrize(
        "secret", [CLIP_SECRET, BURST_SECRET, FACT_SECRET, OBS_SECRET]
    )
    def test_pattern_removes_it(self, secret):
        assert secret not in redact_sensitive(secret)


class TestEnsureMigrated:
    def test_rewrites_activity_rows(self, tmp_path):
        db_path = _activity_db(tmp_path)
        # Control: the raw values really are on disk before the sweep.
        assert CLIP_SECRET in _clipboard_text(db_path)
        assert BURST_SECRET in _event_payloads(db_path)

        counts = purge.ensure_migrated(db_path)

        assert counts, "the sweep reported nothing -- it did no work"
        assert CLIP_SECRET not in _clipboard_text(db_path)
        assert BURST_SECRET not in _event_payloads(db_path)
        assert CONTROL_WORD in _clipboard_text(db_path), "context was destroyed"

    def test_rebuilds_the_fts_index(self, tmp_path):
        db_path = _activity_db(tmp_path)
        purge.ensure_migrated(db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            # Positive control first: the index is populated and reachable.
            assert conn.execute(
                "SELECT count(*) FROM events_fts WHERE events_fts MATCH ?",
                (CONTROL_WORD,),
            ).fetchone()[0] == 1
            # events has no AFTER UPDATE trigger, so without the rebuild the
            # pre-sweep terms would still be indexed.
            conn.execute(
                "INSERT INTO events_fts(events_fts, rank) VALUES('integrity-check', 1)"
            )
        finally:
            conn.close()

    def test_sweeps_the_derived_stores(self, tmp_path):
        db_path = _activity_db(tmp_path)
        probe_db = _probe_db(tmp_path)
        knowledge_db = _knowledge_db(tmp_path)

        purge.ensure_migrated(db_path, probe_db=probe_db, knowledge_db=knowledge_db)

        conn = sqlite3.connect(str(probe_db))
        facts = " ".join(str(r[0]) + str(r[1]) for r in conn.execute("SELECT entity, fact FROM facts"))
        conn.close()
        assert CONTROL_WORD in facts, "facts row is empty -- assertion is vacuous"
        assert FACT_SECRET not in facts, "probe.db facts were not swept"

        conn = sqlite3.connect(str(knowledge_db))
        obs = " ".join(r[0] or "" for r in conn.execute("SELECT content FROM observations"))
        conn.close()
        assert CONTROL_WORD in obs
        assert OBS_SECRET not in obs, "knowledge.db observations were not swept"

    def test_runs_only_once(self, tmp_path):
        db_path = _activity_db(tmp_path)
        first = purge.ensure_migrated(db_path)
        assert first, "the first run did nothing"
        second = purge.ensure_migrated(db_path)
        assert second == {}, (
            "the sweep re-ran; a restart loop would rescan the whole store every time"
        )

    def test_is_idempotent(self, tmp_path):
        db_path = _activity_db(tmp_path)
        purge.ensure_migrated(db_path)
        after_first = (_clipboard_text(db_path), _event_payloads(db_path))
        # Force a second real pass by using a different marker name -- the
        # once-only guard would otherwise hide a non-idempotent rewrite.
        purge.ensure_migrated(db_path, name="secret-redaction-test-second-pass")
        assert (_clipboard_text(db_path), _event_payloads(db_path)) == after_first

    def test_a_missing_database_is_not_an_error(self, tmp_path):
        assert purge.ensure_migrated(tmp_path / "nope.db") == {}

    def test_an_unreadable_derived_store_does_not_abort_the_sweep(self, tmp_path):
        db_path = _activity_db(tmp_path)
        broken = tmp_path / "probe.db"
        broken.write_bytes(b"this is not a database")

        counts = purge.ensure_migrated(db_path, probe_db=broken)

        # activity.db was still swept -- a cleanup must not be able to stop
        # the daemon starting, and it must not skip the store it CAN fix.
        assert CLIP_SECRET not in _clipboard_text(db_path)
        assert counts

    def test_a_clean_store_marks_itself_migrated(self, tmp_path):
        """Otherwise every start of a clean install rescans the whole table."""
        from contextpulse_core.spine import EventBus
        from contextpulse_sight.activity import ActivityDB

        db_path = tmp_path / "activity.db"
        ActivityDB(db_path=db_path).close()
        EventBus(db_path).close()

        assert purge.ensure_migrated(db_path) == {}
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute(
                "SELECT count(*) FROM cp_migrations WHERE name = ?",
                (purge.MIGRATION_NAME,),
            ).fetchone()[0] == 1
        finally:
            conn.close()


class TestDaemonAndMcpBothCallIt:
    """Two processes, either of which can start first."""

    def test_daemon_entry_point_is_wired(self):
        import inspect

        from contextpulse_core import daemon

        assert "start_secret_migration()" in inspect.getsource(daemon.ContextPulseDaemon.run)

    def test_the_daemon_constructor_does_no_sweeping(self):
        """Wiring it into __init__ first was wrong twice over.

        A constructor must not do I/O, and constructing a daemon in a test must
        not touch the real store -- which it did: the first version timed out
        the suite scanning David's live activity.db.
        """
        import inspect

        from contextpulse_core import daemon

        source = inspect.getsource(daemon.ContextPulseDaemon.__init__)
        assert "secret_migration" not in source

    def test_the_sweep_is_backgrounded(self):
        """A full-table scan run synchronously at startup is a hang."""
        import inspect

        from contextpulse_core import daemon

        source = inspect.getsource(daemon.start_secret_migration)
        assert "threading.Thread" in source
        assert "daemon=True" in source

    def test_mcp_entry_point_is_wired(self):
        import inspect

        from contextpulse_sight import mcp_server

        assert "_ensure_secret_migration()" in inspect.getsource(mcp_server.main)
        assert "_ensure_secret_migration()" in inspect.getsource(mcp_server._get_event_bus)
        assert "start_secret_migration" in inspect.getsource(
            mcp_server._ensure_secret_migration
        ), "a tool call must not block on a full-table scan"
