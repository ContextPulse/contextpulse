"""Tests for scripts/purge_clipboard_secrets.py against a fixture database.

The script exists for rows written before clipboard redaction shipped. Its two
ways of being quietly wrong are both covered here:

  1. Reporting success while leaving secrets behind -- specifically in
     events_fts, which `events` has no AFTER UPDATE trigger for, so a payload
     rewrite leaves the old terms searchable unless the index is rebuilt.
  2. Printing a value. Its entire contract is counts by category, and a
     "masked preview" of a secret is still a secret in a scrollback.

Never run this suite against the live database.
"""

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest
from contextpulse_core.spine import EventBus
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.sight_module import SightModule

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "purge_clipboard_secrets.py"

# Synthetic. Chosen to span several categories so the by-category breakdown
# has something to get wrong.
# Each value is a single FTS term. unicode61 treats '-' and '_' as separators,
# so "sk-XXX" and "ghp_XXX" index as the trailing run alone -- probing for a
# prefix of it would return 0 whether or not the secret is in the index, and
# the post-purge assertions would pass vacuously.
CLIP_SECRET = "zqpurgeclipneedle0123456789"
OCR_SECRET = "zqpurgeocrneedle0123456789abcdefghij"  # 36 chars: ghp_ needs 36+
CLIP_TEXT = f"deploy log\nsk-{CLIP_SECRET}\npassword: zqpurgepw123\n"
OCR_TEXT = f"terminal\nghp_{OCR_SECRET}\n"
TITLE_TEXT = "session 4111-1111-1111-1111"


@pytest.fixture(scope="module")
def purge():
    spec = importlib.util.spec_from_file_location("purge_clipboard_secrets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture_db(tmp_path):
    """An activity.db holding raw pre-fix rows, built through the real writers."""
    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    bus = EventBus(db_path)
    module = SightModule()
    module.register(bus.emit)
    module.start()

    now = time.time()
    db.record_clipboard(timestamp=now, text=CLIP_TEXT)
    db.record_clipboard(timestamp=now + 1, text="a perfectly ordinary clipboard note")
    module.emit_clipboard(timestamp=now, text=CLIP_TEXT, hash_val="deadbeef")
    module.emit_ocr(
        timestamp=now,
        frame_path="/tmp/f.jpg",
        ocr_text=OCR_TEXT,
        confidence=0.9,
        app_name="Terminal",
        window_title=TITLE_TEXT,
    )
    module.emit_window_focus("Chrome", "an ordinary window title")

    module.stop()
    bus.close()
    db.close()
    return db_path


def _fts_hits(db_path, term):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT count(*) FROM events_fts WHERE events_fts MATCH ?", (term,)
        ).fetchone()[0]
    finally:
        conn.close()


def _all_text(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        blob = " ".join(r[0] or "" for r in conn.execute("SELECT text FROM clipboard"))
        blob += " ".join(
            f"{r[0]} {r[1]} {r[2]}"
            for r in conn.execute("SELECT payload, window_title, app_name FROM events")
        )
        return blob
    finally:
        conn.close()


class TestDryRunIsTheDefault:
    def test_no_args_writes_nothing(self, purge, fixture_db, capsys):
        before = fixture_db.read_bytes()
        rc = purge.main(["--db", str(fixture_db)])
        out = capsys.readouterr().out

        assert rc == 0
        assert "DRY RUN" in out
        assert CLIP_SECRET in _all_text(fixture_db), "dry run must not modify rows"
        assert fixture_db.read_bytes() == before

    def test_dry_run_reports_categories_not_values(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db)])
        out = capsys.readouterr().out

        assert "API_KEY" in out
        assert "CREDENTIAL" in out
        assert "GH_TOKEN" in out
        assert "CC" in out
        # The contract: never a value, in any form.
        for value in (CLIP_SECRET, OCR_SECRET, "zqpurgepw123", "4111"):
            assert value not in out, f"output leaked {value!r}"
        # Nor any fragment of one long enough to be useful.
        assert CLIP_SECRET[:10] not in out
        assert "sk-" not in out

    def test_counts_distinguish_rows_from_matches(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db)])
        out = capsys.readouterr().out
        # One of the two clipboard rows is clean; the report must say so
        # rather than implying the whole table is dirty.
        assert "rows scanned:      2" in out
        assert "rows with secrets: 1" in out


class TestApplyRemovesSecretsEverywhere:
    def test_apply_scrubs_tables_and_fts(self, purge, fixture_db, capsys):
        for term in (OCR_SECRET, CLIP_SECRET):
            assert _fts_hits(fixture_db, term) == 1, (
                f"fixture is wrong: {term!r} must be searchable BEFORE the purge, "
                "or the post-purge zero proves nothing"
            )

        rc = purge.main(["--db", str(fixture_db), "--apply"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "APPLY" in out

        stored = _all_text(fixture_db)
        for value in (CLIP_SECRET, OCR_SECRET, "zqpurgepw123"):
            assert value not in stored, f"{value!r} survived the purge"

        assert _fts_hits(fixture_db, OCR_SECRET) == 0, "events_fts was not rebuilt"
        assert _fts_hits(fixture_db, CLIP_SECRET) == 0

        # Positive control: the rebuild must not have emptied the index.
        assert _fts_hits(fixture_db, "terminal") == 1

    def test_apply_verifies_itself(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db), "--apply"])
        out = capsys.readouterr().out
        assert "verified: re-scan finds 0 remaining matches" in out
        assert "integrity-check" in out

    def test_verification_covers_the_index_not_only_the_tables(
        self, purge, fixture_db, capsys, monkeypatch
    ):
        # A re-scan of `clipboard` and `events` passes whether or not the
        # index was rebuilt. Simulate a skipped rebuild and require the run
        # to fail rather than print "verified".
        real_apply = purge.apply_updates

        def apply_without_rebuild(conn, clipboard_updates, event_updates):
            real_apply(conn, clipboard_updates, event_updates)
            # Re-introduce the exact desync a skipped rebuild produces: an
            # index row whose terms no longer match the content row it points
            # at. Note a row-count check would NOT see this -- count(*) on an
            # external-content FTS table is answered from the content table.
            conn.execute(
                "INSERT INTO events_fts(rowid, window_title, app_name, text_content) "
                "VALUES (999999, 'stale', 'stale', 'stale')"
            )
            conn.commit()

        monkeypatch.setattr(purge, "apply_updates", apply_without_rebuild)
        rc = purge.main(["--db", str(fixture_db), "--apply"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "FAILED VERIFICATION" in err
        assert "events_fts" in err

    def test_apply_output_still_never_prints_a_value(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db), "--apply"])
        out = capsys.readouterr().out
        for value in (CLIP_SECRET, OCR_SECRET, "zqpurgepw123"):
            assert value not in out

    def test_apply_writes_a_backup_first(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db), "--apply"])
        backups = list(fixture_db.parent.glob("activity.db.pre-purge-*.bak"))
        assert len(backups) == 1
        # The backup is a real, readable database that still holds the
        # original rows -- the purge is recoverable, not destructive.
        conn = sqlite3.connect(str(backups[0]))
        try:
            rows = [r[0] for r in conn.execute("SELECT text FROM clipboard")]
        finally:
            conn.close()
        assert any(CLIP_SECRET in (r or "") for r in rows)

    def test_apply_preserves_clean_rows_and_timestamps(self, purge, fixture_db):
        conn = sqlite3.connect(str(fixture_db))
        before = conn.execute(
            "SELECT id, timestamp, text FROM clipboard ORDER BY id"
        ).fetchall()
        conn.close()

        purge.main(["--db", str(fixture_db), "--apply"])

        conn = sqlite3.connect(str(fixture_db))
        after = conn.execute(
            "SELECT id, timestamp, text FROM clipboard ORDER BY id"
        ).fetchall()
        conn.close()

        assert len(after) == len(before), "rows were deleted, not redacted"
        assert [r[0] for r in after] == [r[0] for r in before]
        assert [r[1] for r in after] == [r[1] for r in before]
        # The clean row is untouched byte for byte.
        assert after[1][2] == before[1][2]

    def test_apply_is_idempotent(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db), "--apply"])
        capsys.readouterr()
        rc = purge.main(["--db", str(fixture_db)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Nothing to purge." in out

    def test_payload_stays_valid_json_with_other_keys_intact(self, purge, fixture_db):
        purge.main(["--db", str(fixture_db), "--apply"])
        conn = sqlite3.connect(str(fixture_db))
        try:
            payloads = [
                json.loads(r[0]) for r in conn.execute("SELECT payload FROM events")
            ]
        finally:
            conn.close()
        clip_payloads = [p for p in payloads if "hash" in p]
        assert clip_payloads, "clipboard event payload disappeared"
        assert clip_payloads[0]["hash"] == "deadbeef", "non-text keys were clobbered"


class TestPayloadKeyCoverage:
    """burst_text and correction_text are scanned too.

    Only the first three of the spine's five text keys are indexed by the
    events_fts trigger, so scanning "what FTS indexes" would leave typed-text
    bursts unexamined -- and nothing redacts those at write time. On the live
    database they happen to be clean today, so this fixture is the only
    evidence the widened scan does anything.
    """

    BURST_SECRET = "zqburstneedle0123456789abcdefghij0123"

    def _db_with_burst(self, tmp_path):
        from contextpulse_core.spine import ContextEvent, EventBus, EventType, Modality

        db_path = tmp_path / "activity.db"
        db = ActivityDB(db_path=db_path)
        bus = EventBus(db_path)
        bus.emit(
            ContextEvent(
                timestamp=time.time(),
                modality=Modality.KEYS,
                event_type=EventType.TYPING_BURST,
                app_name="Editor",
                window_title="notes",
                payload={"burst_text": f"ghp_{self.BURST_SECRET}"},
            )
        )
        bus.close()
        db.close()
        return db_path

    def test_burst_text_is_scanned(self, purge, tmp_path, capsys):
        db_path = self._db_with_burst(tmp_path)
        purge.main(["--db", str(db_path)])
        out = capsys.readouterr().out
        assert "GH_TOKEN" in out, "burst_text was not scanned"
        assert self.BURST_SECRET not in out

    def test_burst_text_is_purged(self, purge, tmp_path):
        db_path = self._db_with_burst(tmp_path)
        purge.main(["--db", str(db_path), "--apply"])
        conn = sqlite3.connect(str(db_path))
        try:
            payloads = " ".join(r[0] for r in conn.execute("SELECT payload FROM events"))
        finally:
            conn.close()
        assert self.BURST_SECRET not in payloads

    def test_scan_keys_track_the_spine_schema(self, purge):
        from contextpulse_core.spine.events import _TEXT_PAYLOAD_KEYS

        # If a text key is added to the event schema it must not silently
        # escape the purge scan.
        assert tuple(purge._PAYLOAD_TEXT_KEYS) == tuple(_TEXT_PAYLOAD_KEYS)


class TestWindowTitleScope:
    """Titles are reported always, rewritten only on request.

    On the live database 60 of 79 flagged event rows are window_title hits,
    almost all the CREDENTIAL pattern firing on ordinary titles containing
    "password:" or "token:". Scrubbing those by default would rewrite far
    more than the vulnerability requires.
    """

    def test_titles_are_reported_in_their_own_section(self, purge, fixture_db, capsys):
        purge.main(["--db", str(fixture_db)])
        out = capsys.readouterr().out
        assert "events table (payload text)" in out
        assert "events table (window_title / app_name)" in out
        assert "NOT rewritten" in out

    def test_titles_survive_a_default_apply(self, purge, fixture_db):
        purge.main(["--db", str(fixture_db), "--apply"])
        assert TITLE_TEXT in _all_text(fixture_db)

    def test_include_titles_scrubs_them(self, purge, fixture_db):
        purge.main(["--db", str(fixture_db), "--apply", "--include-titles"])
        stored = _all_text(fixture_db)
        assert "4111-1111-1111-1111" not in stored
        # And the payload secrets are still handled in the same run.
        assert CLIP_SECRET not in stored

    def test_include_titles_leaves_clean_titles_alone(self, purge, fixture_db):
        purge.main(["--db", str(fixture_db), "--apply", "--include-titles"])
        assert "an ordinary window title" in _all_text(fixture_db)


class TestRefusals:
    def test_missing_database_is_a_refusal_not_a_zero(self, purge, tmp_path):
        with pytest.raises(SystemExit) as exc:
            purge.main(["--db", str(tmp_path / "nope.db")])
        assert "REFUSING" in str(exc.value)

    def test_wrong_schema_is_a_refusal(self, purge, tmp_path):
        stray = tmp_path / "stray.db"
        conn = sqlite3.connect(str(stray))
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
        conn.close()
        with pytest.raises(SystemExit) as exc:
            purge.main(["--db", str(stray)])
        assert "REFUSING" in str(exc.value)

    def test_syntactically_malformed_payload_cannot_be_stored_at_all(
        self, purge, fixture_db
    ):
        # Worth pinning: `events.text_content` is a generated column over
        # json_extract(payload, ...), and SQLite validates that expression on
        # WRITE. The table physically cannot hold non-JSON, so the script's
        # JSONDecodeError branch is unreachable through normal writes -- the
        # reachable case is the one below.
        conn = sqlite3.connect(str(fixture_db))
        try:
            with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
                conn.execute("UPDATE events SET payload = 'not json' WHERE rowid = 1")
        finally:
            conn.close()

    def test_non_object_payload_is_a_refusal(self, purge, fixture_db):
        # Valid JSON, wrong shape — a scalar rather than an object. It has no
        # text keys to scan, and reporting it as clean would be a lie.
        conn = sqlite3.connect(str(fixture_db))
        conn.execute("""UPDATE events SET payload = '"just a string"' WHERE rowid = 1""")
        conn.commit()
        conn.close()
        with pytest.raises(SystemExit) as exc:
            purge.main(["--db", str(fixture_db)])
        assert "REFUSING" in str(exc.value)


class TestDbPathResolution:
    def test_resolves_the_same_path_the_daemon_writes_to(self, purge):
        from contextpulse_sight.config import ACTIVITY_DB_PATH

        assert purge.resolve_db_path() == Path(ACTIVITY_DB_PATH)
