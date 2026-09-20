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
from pathlib import Path

import pytest
from contextpulse_core import purge
from contextpulse_core.redact import redact_sensitive

CONTROL_WORD = "zqcontrol"
CLIP_SECRET = "sk-zqmigrationneedle0123456789ABCD"
OCR_SECRET = "ghp_zqmigrationocr0123456789abcdefghijkl"
BURST_SECRET = "ghp_zqmigrationburst0123456789abcdefghij"
FACT_SECRET = "AKIAZQMIGRATIONFACT1"
MEM_SECRET = "sk-zqmemsweepneedle0123456789ABCD"
OBS_SECRET = "password: zqmigrationobs42"


def _activity_db(tmp_path):
    from contextpulse_core.spine import EventBus
    from contextpulse_sight.activity import ActivityDB

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    db.record_clipboard(timestamp=time.time(), text=f"{CONTROL_WORD} {CLIP_SECRET}")
    # The `activity` table is the largest text store in the product and the
    # first sweep never opened it (review B-2). update_ocr is the daemon's own
    # writer and stores what it is given, which is what a pre-fix row is.
    row_id = db.record(
        timestamp=time.time(),
        window_title=f"{CONTROL_WORD} editor",
        app_name="Code.exe",
    )
    db.update_ocr(row_id, f"{CONTROL_WORD} {OCR_SECRET}", 0.9)
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


def _activity_text(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return " ".join(
            " ".join(str(c or "") for c in row)
            for row in conn.execute(
                "SELECT ocr_text, window_title, app_name FROM activity"
            )
        )
    finally:
        conn.close()


def _activity_fts_hits(db_path, term):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT count(*) FROM activity_fts WHERE activity_fts MATCH ?", (term,)
        ).fetchone()[0]
    finally:
        conn.close()


class TestFixturesAreRedactable:
    @pytest.mark.parametrize(
        "secret", [CLIP_SECRET, OCR_SECRET, BURST_SECRET, FACT_SECRET, OBS_SECRET]
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

    def test_sweeps_the_activity_table(self, tmp_path):
        """Review B-2: the sweep scanned `clipboard` and `events` only.

        `activity.ocr_text` is guaranteed to hold unredacted secrets after
        upgrade for two reasons the redaction branch itself created -- sixteen
        pattern shapes that did not exist when those rows were OCR'd, and the
        word-boundary gap that left every glued token raw -- plus any period
        with redact_ocr_text=False. And it is reachable through search_history.
        """
        db_path = _activity_db(tmp_path)
        assert OCR_SECRET in _activity_text(db_path), "fixture is not raw -- vacuous"

        purge.ensure_migrated(db_path)

        assert OCR_SECRET not in _activity_text(db_path), (
            "activity.ocr_text was not swept"
        )
        assert CONTROL_WORD in _activity_text(db_path), "context was destroyed"

    def test_rebuilds_the_activity_fts_index(self, tmp_path):
        """A search index still holding the old terms is still an oracle."""
        db_path = _activity_db(tmp_path)
        assert _activity_fts_hits(db_path, OCR_SECRET) == 1, "fixture not indexed"

        purge.ensure_migrated(db_path)

        assert _activity_fts_hits(db_path, CONTROL_WORD) == 1, (
            "the whole index was lost, so the assertion below is vacuous"
        )
        assert _activity_fts_hits(db_path, OCR_SECRET) == 0

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


def _markers(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM cp_migrations")}
    finally:
        conn.close()


def _probe_text(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return " ".join(
            f"{r[0]} {r[1]}" for r in conn.execute("SELECT entity, fact FROM facts")
        )
    finally:
        conn.close()


class TestOneMarkerPerStore:
    """Review B-3: one marker, written before the derived stores were swept.

    The marker INSERT sat inside the activity.db block and the probe/knowledge
    loop ran afterwards, warning and continuing on failure. So a locked probe.db
    -- or a tray quit during the multi-minute sweep, which kills it outright
    because start_secret_migration runs on a daemon thread -- left those stores
    unswept FOREVER: the next start saw the marker, returned immediately, and
    nothing ever said so again.
    """

    def test_a_failed_store_leaves_no_marker_and_is_retried(self, tmp_path, monkeypatch):
        db_path = _activity_db(tmp_path)
        knowledge_db = _knowledge_db(tmp_path)
        broken = tmp_path / "broken.db"
        broken.write_bytes(b"this is not a database")

        purge.ensure_migrated(db_path, probe_db=broken, knowledge_db=knowledge_db)

        markers = _markers(db_path)
        assert purge.MIGRATION_NAME in markers, "activity.db was not marked"
        assert f"{purge.MIGRATION_NAME}:knowledge" in markers, (
            "a store that swept cleanly was not marked, so it will be re-swept"
        )
        assert f"{purge.MIGRATION_NAME}:probe" not in markers, (
            "the failed store was marked done -- it will never be retried"
        )

        # Second start, with a probe.db that works this time.
        probe_db = _probe_db(tmp_path)
        calls = []
        real = purge.purge_derived_store

        def spy(path, spec, apply):
            calls.append(Path(path).name)
            return real(path, spec, apply)

        monkeypatch.setattr(purge, "purge_derived_store", spy)
        purge.ensure_migrated(db_path, probe_db=probe_db, knowledge_db=knowledge_db)

        assert "probe.db" in calls, "the failed store was not retried"
        assert "knowledge.db" not in calls, (
            "a completed store was swept again -- the marker is not per-store"
        )
        assert FACT_SECRET not in _probe_text(probe_db)
        assert f"{purge.MIGRATION_NAME}:probe" in _markers(db_path)

    def test_the_activity_marker_is_not_written_when_verification_fails(
        self, tmp_path, monkeypatch
    ):
        """A marker means "this store is clean", not "the code ran"."""
        db_path = _activity_db(tmp_path)
        monkeypatch.setattr(purge, "apply_activity_updates", lambda conn, updates: None)

        purge.ensure_migrated(db_path)

        assert purge.MIGRATION_NAME not in _markers(db_path), (
            "the sweep left secrets behind and still claimed to be done"
        )

        # Unpatched, the retry completes and marks.
        monkeypatch.undo()
        purge.ensure_migrated(db_path)
        assert OCR_SECRET not in _activity_text(db_path)
        assert purge.MIGRATION_NAME in _markers(db_path)

    def test_a_clean_store_is_marked_for_every_store(self, tmp_path):
        """Otherwise every start rescans the derived stores as well."""
        db_path = _activity_db(tmp_path)
        probe_db = _probe_db(tmp_path)
        knowledge_db = _knowledge_db(tmp_path)

        purge.ensure_migrated(db_path, probe_db=probe_db, knowledge_db=knowledge_db)

        assert _markers(db_path) == {
            purge.MIGRATION_NAME,
            f"{purge.MIGRATION_NAME}:probe",
            f"{purge.MIGRATION_NAME}:knowledge",
        }

    def test_a_missing_derived_store_is_marked_rather_than_retried_forever(self, tmp_path):
        """A store that does not exist is clean, not failed."""
        db_path = _activity_db(tmp_path)
        purge.ensure_migrated(db_path, probe_db=tmp_path / "absent.db")
        assert f"{purge.MIGRATION_NAME}:probe" in _markers(db_path)


def _memory_db(tmp_path):
    """A warm tier holding one raw pre-fix row, planted through its own writer.

    WarmTier.upsert stores what it is given -- MemoryStore.store is where
    redaction lives -- so this is exactly the shape of a row written before the
    memory package began redacting.
    """
    from contextpulse_memory.storage import WarmTier

    path = tmp_path / "memory.db"
    tier = WarmTier(path)
    tier.upsert(
        key=f"{CONTROL_WORD}/deploy",
        value=f"{CONTROL_WORD} {MEM_SECRET}",
        tags=[CONTROL_WORD, MEM_SECRET],
        expires_at=None,
    )
    tier.close()
    return path


def _memory_cold_db(tmp_path):
    from contextpulse_memory.storage import ColdTier

    path = tmp_path / "memory_cold.db"
    tier = ColdTier(path)
    tier.ingest([{
        "key": f"{CONTROL_WORD}/archived",
        "value": f"{CONTROL_WORD} {MEM_SECRET}",
        "updated_at": time.time(),
        "modality": "memory",
    }])
    tier.close()
    return path


def _table_text(db_path, sql):
    conn = sqlite3.connect(str(db_path))
    try:
        return " ".join(
            " ".join(str(c or "") for c in row) for row in conn.execute(sql)
        )
    finally:
        conn.close()


class TestMemoryStoresAreSwept:
    """Review S-1, second half: ensure_migrated never opened memory.db.

    Every value stored before the memory package began redacting is still raw
    on disk AND still indexed, and memory_search reads it. The hot tier needs
    no sweep -- it is an in-process dict that dies with the daemon.
    """

    def test_warm_rows_are_swept(self, tmp_path):
        db_path = _activity_db(tmp_path)
        memory_db = _memory_db(tmp_path)
        sql = "SELECT key, value, tags FROM memories"
        assert MEM_SECRET in _table_text(memory_db, sql), "fixture not raw -- vacuous"

        purge.ensure_migrated(db_path, memory_db=memory_db)

        assert MEM_SECRET not in _table_text(memory_db, sql), "memory.db was not swept"
        assert CONTROL_WORD in _table_text(memory_db, sql), "context destroyed"

    def test_the_warm_index_is_rebuilt(self, tmp_path):
        db_path = _activity_db(tmp_path)
        memory_db = _memory_db(tmp_path)

        purge.ensure_migrated(db_path, memory_db=memory_db)

        conn = sqlite3.connect(str(memory_db))
        try:
            assert conn.execute(
                "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH ?",
                (CONTROL_WORD,),
            ).fetchone()[0] == 1, "the whole index was lost -- vacuous"
            assert conn.execute(
                "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH ?",
                ("zqmemsweepneedle0123456789ABCD",),
            ).fetchone()[0] == 0
        finally:
            conn.close()

    def test_cold_rows_are_swept(self, tmp_path):
        db_path = _activity_db(tmp_path)
        cold_db = _memory_cold_db(tmp_path)
        sql = "SELECT text_content, summary_json FROM cold_summaries"
        assert MEM_SECRET in _table_text(cold_db, sql), "fixture not raw -- vacuous"

        purge.ensure_migrated(db_path, memory_cold_db=cold_db)

        assert MEM_SECRET not in _table_text(cold_db, sql)

    def test_the_swept_summary_json_is_still_valid_json(self, tmp_path):
        """summary_json is a text column carrying memory KEYS, so it is swept
        like any other -- but a rewrite that broke the JSON would make the
        archive unreadable rather than merely redacted."""
        db_path = _activity_db(tmp_path)
        cold_db = _memory_cold_db(tmp_path)

        purge.ensure_migrated(db_path, memory_cold_db=cold_db)

        conn = sqlite3.connect(str(cold_db))
        try:
            rows = conn.execute("SELECT summary_json FROM cold_summaries").fetchall()
        finally:
            conn.close()
        assert rows, "nothing archived -- vacuous"
        for (blob,) in rows:
            assert isinstance(json.loads(blob), dict)

    def test_each_memory_store_carries_its_own_marker(self, tmp_path):
        db_path = _activity_db(tmp_path)
        purge.ensure_migrated(
            db_path,
            memory_db=_memory_db(tmp_path),
            memory_cold_db=_memory_cold_db(tmp_path),
        )
        markers = _markers(db_path)
        assert f"{purge.MIGRATION_NAME}:memory" in markers
        assert f"{purge.MIGRATION_NAME}:memory_cold" in markers

    def test_the_daemon_resolves_the_memory_paths(self):
        """A sweep nothing passes the paths to is a sweep that never runs."""
        import inspect

        from contextpulse_core import daemon

        source = inspect.getsource(daemon.run_secret_migration)
        assert "memory_db" in source and "memory_cold_db" in source


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
