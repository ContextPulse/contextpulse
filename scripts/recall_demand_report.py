"""Phase 0 stage-1 denominator: recall-shaped prompts vs recall-tool calls.

The 2026-09-19 gate counts recall calls (``facts_about`` / ``context_at``) and
reads a shortfall as "routing still broken". A shortfall is only evidence of
that if questions the tools should answer were actually asked. This report
reads Claude Code transcripts and prints both halves for one window:

* prompts whose wording refers to an earlier event (the same phrase family the
  MCP server instructions name), and
* the tools sessions actually reached for: ``mcp__contextpulse__*`` calls and
  ``query-journal.py`` invocations.

Read-only. It never calls a ContextPulse tool, so it cannot move the counter
it is measuring.

Usage:
    python scripts/recall_demand_report.py --root ~/.claude/projects \
        --after 2026-10-03T09:41 --interactive-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECALL_SHAPED = re.compile(
    r"\b(we discussed|we talked|last week|yesterday|earlier today|you did this before|"
    r"why did you forget|did we (?:ever|already)|remember (?:when|that|the)|"
    r"what did (?:we|i) (?:decide|say)|we already|previously|last time)\b",
    re.IGNORECASE,
)
RECALL_TOOLS = ("mcp__contextpulse__facts_about", "mcp__contextpulse__context_at")
JOURNAL = "Bash:query-journal"


@dataclass
class SessionTally:
    path: Path
    prompts: int = 0
    recall_prompts: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @property
    def recall_calls(self) -> int:
        return sum(t in RECALL_TOOLS for t in self.tools)


def human_prompts(rec: dict) -> list[str]:
    """Typed prompt text in a user record; tool results, meta and command echoes excluded."""
    if rec.get("type") != "user" or rec.get("isMeta"):
        return []
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        ]
    else:
        return []
    return [t.strip() for t in texts if t.strip() and not t.lstrip().startswith("<")]


def tool_calls(rec: dict) -> list[str]:
    """ContextPulse MCP calls and journal queries in an assistant record."""
    if rec.get("type") != "assistant":
        return []
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for c in content:
        if not (isinstance(c, dict) and c.get("type") == "tool_use"):
            continue
        name = str(c.get("name", ""))
        if name.startswith("mcp__contextpulse__"):
            out.append(name)
        elif name == "Bash" and "query-journal.py" in json.dumps(c.get("input", {})):
            out.append(JOURNAL)
    return out


def tally_file(path: Path, after: str) -> SessionTally:
    tally = SessionTally(path)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or str(rec.get("timestamp", "")) < after:
                continue
            for text in human_prompts(rec):
                tally.prompts += 1
                if RECALL_SHAPED.search(text):
                    tally.recall_prompts.append(text)
            tally.tools.extend(tool_calls(rec))
    return tally


def find_transcripts(roots: list[Path], interactive_only: bool) -> list[Path]:
    files = [p for r in roots for p in r.rglob("*.jsonl")]
    if interactive_only:
        files = [p for p in files if "subagents" not in p.parts and "System32" not in p.as_posix()]
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", action="append", required=True, type=Path)
    ap.add_argument("--after", required=True, help="ISO UTC prefix; earlier records are ignored")
    ap.add_argument(
        "--interactive-only", action="store_true", help="skip subagent and System32 transcripts"
    )
    args = ap.parse_args(argv)

    files = find_transcripts(args.root, args.interactive_only)
    if not files:
        print(
            "REFUSING: no transcripts under the given roots -- a zero here would be a fault, not a reading.",
            file=sys.stderr,
        )
        return 2
    tallies = [tally_file(p, args.after) for p in files]
    prompts = sum(t.prompts for t in tallies)
    recall_prompts = sum(len(t.recall_prompts) for t in tallies)
    recall_calls = sum(t.recall_calls for t in tallies)
    journal = sum(t.tools.count(JOURNAL) for t in tallies)
    cp_calls = sum(1 for t in tallies for name in t.tools if name.startswith("mcp__"))

    for t in tallies:
        for text in t.recall_prompts:
            print(
                f"  {t.path.stem[:8]} recall_calls={t.recall_calls} journal={t.tools.count(JOURNAL)} "
                f"> {text[:100]!r}"
            )
    print(f"window after {args.after}: transcripts={len(files)} prompts={prompts}")
    print(f"recall-shaped prompts: {recall_prompts}")
    print(
        f"recall calls (facts_about/context_at): {recall_calls}   other contextpulse calls: {cp_calls - recall_calls}"
    )
    print(f"journal queries: {journal}")
    if recall_prompts:
        print(
            f"routing ratio: {recall_calls}/{recall_prompts} recall-shaped prompts reached a recall tool"
        )
    else:
        print(
            "routing ratio: undefined -- no recall-shaped prompts, so this window cannot judge routing"
        )
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
