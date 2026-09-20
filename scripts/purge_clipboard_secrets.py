# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Scrub secrets from rows written before redaction shipped.

Capture bypassed redact_sensitive() until 2026-09-19, so rows already on disk
hold API keys, passwords, tokens and card numbers verbatim -- in `clipboard`,
in `events.payload`, in the `events_fts` index built from that payload, and in
TWO DERIVED STORES: probe.db's `facts`, written by the nightly consolidator,
and knowledge.db's `observations`, written by the knowledge bridge. Fixing the
capture path does nothing for any of them. This does.

DRY RUN IS THE DEFAULT. Nothing is written without --apply.

    python scripts/purge_clipboard_secrets.py              # report only
    python scripts/purge_clipboard_secrets.py --apply      # rewrite rows

The scanning and rewriting live in contextpulse_core.purge, which the daemon
also calls at startup (ensure_migrated). One implementation, so an unattended
sweep and a hand-run sweep cannot disagree about what counts as a secret.

OUTPUT IS COUNTS BY CATEGORY ONLY. This script never prints, logs or returns a
matched value -- not truncated, not masked, not hashed. The whole point is that
running it must not do the thing it exists to undo, and a "just the first 20
characters" preview is exactly how a secret ends up in a terminal scrollback, a
CI log, or a pasted report.

Rows are REDACTED IN PLACE, not deleted: the timestamp and the surrounding
context are the useful part of a history, the secret is not.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve the package source the same way the tests do, so the script runs from
# a checkout without an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("screen", "core", "knowledge"):
    _src = _REPO_ROOT / "packages" / _pkg / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from contextpulse_core.purge import (  # noqa: E402
    KNOWLEDGE_OBSERVATIONS,
    PROBE_FACTS,
    Tally,
    apply_activity_updates,
    apply_updates,
    open_db,
    purge_derived_store,
    scan_activity,
    scan_clipboard,
    scan_events,
    table_exists,
    verify_fts_matches_content,
)

__all__ = [
    "Tally", "apply_activity_updates", "apply_updates", "backup_db", "main",
    "open_db", "report", "resolve_db_path", "scan_activity", "scan_clipboard",
    "scan_events", "table_exists", "verify_fts_matches_content",
]


def resolve_db_path() -> Path:
    """Return the activity database the daemon actually writes to.

    Imported from contextpulse_sight.config rather than reconstructed here:
    ActivityDB() defaults to exactly this value, and it moves with
    CONTEXTPULSE_OUTPUT_DIR / CONTEXTPULSE_ACTIVITY_DB. A second copy of the
    path logic would purge the wrong file on any non-default install and report
    a clean zero for the real one.
    """
    from contextpulse_sight.config import ACTIVITY_DB_PATH

    return Path(ACTIVITY_DB_PATH)


def backup_db(conn: sqlite3.Connection, db_path: Path) -> Path:
    """Snapshot the database via SQLite's own backup API before rewriting.

    A file copy is not equivalent under WAL -- it can capture a torn state with
    the committed tail still sitting in the -wal sidecar.

    THE BACKUP IS DELETED once the post-apply verification passes (see main).
    It is a byte-complete copy of every raw row this script exists to scrub,
    and leaving it beside activity.db put it inside any folder backup or sync
    covering the screenshots directory -- so the run would report every secret
    scrubbed while a full plaintext copy sat next to the original (review S5).
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = db_path.with_name(f"{db_path.name}.pre-purge-{stamp}.bak")
    if dest.exists():
        raise SystemExit(f"REFUSING: backup target already exists: {dest}")
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
        target.commit()
    finally:
        # Closed explicitly: `with sqlite3.connect(...)` commits but does NOT
        # close, and on Windows the leaked handle blocks the delete below.
        target.close()
    return dest


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


def _derived_paths(args) -> tuple[Path | None, Path | None]:
    """Resolve probe.db and knowledge.db, honouring explicit overrides."""
    if args.probe_db is not None or args.knowledge_db is not None:
        return args.probe_db, args.knowledge_db
    try:
        from contextpulse_core.probe import default_probe_db

        probe = Path(default_probe_db())
    except Exception:  # pragma: no cover - probe package absent
        probe = None
    try:
        from contextpulse_knowledge.bridge import default_knowledge_db

        knowledge = Path(default_knowledge_db())
    except Exception:  # pragma: no cover - knowledge package absent
        knowledge = None
    return probe, knowledge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrub secrets from stored rows (dry run by default).",
    )
    parser.add_argument("--db", type=Path, default=None,
                        help="Activity database (default: the path the daemon uses).")
    parser.add_argument("--probe-db", type=Path, default=None,
                        help="probe.db (default: the consolidator's path).")
    parser.add_argument("--knowledge-db", type=Path, default=None,
                        help="knowledge.db (default: the bridge's path).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually rewrite the rows. Without this, nothing is written.")
    parser.add_argument("--include-titles", action="store_true",
                        help=(
                            "Also rewrite events.window_title / app_name. Off by default: "
                            "on real data these are mostly CREDENTIAL false positives on "
                            "ordinary window titles. They are reported either way."
                        ))
    parser.add_argument("--keep-backup", action="store_true",
                        help=(
                            "Keep the pre-purge backup after verification. It holds the "
                            "RAW values; by default it is deleted once the re-scan passes."
                        ))
    parser.add_argument("--skip-derived", action="store_true",
                        help="Only sweep activity.db (skip probe.db and knowledge.db).")
    args = parser.parse_args(argv)

    db_path = args.db if args.db is not None else resolve_db_path()
    conn = open_db(db_path, read_only=not args.apply)
    try:
        # `activity` is in this list because the first version of this script
        # swept clipboard and events only and then printed "verified: 0
        # remaining matches" -- a claim that was not true of the largest text
        # store in the database (review B-2).
        for required in ("clipboard", "events", "events_fts", "activity"):
            if not table_exists(conn, required):
                raise SystemExit(
                    f"REFUSING: {db_path} has no '{required}' table -- this does not "
                    "look like a ContextPulse activity database."
                )

        clip_tally, clip_updates = scan_clipboard(conn)
        evt_tally, title_tally, evt_updates = scan_events(conn, args.include_titles)
        act_tally, act_title_tally, act_updates = scan_activity(conn, args.include_titles)

        mode = "APPLY" if args.apply else "DRY RUN (nothing written)"
        print(f"purge_clipboard_secrets — {mode}")
        print(f"database: {db_path}")
        report("clipboard table", clip_tally)
        report("events table (payload text)", evt_tally)
        report("events table (window_title / app_name)", title_tally)
        report("activity table (ocr_text)", act_tally)
        report("activity table (window_title / app_name)", act_title_tally)
        if (title_tally.rows_affected or act_title_tally.rows_affected) and not args.include_titles:
            print(
                "  NOTE: reported but NOT rewritten. Pass --include-titles to "
                "scrub these too; on real data they are mostly the CREDENTIAL "
                "pattern firing on ordinary window titles."
            )

        derived_total = 0
        if not args.skip_derived:
            probe_db, knowledge_db = _derived_paths(args)
            for path, spec, label in (
                (probe_db, PROBE_FACTS, "probe.db (facts)"),
                (knowledge_db, KNOWLEDGE_OBSERVATIONS, "knowledge.db (observations)"),
            ):
                if path is None:
                    continue
                tally = purge_derived_store(Path(path), spec, apply=args.apply)
                report(f"{label} — {path}", tally)
                derived_total += tally.rows_affected

        if not clip_updates and not evt_updates and not act_updates and not derived_total:
            print("\nNothing to purge.")
            return 0

        if not args.apply:
            print(
                f"\n{len(clip_updates)} clipboard row(s), {len(evt_updates)} event "
                f"row(s), {len(act_updates)} activity row(s) and {derived_total} "
                "derived-store row(s) would be redacted in place, and events_fts "
                "and activity_fts rebuilt."
            )
            print("Re-run with --apply to write the changes.")
            return 0

        backup = backup_db(conn, db_path)
        print(f"\nbackup written: {backup}")
        apply_updates(conn, clip_updates, evt_updates)
        apply_activity_updates(conn, act_updates)
        print(
            f"applied: {len(clip_updates)} clipboard row(s), "
            f"{len(evt_updates)} event row(s), {len(act_updates)} activity row(s); "
            "events_fts and activity_fts rebuilt."
        )

        # Verify rather than assert: re-scan the rewritten rows and require
        # zero remaining matches. "The UPDATE ran" is not the same claim as
        # "the secrets are gone".
        clip_after, _ = scan_clipboard(conn)
        evt_after, title_after, _ = scan_events(conn, args.include_titles)
        act_after, act_title_after, _ = scan_activity(conn, args.include_titles)
        remaining = (
            clip_after.total_matches + evt_after.total_matches + act_after.total_matches
        )
        if args.include_titles:
            remaining += title_after.total_matches + act_title_after.total_matches
        if remaining:
            print(
                f"\nFAILED VERIFICATION: {clip_after.total_matches} clipboard, "
                f"{evt_after.total_matches} event payload and "
                f"{act_after.total_matches} activity ocr_text matches still present "
                "after purge. The backup has been kept.",
                file=sys.stderr,
            )
            return 1
        try:
            verify_fts_matches_content(conn)
        except sqlite3.DatabaseError as exc:
            print(
                f"\nFAILED VERIFICATION: events_fts does not match the rows it "
                f"indexes ({exc}). The rows are clean but the search index may "
                "still return the old text. Rebuild it before trusting this run. "
                "The backup has been kept.",
                file=sys.stderr,
            )
            return 1
        print(
            "verified: re-scan finds 0 remaining matches in both tables, and "
            "events_fts passes FTS5 integrity-check against them."
        )

        # Only now is it safe to drop the backup -- it is a byte-complete copy
        # of everything just scrubbed. Deleted by default rather than left
        # beside activity.db inside whatever folder sync covers that directory.
        if args.keep_backup:
            print(
                f"WARNING: {backup} was KEPT and still contains the UNREDACTED "
                "rows. Delete it once you no longer need it."
            )
        else:
            backup.unlink()
            print(f"backup deleted (it held the raw values): {backup.name}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    _force_utf8_console()
    sys.exit(main())
