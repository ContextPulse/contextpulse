"""Grade the ContextPulse injection trial (2026-10-24 to 2026-11-07).

David ruled YES on 2026-10-24 (journal row 28650) to a two-week trial in which
~/.claude/hooks/contextpulse-inject.py hands sessions matching probe facts. This
report reads the hook's log, the session transcripts, probe.db and the journal --
all read-only -- and prints the trial's numbers against a bar written down BEFORE
the trial started. Do not edit SUCCESS_BAR or the definitions below after
TRIAL_START; if they turn out to be wrong, say so in the grading, beside the
numbers they produce.

Usage:
    .venv/Scripts/python.exe scripts/injection_trial_report.py
    .venv/Scripts/python.exe scripts/injection_trial_report.py --sample 15 --seed 7

DEFINITIONS (pre-registered):
* evaluation -- a log row with outcome "injected" or "none" (the hook passed its
  gates and looked); an injection is an evaluation with outcome "injected".
* use -- an injected fact counts as USED when an assistant record in the same
  session, after the injection, either cites it ("cp#<id>") or contains 2 or more
  of its distinctive words: tokens of 5+ letters in the fact's text or entity, not
  stopwords, held by at most 3 facts in the store, and not typed by David anywhere
  in that session (so echoing his own words never counts).
* journal re-lookup -- a query-journal.py call, after an injection in the same
  session, whose command names an injected fact's entity as whole words.
* journal load -- query-journal.py calls per 100 typed prompts, in main
  (non-subagent) transcripts whose entrypoint is "cli" and which started in the
  window; the baseline is the 14 days before TRIAL_START, by the same code.
* save -- a journal row "PHASE0-SAVE | tool=inject" written by scripts/probe_save.py
  --tool inject --fact-id <id> --confirmed-novel inside the window (category
  phase0-save, stage 2 of the 2026-09-19 gate).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core" / "src"))
from contextpulse_core import recall_inject as ri  # noqa: E402

HOME = Path.home()
TRIAL_START = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)  # set when the hooks were armed
TRIAL_END = TRIAL_START + timedelta(days=14)
BASELINE_DAYS = 14

SUCCESS_BAR = """\
SUCCESS BAR (written 2026-10-24, before the trial; do not move it)
  PASS  if  3 or more confirmed saves with tool=inject in the window
            (stage 2 of the 2026-09-19 gate, now fed by injection).
  Supporting evidence, reported but not a pass on its own:
    (a) 15% or more of injected facts are USED (definition above);
    (b) journal load at least 20% below the 14-day pre-trial baseline;
    (c) 50% or more of the graded sample judged relevant by a human.
  STOP EARLY (run --off) if p95 hook latency exceeds 1500 ms, or if the graded
  sample at the one-week mark is under 30% relevant."""

CITE = re.compile(r"\bcp#(\d+)\b")


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_log(path: Path, start: datetime, end: datetime) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"REFUSING: no injection log at {path} -- a zero here would be a fault.")
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = datetime.fromtimestamp(float(row.get("ts") or 0), tz=timezone.utc)
        if start <= ts < end:
            row["_dt"] = ts
            rows.append(row)
    return rows


@dataclass
class Transcript:
    path: Path
    entrypoint: str | None = None
    started: datetime | None = None
    prompts: list[tuple[datetime, str]] = field(default_factory=list)
    assistant: list[tuple[datetime, str]] = field(default_factory=list)
    journal: list[tuple[datetime, str]] = field(default_factory=list)


def _human_texts(rec: dict) -> list[str]:
    if rec.get("type") != "user" or rec.get("isMeta"):
        return []
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    else:
        return []
    return [t for t in texts if t.strip() and not t.lstrip().startswith("<")]


def read_transcript(path: Path) -> Transcript:
    t = Transcript(path)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = parse_ts(rec.get("timestamp", "")) if rec.get("timestamp") else None
            if ts is None:
                continue
            t.started = t.started or ts
            t.entrypoint = t.entrypoint or rec.get("entrypoint")
            for text in _human_texts(rec):
                t.prompts.append((ts, text))
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            parts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "tool_use":
                    blob = json.dumps(c.get("input", {}), ensure_ascii=False)
                    parts.append(blob)
                    if c.get("name") == "Bash" and "query-journal.py" in blob:
                        t.journal.append((ts, blob))
            t.assistant.append((ts, "\n".join(parts)))
    return t


def main_transcripts(root: Path, since: datetime) -> list[Path]:
    floor = since.timestamp()
    out = []
    for p in root.glob("*/*.jsonl"):
        if "System32" in p.parent.name:
            continue
        try:
            if p.stat().st_mtime >= floor:
                out.append(p)
        except OSError:
            continue
    return out


def distinctive(fact: ri.Fact, index: ri.Index, typed: set[str]) -> set[str]:
    toks = set(ri.tokens(fact.fact)) | set(ri.tokens(fact.entity))
    return {t for t in toks if len(t) >= 5 and t not in ri.STOPWORDS and index.df.get(t, 0) <= 3 and t not in typed}


def detect_uses(fact: ri.Fact, after: datetime, t: Transcript, index: ri.Index) -> bool:
    typed = {w for _, p in t.prompts for w in ri.tokens(p)}
    words = distinctive(fact, index, typed)
    for ts, text in t.assistant:
        if ts < after:
            continue
        if any(int(m) == fact.id for m in CITE.findall(text)):
            return True
        if len(words & set(ri.tokens(text))) >= 2:
            return True
    return False


def journal_load(paths: list[Path], start: datetime, end: datetime) -> tuple[int, int, int]:
    """(sessions, typed prompts, journal queries) over cli main transcripts started in [start, end)."""
    sessions = prompts = queries = 0
    for p in paths:
        t = read_transcript(p)
        if t.entrypoint != "cli" or t.started is None or not (start <= t.started < end):
            continue
        sessions += 1
        prompts += len(t.prompts)
        queries += len(t.journal)
    return sessions, prompts, queries


def count_saves(start: datetime) -> Counter:
    qj = HOME / ".claude" / "shared-knowledge" / "scripts" / "query-journal.py"
    proc = subprocess.run(
        [sys.executable, str(qj), "--project", "ContextPulse", "--since", start.strftime("%Y-%m-%d")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"REFUSING: query-journal.py failed ({proc.returncode}): {proc.stderr.strip()[-300:]}")
    return Counter(m for m in re.findall(r"PHASE0-SAVE \| tool=(\w+)", proc.stdout))


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, default=HOME / ".claude" / "hooks" / "contextpulse-inject.jsonl")
    ap.add_argument("--projects", type=Path, default=HOME / ".claude" / "projects")
    ap.add_argument("--probe-db", type=Path, default=HOME / "screenshots" / "probe.db")
    ap.add_argument("--start", default=TRIAL_START.isoformat())
    ap.add_argument("--end", default=TRIAL_END.isoformat())
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--skip-baseline", action="store_true", help="skip the slow transcript baseline scan")
    args = ap.parse_args(argv)
    start, end = parse_ts(args.start), parse_ts(args.end)
    now = datetime.now(timezone.utc)

    print(SUCCESS_BAR)
    print(f"\nWINDOW {start:%Y-%m-%d %H:%M}Z .. {end:%Y-%m-%d %H:%M}Z (report at {now:%Y-%m-%d %H:%M}Z)")

    rows = read_log(args.log, start, end)
    outcomes = Counter(r["outcome"] for r in rows)
    print("\n1. HOOK RUNS by outcome: " + (", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())) or "none"))
    evaluated = [r for r in rows if r["outcome"] in ("injected", "none")]
    injected = [r for r in rows if r["outcome"] == "injected"]
    for ev in ("SessionStart", "UserPromptSubmit"):
        e = [r for r in evaluated if r["event"] == ev]
        i = [r for r in injected if r["event"] == ev]
        toks = [r.get("tokens", 0) for r in i]
        print(f"   {ev}: evaluated={len(e)} injected={len(i)} ({pct(len(i), len(e))})"
              f" tokens mean={statistics.mean(toks):.0f} max={max(toks)}" if toks else
              f"   {ev}: evaluated={len(e)} injected=0")
    ms = sorted(float(r.get("ms") or 0) for r in rows if r["outcome"] not in ("off", "skip:estate", "skip:headless"))
    if ms:
        p95 = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
        print(f"   latency over {len(ms)} evaluations: p50={statistics.median(ms):.0f}ms p95={p95:.0f}ms max={ms[-1]:.0f}ms")

    conn = sqlite3.connect(f"file:{args.probe_db.as_posix()}?mode=ro", uri=True)
    try:
        facts = ri.load_facts(conn)
    finally:
        conn.close()
    by_id = {f.id: f for f in facts}
    index = ri.Index(facts)

    transcripts: dict[str, Transcript] = {}
    for sid in {r["session_id"] for r in injected if r.get("session_id")}:
        hits = list(args.projects.glob(f"*/{sid}.jsonl"))
        if hits:
            transcripts[sid] = read_transcript(hits[0])

    total = used = relooked = 0
    used_rows = 0
    for r in injected:
        t = transcripts.get(r.get("session_id"))
        row_used = False
        for fid in r.get("fact_ids") or []:
            f = by_id.get(int(fid))
            total += 1
            if f is None or t is None:
                continue
            if detect_uses(f, r["_dt"], t, index):
                used += 1
                row_used = True
            if any(ts >= r["_dt"] and ri.word_in(f.entity, blob) for ts, blob in t.journal):
                relooked += 1
        used_rows += row_used
    print(f"\n2. USE: {used} of {total} injected facts used ({pct(used, total)}); "
          f"{used_rows} of {len(injected)} injections had a used fact; "
          f"{len(transcripts)} of {len({r.get('session_id') for r in injected})} sessions' transcripts found")
    print(f"   journal re-lookups naming an injected fact's entity: {relooked}")

    if not args.skip_baseline:
        base_start = start - timedelta(days=BASELINE_DAYS)
        paths = main_transcripts(args.projects, base_start)
        bs, bp, bq = journal_load(paths, base_start, start)
        ts_, tp, tq = journal_load(paths, start, min(end, now))
        print(f"\n3. JOURNAL LOAD (cli main sessions): baseline {base_start:%m-%d}..{start:%m-%d} "
              f"sessions={bs} prompts={bp} queries={bq} -> {100 * bq / bp if bp else 0:.1f} per 100 prompts")
        print(f"   trial so far: sessions={ts_} prompts={tp} queries={tq} -> "
              f"{100 * tq / tp if tp else 0:.1f} per 100 prompts"
              + (f" ({100 * ((tq / tp) / (bq / bp) - 1):+.0f}% vs baseline)" if bp and bq and tp else ""))

    saves = count_saves(start)
    print(f"\n4. CONFIRMED SAVES since {start:%Y-%m-%d}: tool=inject {saves.get('inject', 0)}; "
          f"other tools {sum(v for k, v in saves.items() if k != 'inject')}")
    print(f"   VERDICT on the bar: {'PASS' if saves.get('inject', 0) >= 3 else 'NOT MET (yet)'}")

    print(f"\n5. SAMPLE of {min(args.sample, len(injected))} injected blocks for grading (seed {args.seed}).")
    print("   Grade each line R (relevant to the prompt) or N; facts are re-rendered from probe.db.")
    rng = random.Random(args.seed)
    for r in rng.sample(injected, min(args.sample, len(injected))):
        t = transcripts.get(r.get("session_id"))
        prompt = ""
        if t and r["event"] == "UserPromptSubmit":
            later = [p for ts, p in t.prompts if ts >= r["_dt"] - timedelta(seconds=5)]
            prompt = later[0] if later else ""
        print(f"\n   [{r['_dt']:%m-%d %H:%M}] {r['event']} session {str(r.get('session_id'))[:8]} "
              f"project={r.get('project')} prompt={prompt[:140]!r}")
        for fid in r.get("fact_ids") or []:
            f = by_id.get(int(fid))
            print("     " + (ri.render_line(f, now.timestamp())[:220] if f else f"cp#{fid} (no longer in store)"))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
