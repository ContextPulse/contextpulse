# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Phase 0 save-counter — log an attributed fused/temporal recall "save".

See .internal/fable-redesign/cp-implementation-plan-FINAL.md §Phase 0.

A "save" counts toward the kill-criterion (target: 3) ONLY if ALL hold:
  1. The answer came from facts_about / context_at fused-or-temporal output;
  2. It was NOT obtainable from plain events-FTS (search_all_events) or existing
     memory infra (MEMORY.md, journal, CLAUDE.md) — you check this at log time;
  3. It's logged at the moment of use with the query text and answering tool.

This wraps the shared-knowledge journal (log-entry.py). It FAILS CLOSED: without
--confirmed-novel (your assertion that rule 2 holds), it refuses to log, so the
save count can't be inflated by undifferentiated recall.

Usage:
    python scripts/probe_save.py --tool facts_about --query "what did I decide about F03?" \\
        --confirmed-novel --note "context_at surfaced the D1 sign-off across 3 events"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_LOG_ENTRY = Path.home() / ".claude" / "shared-knowledge" / "scripts" / "log-entry.py"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log a Phase 0 attributed save")
    ap.add_argument(
        "--tool",
        required=True,
        choices=["facts_about", "context_at"],
        help="Which probe tool answered",
    )
    ap.add_argument("--query", required=True, help="The question you asked, verbatim")
    ap.add_argument("--note", default="", help="Why plain FTS/memory couldn't answer it")
    ap.add_argument(
        "--confirmed-novel",
        action="store_true",
        help="REQUIRED: assert the answer was NOT obtainable from FTS/MEMORY/journal",
    )
    ap.add_argument("--dry-run", action="store_true", help="Build+show the entry, don't write")
    args = ap.parse_args(argv)

    if not args.confirmed_novel:
        print(
            "REFUSED: a save must be novel. Re-run with --confirmed-novel only if the\n"
            "answer was NOT obtainable from search_all_events, MEMORY.md, the journal,\n"
            "or CLAUDE.md. (Attribution rule 2 - fail-closed so the count stays honest.)",
            file=sys.stderr,
        )
        return 2

    content = (
        f"PHASE0-SAVE | tool={args.tool} | query={args.query!r} "
        f"| counterfactual=confirmed-novel" + (f" | note={args.note}" if args.note else "")
    )

    cmd = [
        sys.executable,
        str(_LOG_ENTRY),
        "--type",
        "observation",
        "--project",
        "ContextPulse",
        "--agent",
        "claude-code",
        "--category",
        "phase0-save",
        "--content",
        content,
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    print(
        "\nSAVE LOGGED. Count them: "
        "python ~/.claude/shared-knowledge/scripts/query-journal.py "
        "--project ContextPulse --last 100 | grep PHASE0-SAVE"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
