# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Claude Code transcripts as an event source for the fact store.

Typing capture stores counts, not words, so a fact a person types or dictates
to an AI agent ("you can read my Apple mail", "I already used the Hyatt offer")
never reached probe.db. This module reads those prompts from Claude Code's own
session transcripts (``<root>/<project>/<session>.jsonl``) and hands them to the
distiller as ``agent`` events.

What is read: only records a person produced -- ``origin.kind == "human"``,
which covers prompts typed or queued in a terminal and prompts sent from the
Desktop app or the phone. Everything else is dropped: tool results, meta
records, slash-command echoes, compaction summaries, interrupted markers,
sidechains, subagent transcripts (they live in subfolders and are never
listed), single-prompt headless runs, scheduled or headless briefs, pasted
handoff briefs, and sessions whose working folder is System32, a Temp folder
(the distiller's own runs used one) or an excluded estate.

The root is a parameter so another estate can run its own store from its own
transcripts. Excluded estates are configured, never named in code: lower-cased
substrings from the ``CONTEXTPULSE_TRANSCRIPT_EXCLUDES`` environment variable
(comma-separated) and from ``transcript_excludes.txt`` in the ContextPulse app
data folder (one per line, ``#`` comments). A project folder whose name matches
is never opened, a record whose working folder matches is dropped, and a root
whose path matches is refused.

Redaction happens here, before any truncation: the product redactor, then the
prose patterns below that the screen redactor has no reason to carry (one-time
codes, passcodes and PINs written as sentences, passwords given with "is",
bank and account numbers, long mixed-case tokens). Ruling "Purge Stored
Credentials from ContextPulse Clipboard History" (2026-10-17) is the standard.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from contextpulse_core.redact import redact_sensitive

logger = logging.getLogger(__name__)

# Excluded estates come from configuration, not code (see the module docstring):
# the names are private to whoever runs the store.
EXCLUDES_ENV = "CONTEXTPULSE_TRANSCRIPT_EXCLUDES"
EXCLUDES_FILENAME = "transcript_excludes.txt"
# Working folders that are never a person at a keyboard.
_EXCLUDED_CWD = (
    "\\windows\\system32",
    "/windows/system32",
    "\\appdata\\local\\temp",
    "/appdata/local/temp",
    "cp-probe-",
)

DEFAULT_MAX_CHARS = 2000

_COMPACTION = "This session is being continued from a previous conversation"
_INTERRUPTED = "[Request interrupted"
# First prompt of a session that a launcher, not a person, wrote.
_BRIEF_FIRST = re.compile(
    r"(you are a headless|headless|dispatched by|you are running (as|in) a scheduled|"
    r"scheduled (task|cycle|run)|director cycle|^run the .*cycle|cycle brief|#\s*task:|"
    r"this is an automated|no one will answer|answer with only)",
    re.I,
)
# A pasted brief or spec: a markdown document, not a sentence to an agent.
_PASTED_DOC = re.compile(r"^\s*#{1,3} ")
_PASTED_DOC_MIN_CHARS = 800
# "Yes", "Do it", "done": a reply whose meaning lives in the question it answers,
# which is not sent. Measured 2026-10-24 on 30 days of real prompts.
_MIN_PROMPT_CHARS = 15


@dataclass(frozen=True)
class TranscriptPrompt:
    key: str  # record uuid; a forked session copies it, so it dedups forks
    session_id: str  # transcript file stem
    ts: float  # unix seconds
    text: str  # redacted, full length
    project: str  # last component of the working folder
    entrypoint: str


# ── redaction ───────────────────────────────────────────────────────

_R = "[REDACTED:{}]"
_CODE_WORDS = re.compile(
    r"(?i)\b(?:pass\s?codes?|pins?|otps?|one[- ]time\s+(?:pass)?codes?|"
    r"(?:verification|security|auth(?:entication)?|login|sign[- ]in|access|sms|2fa|mfa|"
    r"confirmation|reset)\s+codes?|2fa|mfa|codes?)\b"
)
_ACCOUNT_WORDS = re.compile(
    r"(?i)\b(?:account|acct|routing|aba|iban|member(?:ship)?|policy|bank)\b"
)
# Digit runs governed by a preceding keyword. A date or time part (2026-10-24,
# 14:05, 12/29) is not a code, so a run touching - / : / . on either side is kept.
_CODE_DIGITS = re.compile(r"(?<![\d\-/:.])\d{4,10}(?![\d\-/:]|\.\d)")
_ACCOUNT_DIGITS = re.compile(r"(?<![\d\-/:.])\d{6,17}(?![\d\-/:]|\.\d)")
_CODE_WINDOW = 60
_ACCOUNT_WINDOW = 40

_PASSWORD_PROSE = re.compile(
    r"(?i)\b(pass(?:word|phrase)s?|passwd|pwd)(\s+(?:is|was|=)\s*|\s*[:=]\s*)"
    r"([\"'“‘]?)(\S+)"
)
_NOT_A_PASSWORD = {
    "in",
    "on",
    "the",
    "a",
    "an",
    "not",
    "stored",
    "saved",
    "wrong",
    "incorrect",
    "correct",
    "still",
    "now",
    "manager",
    "reset",
    "required",
    "expired",
    "changed",
    "missing",
    "fine",
    "there",
    "here",
    "set",
    "same",
    "different",
    "blank",
    "empty",
    "invalid",
    "valid",
    # Adverbs: "the password is probably already in Dashlane" (real prompt).
    "probably",
    "likely",
    "already",
    "also",
    "just",
    "maybe",
    "definitely",
}
# A long token mixing upper, lower and digits that no vendor rule named.
_MIXED_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_]{24,}(?![A-Za-z0-9_])")


def _redact_runs(text: str, words: re.Pattern, digits: re.Pattern, window: int, label: str) -> str:
    spans: list[tuple[int, int]] = []
    for m in words.finditer(text):
        lo = m.end()
        for d in digits.finditer(text, lo, min(len(text), lo + window)):
            spans.append(d.span())
    for s, e in sorted(set(spans), reverse=True):
        text = text[:s] + _R.format(label) + text[e:]
    return text


def _redact_password_prose(text: str) -> str:
    def repl(m: re.Match) -> str:
        value = m.group(4).strip("\"'”’.,;)")
        if value.lower() in _NOT_A_PASSWORD or value.startswith("[REDACTED"):
            return m.group(0)
        return m.group(1) + m.group(2) + m.group(3) + _R.format("CREDENTIAL")

    return _PASSWORD_PROSE.sub(repl, text)


def _redact_mixed_tokens(text: str) -> str:
    def repl(m: re.Match) -> str:
        t = m.group(0)
        if (
            any(c.isupper() for c in t)
            and any(c.islower() for c in t)
            and sum(c.isdigit() for c in t) >= 2
        ):
            return _R.format("TOKEN")
        return t

    return _MIXED_TOKEN.sub(repl, text)


def redact_transcript_text(text: str) -> str:
    """The product redactor, then the prose patterns typed text needs. Idempotent."""
    text = redact_sensitive(text)
    text = _redact_password_prose(text)
    text = _redact_runs(text, _CODE_WORDS, _CODE_DIGITS, _CODE_WINDOW, "CODE")
    text = _redact_runs(text, _ACCOUNT_WORDS, _ACCOUNT_DIGITS, _ACCOUNT_WINDOW, "ACCOUNT")
    return _redact_mixed_tokens(text)


# ── reading ─────────────────────────────────────────────────────────


def default_excludes_file() -> Path:
    """``transcript_excludes.txt`` in the ContextPulse app data folder."""
    from contextpulse_core.config import APPDATA_DIR

    return APPDATA_DIR / EXCLUDES_FILENAME


def configured_excludes(
    environ: dict[str, str] | None = None, path: Path | None = None
) -> tuple[str, ...]:
    """Lower-cased exclusion substrings from the environment and the excludes file.

    A missing file contributes nothing. A file that exists but cannot be read
    raises OSError: silently reading nothing would ingest the very estates it
    names.
    """
    env = os.environ if environ is None else environ
    items = env.get(EXCLUDES_ENV, "").split(",")
    file = default_excludes_file() if path is None else path
    if file.exists():
        for line in file.read_text(encoding="utf-8").splitlines():
            items.append(line.split("#", 1)[0])
    seen: list[str] = []
    for item in items:
        x = item.strip().lower()
        if x and x not in seen:
            seen.append(x)
    return tuple(seen)


def check_personal_root(root: Path, excludes: tuple[str, ...] | None = None) -> None:
    """Refuse a root that belongs to an excluded estate."""
    excludes = configured_excludes() if excludes is None else excludes
    hit = next((x for x in excludes if x in str(root).lower()), None)
    if hit is not None:
        raise ValueError(
            f"refusing transcript root {root}: it matches the excluded estate {hit!r} "
            f"({EXCLUDES_ENV} or {EXCLUDES_FILENAME}), which keeps its own store"
        )


def _human_text(rec: dict) -> str | None:
    """Text a person typed or dictated in this record, or None."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return None
    if rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
        return None
    origin = rec.get("origin")
    if not (isinstance(origin, dict) and origin.get("kind") == "human"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        parts = [
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        ]
    else:
        return None
    parts = [p.strip() for p in parts if p.strip() and not p.lstrip().startswith("<")]
    if not parts:
        return None
    text = "\n".join(parts)
    if text.startswith(_COMPACTION) or text.startswith(_INTERRUPTED):
        return None
    return text


def _epoch(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _project_label(cwd: str) -> str:
    name = re.split(r"[\\/]", cwd.rstrip("\\/"))[-1] if cwd else ""
    return name or "unknown"


def _read_session(path: Path, since_ts: float, excludes: tuple[str, ...]) -> list[TranscriptPrompt]:
    rows: list[tuple[dict, str, float]] = []
    entries: Counter[str] = Counter()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"user"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            text = _human_text(rec)
            if text is None:
                continue
            ts = _epoch(str(rec.get("timestamp", "")))
            if ts is None:
                continue
            entries[str(rec.get("entrypoint"))] += 1
            rows.append((rec, text, ts))
    if not rows:
        return []
    main_entry = entries.most_common(1)[0][0]
    if main_entry == "sdk-cli" and len(rows) == 1:
        return []  # one-shot headless run
    if _BRIEF_FIRST.search(rows[0][1][:400]):
        return []  # a launcher's brief opened the session
    out: list[TranscriptPrompt] = []
    for rec, text, ts in rows:
        cwd = str(rec.get("cwd") or "")
        low = cwd.lower()
        if any(x in low for x in _EXCLUDED_CWD) or any(x in low for x in excludes):
            continue
        if ts < since_ts:
            continue
        if len(text) >= _PASTED_DOC_MIN_CHARS and _PASTED_DOC.match(text):
            continue  # a pasted handoff brief or spec
        if len(text) < _MIN_PROMPT_CHARS:
            continue
        key = str(rec.get("uuid") or f"{path.stem}:{rec.get('timestamp')}")
        out.append(
            TranscriptPrompt(
                key=key,
                session_id=path.stem,
                ts=ts,
                text=redact_transcript_text(text),
                project=_project_label(cwd),
                entrypoint=str(rec.get("entrypoint")),
            )
        )
    return out


def iter_prompts(
    root: Path | str,
    *,
    since_ts: float,
    excludes: tuple[str, ...] | None = None,
) -> Iterator[TranscriptPrompt]:
    """Yield human prompts at or after ``since_ts`` from ``root``'s top-level transcripts.

    ``root`` is a Claude Code ``projects`` folder. Only ``<root>/<dir>/*.jsonl``
    is read; subfolders (subagent transcripts) are never listed. A directory
    whose name contains an ``excludes`` substring is skipped before any file in
    it is opened. Raises FileNotFoundError when ``root`` does not exist, so a
    wrong path is a failure rather than an empty, healthy-looking run.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"transcript root not found: {root}")
    excludes = configured_excludes() if excludes is None else excludes
    for d in sorted(os.scandir(root), key=lambda e: e.name):
        if not d.is_dir() or any(x in d.name.lower() for x in excludes):
            continue
        for f in sorted(os.scandir(d.path), key=lambda e: e.name):
            if not (f.is_file() and f.name.endswith(".jsonl")):
                continue
            try:
                if f.stat().st_mtime < since_ts:
                    continue  # nothing appended since the window opened
            except OSError:
                continue
            try:
                yield from _read_session(Path(f.path), since_ts, excludes)
            except OSError as exc:
                logger.warning("transcript unreadable, skipped: %s (%s)", f.path, exc)


def to_events(
    prompts: Iterable[TranscriptPrompt], max_chars: int = DEFAULT_MAX_CHARS
) -> list[dict]:
    """Prompts as distiller events (the shape probe.build_extraction_prompt takes)."""
    return [
        {
            "event_id": "cc-" + p.key.replace("-", "")[:8],
            "timestamp": round(p.ts, 3),
            "modality": "agent",
            "app_name": "Claude Code",
            "window_title": p.project,
            "text": p.text,
            "max_chars": max_chars,
        }
        for p in prompts
    ]


# ── once-only ledger in probe.db ────────────────────────────────────

_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS transcript_ingest (
    prompt_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    prompt_ts REAL NOT NULL,
    ingested_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""


def _ensure_ledger(conn: sqlite3.Connection) -> None:
    conn.executescript(_LEDGER_SQL)


def select_unseen(
    conn: sqlite3.Connection,
    prompts: Iterable[TranscriptPrompt],
    *,
    max_total_chars: int,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[TranscriptPrompt]:
    """Prompts not yet ingested, oldest first, up to ``max_total_chars`` of prompt text.

    Each prompt is costed at its truncated length (``max_chars``), so one long
    paste cannot exclude everything after it. What does not fit stays unseen
    for the next run.
    """
    _ensure_ledger(conn)
    seen = {r[0] for r in conn.execute("SELECT prompt_key FROM transcript_ingest")}
    fresh: dict[str, TranscriptPrompt] = {}
    for p in prompts:
        if p.key not in seen and p.key not in fresh:
            fresh[p.key] = p
    batch: list[TranscriptPrompt] = []
    used = 0
    for p in sorted(fresh.values(), key=lambda p: (p.ts, p.key)):
        cost = min(len(p.text), max_chars)
        if batch and used + cost > max_total_chars:
            break
        batch.append(p)
        used += cost
    return batch


def mark_ingested(conn: sqlite3.Connection, prompts: Iterable[TranscriptPrompt]) -> int:
    """Record prompts as ingested; return how many were new. Idempotent."""
    _ensure_ledger(conn)
    n = 0
    for p in prompts:
        cur = conn.execute(
            "INSERT OR IGNORE INTO transcript_ingest (prompt_key, session_id, prompt_ts)"
            " VALUES (?, ?, ?)",
            (p.key, p.session_id, p.ts),
        )
        n += cur.rowcount
    conn.commit()
    return n
