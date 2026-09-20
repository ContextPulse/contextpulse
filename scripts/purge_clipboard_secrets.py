# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Scrub secrets from clipboard and event rows written before redaction shipped.

Clipboard capture bypassed redact_sensitive() until 2026-09-19, so rows already
in activity.db hold API keys, passwords, tokens and card numbers verbatim --
in the `clipboard` table, in `events.payload`, and in the `events_fts` index
built from that payload. Fixing the capture path does nothing for them. This
does.

DRY RUN IS THE DEFAULT. Nothing is written without --apply.

    python scripts/purge_clipboard_secrets.py              # report only
    python scripts/purge_clipboard_secrets.py --apply      # rewrite rows

OUTPUT IS COUNTS BY CATEGORY ONLY. This script never prints, logs or returns a
matched value -- not truncated, not masked, not hashed. The whole point is that
running it must not do the thing it exists to undo, and a "just the first 20
characters" preview is exactly how a secret ends up in a terminal scrollback,
a CI log, or a pasted report.

Rows are REDACTED IN PLACE, not deleted: the timestamp and the surrounding
context are the useful part of a clipboard history, the secret is not.

events_fts is rebuilt afterwards, and that is not optional. The spine defines
an AFTER INSERT and an AFTER DELETE trigger on `events` but no AFTER UPDATE --
so rewriting a payload leaves the old terms sitting in the full-text index,
where search_all_events would still find them. A purge that skipped the
rebuild would report success and leave every secret searchable.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve the package source the same way the tests do, so the script runs from
# a checkout without an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("screen", "core"):
    _src = _REPO_ROOT / "packages" / _pkg / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from contextpulse_sight.redact import redact_with_counts  # noqa: E402

# Payload keys the spine's text_content generated column reads, in its order.
# Keep in step with _SCHEMA_SQL in contextpulse_core/spine/bus.py.
_PAYLOAD_TEXT_KEYS = ("ocr_text", "transcript", "text")


def resolve_db_path() -> Path:
    """Return the activity database the daemon actually writes to.

    Imported from contextpulse_sight.config rather than reconstructed here:
    ActivityDB() defaults to exactly this value, and it moves with
    CONTEXTPULSE_OUTPUT_DIR / CONTEXTPULSE_ACTIVITY_DB. A second copy of the
    path logic would purge the wrong file on any non-default install and
    report a clean zero for the real one.
    """
    from contextpulse_sight.config import ACTIVITY_DB_PATH

    return Path(ACTIVITY_DB_PATH)


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"REFUSING: no database at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


class Tally:
    """Accumulates category counts. Deliberately cannot hold a value."""

    def __init__(self) -> None:
        self.categories: dict[str, int] = {}
        self.rows_affected = 0
        self.rows_scanned = 0

    def add(self, counts: dict[str, int]) -> None:
        if not counts:
            return
        self.rows_affected += 1
        for category, n in counts.items():
            self.categories[category] = self.categories.get(category, 0) + n

    @property
    def total_matches(self) -> int:
        return sum(self.categories.values())


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


def scan_events(
    conn: sqlite3.Connection, include_titles: bool = False
) -> tuple[Tally, Tally, list[tuple[int, str, str, str]]]:
    """Find event rows carrying secrets.

    Returns (payload_tally, title_tally, [(rowid, payload_json, title, app)]).

    Payload text and window_title/app_name are tallied SEPARATELY because
    they behave differently on real data. Measured on the live database:
    19 flagged rows are clipboard payloads -- the reported leak, matching the
    19 clipboard-table rows one for one -- while 60 are window_title matches
    spread across flow/click, sight/screen_capture and flow/drag events. Those
    60 are overwhelmingly the CREDENTIAL pattern firing on ordinary window
    titles that merely contain "password:" or "token:", which is a browser tab
    on a settings page, not a secret.

    So titles are reported always and rewritten only under --include-titles.
    Scrubbing 60 probably-benign window titles by default would be a far
    larger blast radius than the vulnerability being closed, and window titles
    are already redacted at the MCP boundary on the way out.

    A payload that will not parse is a hard error, not a skip: silently
    passing over it would report the row as clean.
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
        for key in _PAYLOAD_TEXT_KEYS:
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
        merged_title_counts: dict[str, int] = {}
        for counts in (title_counts, app_counts):
            for category, n in counts.items():
                merged_title_counts[category] = merged_title_counts.get(category, 0) + n
        title_tally.add(merged_title_counts)

        rewrite_titles = include_titles and bool(merged_title_counts)
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


def backup_db(conn: sqlite3.Connection, db_path: Path) -> Path:
    """Snapshot the database via SQLite's own backup API before rewriting.

    A file copy is not equivalent under WAL -- it can capture a torn state
    with the committed tail still sitting in the -wal sidecar.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = db_path.with_name(f"{db_path.name}.pre-purge-{stamp}.bak")
    if dest.exists():
        raise SystemExit(f"REFUSING: backup target already exists: {dest}")
    with sqlite3.connect(str(dest)) as target:
        conn.backup(target)
    return dest


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
            [
                (payload, title, app, rowid)
                for rowid, payload, title, app in event_updates
            ],
        )
        # Mandatory. `events` carries AFTER INSERT and AFTER DELETE triggers
        # into events_fts but no AFTER UPDATE, so the index still holds the
        # pre-purge terms until it is rebuilt from the content table.
        conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")


def verify_fts_matches_content(conn: sqlite3.Connection) -> None:
    """Prove events_fts agrees with the rows it indexes.

    Re-scanning the tables says nothing about the index -- they are separate
    artifacts, and the index is the one with no AFTER UPDATE trigger keeping
    it honest.

    The rank=1 argument is load-bearing and was established by measurement,
    not by reading the docs. Against an external-content table whose content
    had been updated with the index left stale -- exactly the hazard here --
    on SQLite 3.50.4:

        integrity-check (no arg)  -> PASSED, did not detect
        integrity-check, rank=0   -> PASSED, did not detect
        integrity-check, rank=1   -> raised DatabaseError

    Only rank=1 compares the index against the content table; the other two
    check the index's internal consistency, which a stale-but-coherent index
    satisfies. Row counts are no help either: count(*) on an external-content
    FTS table is answered from the content table, so it agrees even when the
    index does not.
    """
    conn.execute("INSERT INTO events_fts(events_fts, rank) VALUES('integrity-check', 1)")


def report(title: str, tally: Tally) -> None:
    print(f"\n{title}")
    print(f"  rows scanned:      {tally.rows_scanned}")
    print(f"  rows with secrets: {tally.rows_affected}")
    print(f"  total matches:     {tally.total_matches}")
    if not tally.categories:
        print("  (no categories matched)")
        return
    print("  by category:")
    for category in sorted(tally.categories):
        print(f"    {category:<16} {tally.categories[category]}")


def _force_utf8_console() -> None:
    """Windows hands this script a cp1252 stdout, which mangles the em dash in
    the header and would raise UnicodeEncodeError on any non-cp1252 character.
    Reconfigure at the entry point, before anything prints. errors="replace"
    keeps a scheduled run alive rather than killing it on one glyph.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:  # absent under pytest capture and when piped
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrub secrets from clipboard and event rows (dry run by default).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Database to operate on (default: the path the daemon uses).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite the rows. Without this, nothing is written.",
    )
    parser.add_argument(
        "--include-titles",
        action="store_true",
        help=(
            "Also rewrite events.window_title / app_name. Off by default: on "
            "real data these are mostly CREDENTIAL false positives on ordinary "
            "window titles. They are reported either way."
        ),
    )
    args = parser.parse_args(argv)

    db_path = args.db if args.db is not None else resolve_db_path()
    conn = open_db(db_path)
    try:
        for required in ("clipboard", "events", "events_fts"):
            if not table_exists(conn, required):
                raise SystemExit(
                    f"REFUSING: {db_path} has no '{required}' table -- this does not "
                    "look like a ContextPulse activity database."
                )

        clip_tally, clip_updates = scan_clipboard(conn)
        evt_tally, title_tally, evt_updates = scan_events(conn, args.include_titles)

        mode = "APPLY" if args.apply else "DRY RUN (nothing written)"
        print(f"purge_clipboard_secrets — {mode}")
        print(f"database: {db_path}")
        report("clipboard table", clip_tally)
        report("events table (payload text)", evt_tally)
        report("events table (window_title / app_name)", title_tally)
        if title_tally.rows_affected and not args.include_titles:
            print(
                "  NOTE: reported but NOT rewritten. Pass --include-titles to "
                "scrub these too; on real data they are mostly the CREDENTIAL "
                "pattern firing on ordinary window titles."
            )

        if not clip_updates and not evt_updates:
            print("\nNothing to purge.")
            return 0

        if not args.apply:
            print(
                f"\n{len(clip_updates)} clipboard row(s) and {len(evt_updates)} event "
                "row(s) would be redacted in place, and events_fts rebuilt."
            )
            print("Re-run with --apply to write the changes.")
            return 0

        backup = backup_db(conn, db_path)
        print(f"\nbackup written: {backup}")
        apply_updates(conn, clip_updates, evt_updates)
        print(
            f"applied: {len(clip_updates)} clipboard row(s), "
            f"{len(evt_updates)} event row(s), events_fts rebuilt."
        )

        # Verify rather than assert: re-scan the rewritten rows and require
        # zero remaining matches. "The UPDATE ran" is not the same claim as
        # "the secrets are gone".
        clip_after, _ = scan_clipboard(conn)
        evt_after, title_after, _ = scan_events(conn, args.include_titles)
        remaining = clip_after.total_matches + evt_after.total_matches
        if args.include_titles:
            remaining += title_after.total_matches
        if remaining:
            print(
                f"\nFAILED VERIFICATION: {clip_after.total_matches} clipboard and "
                f"{evt_after.total_matches} event payload matches still present "
                "after purge.",
                file=sys.stderr,
            )
            return 1
        try:
            verify_fts_matches_content(conn)
        except sqlite3.DatabaseError as exc:
            print(
                f"\nFAILED VERIFICATION: events_fts does not match the rows it "
                f"indexes ({exc}). The rows are clean but the search index may "
                "still return the old text. Rebuild it before trusting this run.",
                file=sys.stderr,
            )
            return 1
        print(
            "verified: re-scan finds 0 remaining matches in both tables, and "
            "events_fts passes FTS5 integrity-check against them."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    _force_utf8_console()
    sys.exit(main())
