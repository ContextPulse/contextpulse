# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Phase 0 save-gate attribution report (cp-savegate-attribution-instrument).

Reconciles the automatic tool_usage log in probe.db (written on every real
facts_about/context_at call, see contextpulse_core.probe.record_usage)
against the manually-confirmed PHASE0-SAVE journal entries (scripts/probe_save.py,
category=phase0-save).

This is what makes a save count readable instead of ambiguous: before this
report, "0 attributed saves" could mean either "recall produced nothing worth
attributing" or "nobody called the tools" -- indistinguishable from outside.
Reading tool_usage alongside the confirmed-save count separates them.

Usage:
    python scripts/probe_usage_report.py
    python scripts/probe_usage_report.py --probe-db /path/to/probe.db --since 2026-07-07
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from contextpulse_core import probe

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_QUERY_JOURNAL = Path.home() / ".claude" / "shared-knowledge" / "scripts" / "query-journal.py"


def confirmed_save_count(
    project: str = "ContextPulse", since: str = "2026-07-07"
) -> int | None:
    """Count PHASE0-SAVE journal rows (category=phase0-save). None if unreachable.

    None (not 0) on any failure to reach/parse the journal -- a save count we
    could not verify must never render as "zero confirmed saves".
    """
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(_QUERY_JOURNAL),
                "--project",
                project,
                "--since",
                since,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    return sum(1 for r in rows if isinstance(r, dict) and r.get("category") == "phase0-save")


def format_report(summary: dict[str, Any], saves: int | None, probe_db: Path) -> str:
    lines = [
        f"Phase 0 attribution report -- probe.db: {probe_db}",
        "",
        f"  Tool calls total:      {summary['total_calls']}",
        f"  Tool calls w/ hits:    {summary['calls_with_hits']}",
    ]
    for tool in sorted(summary["by_tool"]):
        d = summary["by_tool"][tool]
        lines.append(f"    {tool}: {d['calls']} calls, {d['with_hits']} with hits")
    lines.append("")
    if saves is None:
        lines.append("  Confirmed saves (journal): COULD NOT VERIFY (query-journal unreachable)")
    else:
        lines.append(f"  Confirmed saves (journal, category=phase0-save): {saves}")
    lines.append("")
    if summary["total_calls"] == 0:
        lines.append(
            "  READ: zero tool calls recorded -- a 0 save count here means "
            "'nothing was watching', not 'no value'. The tools have never been used."
        )
    elif saves == 0:
        lines.append(
            f"  READ: {summary['total_calls']} real tool call(s) recorded, 0 confirmed "
            "saves -- this 0 is now meaningful: the instrument WAS watching."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--probe-db", default=None, help="Path to probe.db (default: probe.default_probe_db())")
    ap.add_argument("--since", default="2026-07-07", help="Journal --since date for confirmed saves")
    ap.add_argument("--project", default="ContextPulse")
    args = ap.parse_args(argv)

    db_path = Path(args.probe_db) if args.probe_db else probe.default_probe_db()
    if not db_path.exists():
        print(
            f"No probe.db at {db_path} -- the probe tools have never been called. "
            "0 usage, 0 saves (both genuinely zero, not unmeasured)."
        )
        return 0

    conn = probe.connect_probe(db_path)
    try:
        summary = probe.usage_summary(conn)
    finally:
        conn.close()

    saves = confirmed_save_count(project=args.project, since=args.since)
    print(format_report(summary, saves, db_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
