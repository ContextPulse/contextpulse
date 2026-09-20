# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Scrub secrets from rows written before redaction shipped, and do it once.

THIS IS THE LIBRARY. ``scripts/purge_clipboard_secrets.py`` is the interactive
CLI over it, and :func:`ensure_migrated` is the unattended path the daemon and
the MCP server call at startup.

Why both. Output redaction cannot close a query oracle: as long as raw rows sit
in the store, a search that counts matches leaks them one character at a time
(the adversarial review proved it end to end). Redacting the rows themselves is
the only fix that closes that class, and it cannot wait for a human to remember
to run a script.

SCOPE IS THREE DATABASES, NOT ONE. activity.db is the primary store, but
probe.db's ``facts`` and knowledge.db's ``observations`` are SECOND AND THIRD
COPIES of the same captured text, written by the consolidator and the knowledge
bridge. A purge that cleaned only activity.db reported "verified: 0 remaining
matches" while both of the others still served the secret through live MCP
tools.

OUTPUT IS COUNTS BY CATEGORY ONLY. Nothing here ever returns, prints or logs a
matched value -- not truncated, not masked, not hashed. Running the cleanup
must not do the thing it exists to undo.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from contextpulse_core.redact import _redactable_payload_keys, redact_with_counts

logger = logging.getLogger(__name__)

# Bumped when the redaction patterns change in a way that makes an already-
# migrated store worth re-sweeping. The marker is per-name, so raising this
# re-runs the sweep exactly once more.
MIGRATION_NAME = "secret-redaction-v2"

_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS cp_migrations (
    name TEXT PRIMARY KEY,
    applied_at REAL NOT NULL,
    rows_affected INTEGER NOT NULL DEFAULT 0
)
"""


@dataclass
class Tally:
    """Accumulates category counts. Deliberately cannot hold a value."""

    categories: dict[str, int] = field(default_factory=dict)
    rows_affected: int = 0
    rows_scanned: int = 0

    def add(self, counts: dict[str, int]) -> None:
        if not counts:
            return
        self.rows_affected += 1
        for category, n in counts.items():
            self.categories[category] = self.categories.get(category, 0) + n

    @property
    def total_matches(self) -> int:
        return sum(self.categories.values())


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def open_db(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"REFUSING: no database at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if not read_only:
        # A pragma is a WRITE. Skipped on a read-only open so a dry run is
        # literally side-effect free rather than nearly so (review N2).
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── activity.db ─────────────────────────────────────────────────────

def scan_clipboard(conn: sqlite3.Connection) -> tuple[Tally, list[tuple[int, str]]]:
    """Find clipboard rows carrying secrets. Returns (tally, pending updates)."""
    tally = Tally()
    updates: list[tuple[int, str]] = []
    for row in conn.execute("SELECT id, text FROM clipboard").fetchall():
        tally.rows_scanned += 1
        cleaned, counts = redact_with_counts(row["text"] or "")
        if counts:
            tally.add(counts)
            updates.append((row["id"], cleaned))
    return tally, updates


def scan_activity(
    conn: sqlite3.Connection, include_titles: bool = False
) -> tuple[Tally, Tally, list[tuple[str, str, str, int]]]:
    """Find `activity` rows carrying secrets. Returns (ocr_tally, title_tally, updates).

    THE LARGEST TEXT STORE IN THE PRODUCT, and the first sweep never opened it
    (review B-2). It is guaranteed to hold unredacted secrets after upgrade for
    two reasons the redaction work itself created: sixteen pattern shapes that
    did not exist when those rows were OCR'd, and the word-boundary gap that
    left every glued token raw. Add any period with redact_ocr_text=False.

    Titles are tallied SEPARATELY and rewritten only under include_titles, for
    the same measured reason as `events` -- on real data the CREDENTIAL pattern
    fires overwhelmingly on ordinary window titles containing "password:" or
    "token:", and scrubbing those by default is a far larger blast radius than
    the vulnerability being closed.
    """
    ocr_tally = Tally()
    title_tally = Tally()
    updates: list[tuple[str, str, str, int]] = []

    for row in conn.execute(
        "SELECT id, ocr_text, window_title, app_name FROM activity"
    ).fetchall():
        ocr_tally.rows_scanned += 1
        title_tally.rows_scanned += 1

        ocr, ocr_counts = redact_with_counts(row["ocr_text"] or "")
        ocr_tally.add(ocr_counts)

        title, title_counts = redact_with_counts(row["window_title"] or "")
        app, app_counts = redact_with_counts(row["app_name"] or "")
        merged: dict[str, int] = {}
        for counts in (title_counts, app_counts):
            for category, n in counts.items():
                merged[category] = merged.get(category, 0) + n
        title_tally.add(merged)

        rewrite_titles = include_titles and bool(merged)
        if ocr_counts or rewrite_titles:
            updates.append((
                # ocr_text is nullable and must stay NULL if it was NULL --
                # writing "" would turn "not OCR'd yet" into "OCR'd, empty".
                ocr if row["ocr_text"] is not None else None,
                title if rewrite_titles else (row["window_title"] or ""),
                app if rewrite_titles else (row["app_name"] or ""),
                row["id"],
            ))

    return ocr_tally, title_tally, updates


def apply_activity_updates(
    conn: sqlite3.Connection, updates: list[tuple[str, str, str, int]]
) -> None:
    """Rewrite `activity` rows and rebuild its FTS index.

    Kept separate from apply_updates rather than folded into it: that function's
    signature is part of the CLI's contract and is monkeypatched in tests.

    The rebuild is belt AND braces. Unlike `events`, `activity` DOES carry an
    AFTER UPDATE trigger into activity_fts, so the index should already be in
    step -- but a store whose triggers failed to create (ActivityDB._init_schema
    swallows OperationalError) would otherwise keep serving the pre-sweep terms,
    and a stale index is still an oracle.
    """
    if not updates:
        return
    with conn:  # one transaction; rolls back on any exception
        conn.executemany(
            "UPDATE activity SET ocr_text = ?, window_title = ?, app_name = ? WHERE id = ?",
            updates,
        )
        if table_exists(conn, "activity_fts"):
            conn.execute("INSERT INTO activity_fts(activity_fts) VALUES('rebuild')")


def scan_events(
    conn: sqlite3.Connection, include_titles: bool = False
) -> tuple[Tally, Tally, list[tuple[int, str, str, str]]]:
    """Find event rows carrying secrets.

    Returns (payload_tally, title_tally, [(rowid, payload_json, title, app)]).

    Payload text and window_title/app_name are tallied SEPARATELY because they
    behave differently on real data. Measured on the live database: 19 flagged
    rows are clipboard payloads -- the reported leak -- while 60 are
    window_title matches, overwhelmingly the CREDENTIAL pattern firing on
    ordinary titles that merely contain "password:" or "token:". Those are a
    browser tab on a settings page, not a secret.

    So titles are reported always and rewritten only under --include-titles.
    Scrubbing 60 probably-benign titles by default would be a far larger blast
    radius than the vulnerability being closed, and titles are already redacted
    at the MCP boundary on the way out.

    A payload that will not parse is a hard error, not a skip: silently passing
    over it would report the row as clean.
    """
    payload_tally = Tally()
    title_tally = Tally()
    updates: list[tuple[int, str, str, str]] = []
    unparsable = 0

    for row in conn.execute(
        "SELECT rowid, payload, window_title, app_name FROM events"
    ).fetchall():
        payload_tally.rows_scanned += 1
        title_tally.rows_scanned += 1
        raw_payload = row["payload"] or "{}"

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            unparsable += 1
            continue
        if not isinstance(payload, dict):
            unparsable += 1
            continue

        payload_counts: dict[str, int] = {}
        for key in _redactable_payload_keys():
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                continue
            cleaned, counts = redact_with_counts(value)
            if counts:
                payload[key] = cleaned
                for category, n in counts.items():
                    payload_counts[category] = payload_counts.get(category, 0) + n
        payload_tally.add(payload_counts)

        title, title_counts = redact_with_counts(row["window_title"] or "")
        app, app_counts = redact_with_counts(row["app_name"] or "")
        merged: dict[str, int] = {}
        for counts in (title_counts, app_counts):
            for category, n in counts.items():
                merged[category] = merged.get(category, 0) + n
        title_tally.add(merged)

        rewrite_titles = include_titles and bool(merged)
        if payload_counts or rewrite_titles:
            updates.append((
                row["rowid"],
                json.dumps(payload, ensure_ascii=False),
                title if rewrite_titles else (row["window_title"] or ""),
                app if rewrite_titles else (row["app_name"] or ""),
            ))

    if unparsable:
        raise SystemExit(
            f"REFUSING: {unparsable} event row(s) have a payload that is not a JSON "
            "object. They cannot be scanned, so this run cannot claim the table is "
            "clean. Investigate before purging."
        )

    return payload_tally, title_tally, updates


def apply_updates(
    conn: sqlite3.Connection,
    clipboard_updates: list[tuple[int, str]],
    event_updates: list[tuple[int, str, str, str]],
) -> None:
    with conn:  # one transaction; rolls back on any exception
        conn.executemany(
            "UPDATE clipboard SET text = ? WHERE id = ?",
            [(text, row_id) for row_id, text in clipboard_updates],
        )
        conn.executemany(
            "UPDATE events SET payload = ?, window_title = ?, app_name = ? WHERE rowid = ?",
            [(p, t, a, r) for r, p, t, a in event_updates],
        )
        # Mandatory. `events` carries AFTER INSERT and AFTER DELETE triggers
        # into events_fts but no AFTER UPDATE, so the index still holds the
        # pre-purge terms until it is rebuilt from the content table.
        conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")


def verify_fts_matches_content(conn: sqlite3.Connection) -> None:
    """Prove events_fts agrees with the rows it indexes.

    rank=1 is load-bearing and was established by measurement. Against an
    external-content table whose content was updated with the index left stale
    -- exactly the hazard here -- on SQLite 3.50.4:

        integrity-check (no arg)  -> PASSED, did not detect
        integrity-check, rank=0   -> PASSED, did not detect
        integrity-check, rank=1   -> raised DatabaseError

    Only rank=1 compares the index against the content table. Row counts are no
    help either: count(*) on an external-content FTS table is answered from the
    content table, so it agrees even when the index does not.
    """
    conn.execute("INSERT INTO events_fts(events_fts, rank) VALUES('integrity-check', 1)")


# ── probe.db (facts) and knowledge.db (observations) ────────────────

def scan_text_table(
    conn: sqlite3.Connection, table: str, key_column: str, text_columns: tuple[str, ...]
) -> tuple[Tally, list[tuple]]:
    """Generic scanner for a derived store's text columns.

    Returns (tally, [(*new_values, key)]) ready for an UPDATE ... WHERE key = ?.
    Used for probe.db's `facts` (entity, fact) and knowledge.db's
    `observations` (content, window_title, url) -- both hold text COPIED or
    DERIVED from events, and neither is touched by an activity.db purge.
    """
    tally = Tally()
    updates: list[tuple] = []
    cols = ", ".join(text_columns)
    for row in conn.execute(f"SELECT {key_column}, {cols} FROM {table}").fetchall():
        tally.rows_scanned += 1
        new_values: list[str] = []
        row_counts: dict[str, int] = {}
        for column in text_columns:
            value = row[column]
            if not isinstance(value, str) or not value:
                new_values.append(value)
                continue
            cleaned, counts = redact_with_counts(value)
            new_values.append(cleaned)
            for category, n in counts.items():
                row_counts[category] = row_counts.get(category, 0) + n
        if row_counts:
            tally.add(row_counts)
            updates.append((*new_values, row[key_column]))
    return tally, updates


def apply_text_table(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    text_columns: tuple[str, ...],
    updates: list[tuple],
) -> None:
    if not updates:
        return
    assignments = ", ".join(f"{c} = ?" for c in text_columns)
    with conn:
        conn.executemany(
            f"UPDATE {table} SET {assignments} WHERE {key_column} = ?", updates
        )


def existing_text_columns(
    conn: sqlite3.Connection, table: str, candidates: tuple[str, ...]
) -> tuple[str, ...]:
    """Intersect a candidate column list with what the table actually has.

    Schemas differ across versions and a hardcoded column list would make the
    whole sweep raise on an older store -- which, in ensure_migrated, would
    take the daemon down at startup over a cleanup.
    """
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return tuple(c for c in candidates if c in have)


PROBE_FACTS = ("facts", "id", ("entity", "fact"))
KNOWLEDGE_OBSERVATIONS = ("observations", "id", ("content", "window_title", "url"))


def purge_derived_store(db_path: Path, spec: tuple, apply: bool) -> Tally:
    """Scan (and optionally rewrite) one derived store. Missing DB = empty tally."""
    table, key_column, candidates = spec
    if not db_path.exists():
        return Tally()
    conn = open_db(db_path, read_only=not apply)
    try:
        if not table_exists(conn, table):
            return Tally()
        columns = existing_text_columns(conn, table, candidates)
        if not columns:
            return Tally()
        tally, updates = scan_text_table(conn, table, key_column, columns)
        if apply:
            apply_text_table(conn, table, key_column, columns, updates)
        return tally
    finally:
        conn.close()


# ── the unattended path ─────────────────────────────────────────────

def migration_applied(conn: sqlite3.Connection, name: str = MIGRATION_NAME) -> bool:
    conn.execute(_MIGRATIONS_DDL)
    row = conn.execute("SELECT 1 FROM cp_migrations WHERE name = ?", (name,)).fetchone()
    return row is not None


def mark_migrated(conn: sqlite3.Connection, name: str, rows: int) -> None:
    """Record that ONE store is clean. Call only after verifying it."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO cp_migrations (name, applied_at, rows_affected) "
            "VALUES (?, ?, ?)",
            (name, time.time(), rows),
        )


def sweep_activity_db(conn: sqlite3.Connection) -> tuple[dict[str, int], int, bool]:
    """Sweep clipboard, events and activity in one database.

    Returns (counts, rows_affected, verified). `verified` is a re-scan result,
    not an assertion that the UPDATE ran: "the statements executed" and "the
    secrets are gone" are different claims, and only the second one may write a
    marker. The re-scan is skipped when nothing was rewritten -- the first scan
    already proved that store clean, so paying for a second full pass would
    double the cost of the common case for no information.
    """
    counts: dict[str, int] = {}

    clip_tally, clip_updates = scan_clipboard(conn)
    evt_tally, _evt_titles, evt_updates = scan_events(conn, include_titles=False)
    act_tally, _act_titles, act_updates = scan_activity(conn, include_titles=False)

    if clip_updates or evt_updates:
        apply_updates(conn, clip_updates, evt_updates)
    apply_activity_updates(conn, act_updates)

    for tally in (clip_tally, evt_tally, act_tally):
        for category, n in tally.categories.items():
            counts[category] = counts.get(category, 0) + n
    rows = clip_tally.rows_affected + evt_tally.rows_affected + act_tally.rows_affected

    verified = True
    if clip_updates or evt_updates or act_updates:
        remaining = (
            scan_clipboard(conn)[0].total_matches
            + scan_events(conn, include_titles=False)[0].total_matches
            + scan_activity(conn, include_titles=False)[0].total_matches
        )
        verified = remaining == 0
        if not verified:
            logger.warning(
                "secret migration: activity.db still has %d match(es) after the "
                "sweep; not marking it done so the next start retries", remaining,
            )

    return counts, rows, verified


def sweep_derived_store(db_path: Path, spec: tuple) -> tuple[Tally, bool]:
    """Sweep one derived store and re-scan it. Returns (tally, verified)."""
    tally = purge_derived_store(db_path, spec, apply=True)
    if not tally.rows_affected:
        return tally, True
    after = purge_derived_store(db_path, spec, apply=False)
    return tally, after.total_matches == 0


def ensure_migrated(
    activity_db: Path,
    probe_db: Path | None = None,
    knowledge_db: Path | None = None,
    name: str = MIGRATION_NAME,
) -> dict[str, int]:
    """Redact pre-fix rows in place, once PER STORE, before anything serves them.

    Called at daemon startup AND on first use of the sight MCP server, because
    they are separate processes and either can be the one that runs first. The
    markers live in a table inside activity.db rather than in a file, so the
    check and the claim are in the same transactional store.

    ONE MARKER PER STORE, WRITTEN ONLY AFTER THAT STORE IS VERIFIED CLEAN.
    The first version wrote a single marker inside the activity.db block and
    swept probe.db and knowledge.db afterwards, warning and continuing on
    failure. So a locked probe.db -- or a tray quit during the multi-minute
    sweep, which kills it outright because start_secret_migration runs on a
    daemon thread -- left those stores unswept FOREVER: the next start saw the
    marker, returned immediately, and nothing ever said so again (review B-3).

    A store that fails is simply not marked, so the next start retries it; a
    store that succeeded is not swept twice. A store that does not exist is
    clean, not failed, and IS marked -- otherwise every start rescans nothing
    forever.

    Returns a {category: count} dict of what it rewrote. COUNTS ONLY -- no
    value is returned, logged or raised.

    Failure is contained: this is a cleanup, not a precondition for capture, so
    an unmigratable store logs and returns rather than taking the daemon down.
    The search paths redact independently, so a failed migration degrades to
    the previous behaviour instead of opening a hole.
    """
    counts: dict[str, int] = {}
    if not activity_db.exists():
        return counts
    try:
        conn = open_db(activity_db)
    except SystemExit:
        return counts
    try:
        if not migration_applied(conn, name):
            try:
                found, rows, verified = sweep_activity_db(conn)
            except (sqlite3.DatabaseError, SystemExit) as exc:
                logger.warning("secret migration %s did not complete: %s", name, exc)
                return counts
            for category, n in found.items():
                counts[category] = counts.get(category, 0) + n
            if verified:
                mark_migrated(conn, name, rows)
                logger.info(
                    "secret migration %s: rewrote %d row(s) in activity.db; "
                    "categories=%s", name, rows, sorted(found),
                )

        for db_path, spec, label in (
            (probe_db, PROBE_FACTS, "probe"),
            (knowledge_db, KNOWLEDGE_OBSERVATIONS, "knowledge"),
        ):
            if db_path is None:
                continue
            marker = f"{name}:{label}"
            if migration_applied(conn, marker):
                continue
            try:
                tally, verified = sweep_derived_store(Path(db_path), spec)
            except (sqlite3.DatabaseError, SystemExit) as exc:
                # No marker, so the next start retries this store and only
                # this store.
                logger.warning("secret migration: %s.db not swept: %s", label, exc)
                continue
            for category, n in tally.categories.items():
                counts[category] = counts.get(category, 0) + n
            if not verified:
                logger.warning(
                    "secret migration: %s.db still has matches after the sweep; "
                    "not marking it done so the next start retries", label,
                )
                continue
            mark_migrated(conn, marker, tally.rows_affected)
            if tally.rows_affected:
                logger.info(
                    "secret migration: rewrote %d row(s) in %s.db; categories=%s",
                    tally.rows_affected, label, sorted(tally.categories),
                )
    finally:
        conn.close()

    return counts
