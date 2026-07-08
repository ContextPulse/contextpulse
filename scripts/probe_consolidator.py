# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""THROWAWAY — Phase 0 wedge-probe consolidator.

See .internal/fable-redesign/cp-implementation-plan-FINAL.md §Phase 0.

Nightly (or manual) batch: read the last N hours of `events` from the LIVE
activity.db (read-only, WAL-safe — never locks the capture writer), ask the
Claude CLI (on the founder's Max subscription, ~$0 marginal) to distill durable
entity/facts, and append them to the throwaway probe.db. That's the fused-recall
surface the facts_about/context_at MCP tools read.

Divergence from the plan's "read from a copy first": we open the live DB in
read-only URI mode instead of copying 87MB nightly. mode=ro respects WAL (sees
the writer's latest committed rows), takes no write lock, and was verified
non-interfering during orientation. Copying only the .db file would MISS
uncommitted -wal rows — read-only URI is both cheaper and more correct.

Usage:
    python scripts/probe_consolidator.py --hours 24
    python scripts/probe_consolidator.py --dry-run        # print prompt, no LLM call
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
import time

from contextpulse_core import probe

# Captured OCR text is full Unicode (emoji, CJK, etc.); the Windows console
# defaults to cp1252 and would crash on print/logging. Force UTF-8 everywhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [probe-consolidator] %(levelname)s %(message)s",
)
logger = logging.getLogger("probe.consolidator")


def call_claude(prompt: str, timeout: int = 600) -> str:
    """Invoke the Claude CLI headlessly and return stdout. Fail loud.

    The prompt is piped via STDIN (not passed as an argv) — it can be tens of KB
    with full Unicode, which would hit the Windows command-line length limit and
    mangle non-ASCII if passed as an argument.
    """
    proc = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        # Strip ANTHROPIC_API_KEY/AUTH_TOKEN so the CLI uses the founder's
        # claude.ai Max login (free) instead of billing/failing on a Console
        # API key that leaked in from the User-scope environment.
        env=probe.claude_cli_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[:500].strip()}")
    return proc.stdout


def open_events_ro(activity_db) -> sqlite3.Connection:
    """Open the live activity.db read-only (WAL-safe, no writer lock)."""
    conn = sqlite3.connect(f"file:{activity_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 0 probe consolidator (THROWAWAY)")
    ap.add_argument("--hours", type=float, default=24.0, help="Lookback window")
    ap.add_argument("--activity-db", default=str(probe.default_activity_db()))
    ap.add_argument("--probe-db", default=str(probe.default_probe_db()))
    ap.add_argument("--limit", type=int, default=1500, help="Max events per pass")
    ap.add_argument("--timeout", type=int, default=600, help="Claude CLI timeout (s)")
    ap.add_argument("--dry-run", action="store_true", help="Print prompt, no LLM call")
    args = ap.parse_args(argv)

    since = time.time() - args.hours * 3600
    logger.info("Reading events since %.0f (%.1fh) from %s", since, args.hours, args.activity_db)

    src = open_events_ro(args.activity_db)
    try:
        events = probe.read_recent_events(src, since_ts=since, limit=args.limit)
    finally:
        src.close()

    logger.info("Read %d events", len(events))
    if len(events) >= args.limit:
        # A real day exceeds the cap (measured ~1900 events/24h), so a single
        # --hours 24 pass only sees the most recent ~1.75h (red-team C2). Run
        # this every 6h with --hours 6 to cover the full day under the cap.
        logger.warning(
            "Event cap hit (%d) — window truncated to the most recent %d events; "
            "schedule shorter windows more often for full-day coverage.",
            args.limit,
            args.limit,
        )

    prompt = probe.build_extraction_prompt(events)

    if args.dry_run:
        print(prompt)
        logger.info("Dry run — %d chars of prompt, no LLM call.", len(prompt))
        return 0

    pconn = probe.connect_probe(args.probe_db)
    try:
        if not events:
            probe.record_run(pconn, events=0, facts=0, error="no events in window")
            logger.info("No events in window — nothing to consolidate.")
            return 0
        try:
            logger.info("Calling Claude CLI for extraction...")
            output = call_claude(prompt, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 — record then fail loud
            probe.record_run(pconn, events=len(events), facts=0, error=str(exc)[:300])
            logger.exception("Claude CLI call failed")
            return 1
        facts = probe.parse_facts(output)
        logger.info("Parsed %d facts from LLM output", len(facts))
        n = probe.write_facts(pconn, facts)
        probe.record_run(pconn, events=len(events), facts=n, error=None)
        logger.info("Wrote %d new facts to %s", n, args.probe_db)
        print(f"OK: {len(events)} events -> {n} new facts written to {args.probe_db}")
        return 0
    finally:
        pconn.close()


if __name__ == "__main__":
    sys.exit(main())
