# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Phase 0 wedge-probe — fused-recall logic. THROWAWAY.

See ``.internal/fable-redesign/cp-implementation-plan-FINAL.md`` §Phase 0.

This module is deliberately disposable: it exists only to put fused/temporal
recall in front of the founder in ~2 weeks and test the "save" hypothesis
before any architecture is bought. It does NOT obey the cp_core pure-logic
rule (Phase 0 is exempt by contract) and will be deleted or superseded by
``packages/knowledge/`` at Phase 1.

Surface:
    connect_probe(path)               -> sqlite3.Connection (schema ensured)
    read_recent_events(conn, since)   -> [event dict]  (from activity.db `events`)
    build_extraction_prompt(events)   -> str           (prompt for the Claude CLI)
    parse_facts(llm_output)           -> [fact dict]   (tolerant JSON extraction)
    write_facts(conn, facts)          -> int           (rows written to probe.db)
    query_facts_about(conn, entity)   -> [fact dict]   (entity recall)
    query_context_at(conn, t)         -> [fact dict]   (temporal recall)

A "fact dict" is: {entity, fact, valid_from, source_event_ids: list, confidence}.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

# ISO datetime formats an LLM might emit for valid_from despite the prompt.
_ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _coerce_ts(value: Any) -> float | None:
    """Coerce a valid_from to a float epoch-seconds, or None if unusable.

    Guards two real corruptions (red-team M2): an ISO string stored as TEXT is
    invisible to context_at's numeric BETWEEN and sorts above REAL in
    facts_about; a millisecond epoch lands facts ~50,000 years in the future.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            for fmt in _ISO_FORMATS:
                try:
                    return datetime.strptime(value.strip(), fmt).timestamp()
                except ValueError:
                    continue
            return None
    else:
        return None
    if f > 1e12:  # milliseconds — bring back to seconds
        f /= 1000.0
    return f


logger = logging.getLogger(__name__)

# Live capture DB (read-only source) and throwaway probe DB (fact sink). Both
# env-overridable so tests and alt setups don't touch the founder's real data.
_DEFAULT_ACTIVITY_DB = Path.home() / "screenshots" / "activity.db"


def default_activity_db() -> Path:
    """Path to the live events DB (read-only source)."""
    return Path(os.environ.get("CONTEXTPULSE_ACTIVITY_DB", str(_DEFAULT_ACTIVITY_DB)))


def default_probe_db() -> Path:
    """Path to the throwaway probe.db (fact sink); defaults beside activity.db."""
    env = os.environ.get("CONTEXTPULSE_PROBE_DB")
    return Path(env) if env else default_activity_db().parent / "probe.db"


# Auth-source env vars that make `claude -p` bypass the claude.ai login and bill
# a Console/API wallet instead. The consolidator is designed to run FREE on the
# founder's Max subscription (see module docstring / consolidator §9); a User-
# scope ANTHROPIC_API_KEY leaking into the scheduled task both mis-bills every
# 6h run to the credit-card wallet AND took the task down for two runs when that
# path had a transient failure (claude exited 1: "connectors are disabled
# because ANTHROPIC_API_KEY ... takes precedence over your claude.ai login").
# Stripping them forces the deterministic, zero-marginal-cost Max login.
_CLAUDE_AUTH_OVERRIDE_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def claude_cli_env() -> dict[str, str]:
    """Return the process env with auth-override vars stripped.

    Pass to ``subprocess.run(..., env=...)`` when invoking the Claude CLI so it
    always uses the claude.ai Max login rather than whatever API key happens to
    be exported into the task's environment.
    """
    env = dict(os.environ)
    for var in _CLAUDE_AUTH_OVERRIDE_VARS:
        env.pop(var, None)
    return env


# Cap on events fed to one extraction pass — keeps the prompt within a sane
# token budget. Phase 0 is a probe, not a backfill; 24h of events is small.
_MAX_EVENTS = 1500
# Per-event text is truncated so one noisy OCR frame can't dominate the prompt.
_MAX_TEXT_CHARS = 600

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    fact TEXT NOT NULL,
    valid_from REAL,
    source_event_ids TEXT,        -- JSON array of event_id
    confidence REAL DEFAULT 0.5,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    -- Disjoint nightly windows + manual reruns re-extract stable facts; an exact
    -- (entity, fact) dedup keeps append-only recall from cluttering (red-team M4).
    UNIQUE(entity, fact)
);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_facts_valid_from ON facts(valid_from);

-- Per-run ledger so a failed/empty 3am run is visible, not a silent zero
-- (red-team M1). Reviewed at the exit gate to distinguish "no saves" from
-- "consolidator never actually ran".
CREATE TABLE IF NOT EXISTS probe_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    events INTEGER NOT NULL DEFAULT 0,
    facts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""


# ── probe.db ────────────────────────────────────────────────────────


def connect_probe(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the throwaway probe.db and ensure schema."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def write_facts(conn: sqlite3.Connection, facts: list[dict[str, Any]]) -> int:
    """Insert parsed facts (deduped on entity+fact); return the number written."""
    written = 0
    for f in facts:
        cur = conn.execute(
            "INSERT OR IGNORE INTO facts (entity, fact, valid_from, source_event_ids, confidence)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                f["entity"],
                f["fact"],
                f.get("valid_from"),
                json.dumps(f.get("source_event_ids", [])),
                float(f.get("confidence", 0.5)),
            ),
        )
        written += cur.rowcount  # 0 when the UNIQUE(entity, fact) dedup fires
    conn.commit()
    return written


def record_run(conn: sqlite3.Connection, events: int, facts: int, error: str | None = None) -> None:
    """Append a row to the run ledger so empty/failed runs aren't silent."""
    conn.execute(
        "INSERT INTO probe_runs (events, facts, error) VALUES (?, ?, ?)",
        (events, facts, error),
    )
    conn.commit()


def _row_to_fact(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["source_event_ids"] = json.loads(d.get("source_event_ids") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["source_event_ids"] = []
    return d


def query_facts_about(
    conn: sqlite3.Connection, entity: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Entity recall: facts whose entity contains ``entity`` (case-insensitive)."""
    rows = conn.execute(
        "SELECT * FROM facts WHERE entity LIKE ? COLLATE NOCASE"
        " ORDER BY valid_from DESC, confidence DESC LIMIT ?",
        (f"%{entity}%", limit),
    ).fetchall()
    return [_row_to_fact(r) for r in rows]


def query_context_at(
    conn: sqlite3.Connection,
    t: float,
    window_s: float = 1800.0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Temporal recall: facts valid within ``+/- window_s`` of ``t``, closest first."""
    rows = conn.execute(
        "SELECT * FROM facts WHERE valid_from BETWEEN ? AND ?"
        " ORDER BY ABS(valid_from - ?) ASC LIMIT ?",
        (t - window_s, t + window_s, t, limit),
    ).fetchall()
    return [_row_to_fact(r) for r in rows]


# ── events (read-only source) ───────────────────────────────────────


# Mirror ContextEvent._TEXT_PAYLOAD_KEYS (events.py). The live events_fts trigger
# only indexes the first 3; burst_text (typed bursts) and correction_text (voice
# corrections) are the person's own words — strongest intent signal — so the
# probe extracts all 5 even though FTS can't (a genuine fused-recall edge).
_TEXT_KEYS = ("ocr_text", "transcript", "text", "burst_text", "correction_text")


def _extract_text(payload_raw: str | None) -> str:
    """Pull the best text field out of an event payload; never raise."""
    if not payload_raw:
        return ""
    try:
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if value:
            return value
    return ""


def read_recent_events(
    conn: sqlite3.Connection, since_ts: float, limit: int = _MAX_EVENTS
) -> list[dict[str, Any]]:
    """Read events after ``since_ts`` from the live ``events`` table (read-only).

    When more than ``limit`` events fall in the window, the most RECENT ``limit``
    are taken (capture is dense — a full day can exceed the cap; recent activity
    is the most relevant to consolidate). Results are returned chronologically.
    Text is extracted from the JSON payload (ocr_text / transcript / text).
    """
    rows = conn.execute(
        "SELECT event_id, timestamp, modality, event_type, app_name, window_title, payload"
        " FROM events WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
        (since_ts, limit),
    ).fetchall()
    rows = list(reversed(rows))  # newest-N selected above; emit chronologically
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        out.append(
            {
                "event_id": d["event_id"],
                "timestamp": d["timestamp"],
                "modality": d["modality"],
                "app_name": d["app_name"] or "",
                "window_title": d["window_title"] or "",
                "text": _extract_text(d["payload"]),
            }
        )
    return out


# ── extraction prompt ───────────────────────────────────────────────


# Authored + red-teamed by Fable (2026-07-07); see
# .internal/fable-redesign/phase0-extraction-prompt-2026-07-07.md for rationale +
# golden set. Overridable at runtime via CONTEXTPULSE_PROBE_PROMPT_FILE (a file
# whose contents replace this header) so the prompt can be iterated without a
# code change — the single biggest quality lever for the probe.
_PROMPT_HEADER = """\
You are the nightly memory consolidator for a personal activity-capture system.
Input: a chronological, timestamped log of ONE person's computer activity over
roughly the last 24 hours. Each line:
  [<event_id>] ts=<unix_ts> <modality> app='<app>' win='<window title>' :: <text>
Modalities: sight = screen OCR (noisy), voice = speech transcript, touch =
typed text, clipboard = copied text. Voice and touch are the person's own
words and are the strongest evidence of intent and decisions.

YOUR JOB
Distill durable FACTS to be recalled later via two tools: facts_about(entity)
and context_at(timestamp). The raw log is ALREADY fully keyword-searchable, so
a fact has value ONLY if a keyword search over the raw log could not produce
the same answer. Emit exactly two kinds of facts:

1. FUSED facts — synthesized across MULTIPLE events, usually spanning times,
   apps, or modalities: decisions and their reasons, outcomes, state changes,
   relationships, cause/effect, who-what-why connections.
     GOOD: "Booked the Southwest DEN-BOS 11:25 AM flight for $214.96 over the
            cheaper $198 JetBlue because JetBlue arrived too late."
            (price-comparison screen + voice remark + confirmation page)
     GOOD: "Fixed the Decimal*float TypeError in invoicer's tax.py by coercing
            rate to Decimal; full test suite passing afterward."
            (error on screen + typed fix + green pytest run)
2. TEMPORAL facts — what was true or happening at/around a time, phrased so
   context_at(t) can answer them: work sessions, states with a start, blocked/
   unblocked transitions, before-vs-after.
     GOOD: "From ~13:05 to ~14:20 was debugging the invoicer tax rounding bug."
     GOOD: "As of the evening, the staging deploy was still blocked waiting on
            ACM certificate validation."

NEVER emit restatements — anything answerable by grepping a single event:
     BAD: "United flight DEN-BOS costs $238."         (on-screen text; FTS has it)
     BAD: "An email from Alice Wong is in the inbox." (screen content)
     BAD: "tax.py defines apply_tax()."               (visible code)
Apply this test to every candidate fact: "would a keyword search of the raw
log for this entity surface this same answer?" If yes, drop the fact.

ENTITIES
- "entity" is the short canonical name the person would later type into
  facts_about(): a project, person, product, file, trip, ticket, or topic.
- Use ONE canonical form per real-world thing across your entire output. Fold
  aliases, abbreviations, vendor prefixes, and misspellings into the most
  specific name the person themselves uses: "ToS" / "thinkorswim" /
  "TD Ameritrade thinkorswim" -> "thinkorswim"; "Bob" / "Robert Chen" /
  "rchen@acme.com" -> "Robert Chen".
- Prefer the project over the file, the person over their email address, the
  product over its parent company (unless the fact is about the company).

FIELDS
- fact: ONE self-contained sentence with the concrete specifics (names,
  amounts, versions, outcomes, reasons) needed to be useful months from now,
  standing alone. No pronouns without antecedents.
- valid_from: unix timestamp when the fact BECAME TRUE — the decision moment,
  the state-change event, or the session start. Use the ts of the earliest
  event that proves the fact. Never invent a timestamp outside the log.
- source_event_ids: the real event ids supporting the fact. A fused fact
  should normally cite 2 or more events.
- confidence:
    0.90-1.00  the person explicitly said/typed it, or multiple independent
               events confirm it
    0.70-0.85  solid multi-event inference with only one plausible reading
    0.50-0.65  single-event inference of a durable state; alternative
               readings possible
    below 0.50 do not emit the fact at all

NOISE AND HONESTY
- Skip UI chrome, menus, ads, promotions, notifications, cookie banners,
  boilerplate, autocomplete suggestions, code merely scrolled past, and
  articles merely glimpsed.
- Passive reading becomes a fact only at session level ("spent ~40 minutes
  researching Rust WASM toolchains"), never per headline or per page.
- Every fact must be traceable to its cited events — no hallucination, no
  speculation. Where OCR is garbled or ambiguous, prefer omission.
- Quality over quantity: a typical day yields 0-15 facts; hard cap 25. No
  near-duplicates of the same fact in different words.
- If nothing durable happened, return [].

SENSITIVE DATA
The log WILL contain secrets and personal data. Hard rules:
- NEVER output passwords, API keys or tokens, OTP/2FA codes, full credit
  card, bank, routing, or account numbers, SSNs or government IDs, or private
  keys — not even partially, masked, or paraphrased. If an event contains
  only such data, ignore the whole event.
- Last-4 digits are permitted only when needed to identify WHICH card or
  account was used.
- The person's OWN health, financial, and legal facts ARE worth capturing
  when they themselves stated or acted on them (appointments made, amounts
  paid, decisions taken) — this memory exists to serve them. Capture exactly
  what is evidenced; never infer diagnoses, conditions, or legal exposure
  beyond what is explicit.

OUTPUT FORMAT
Return ONLY a JSON array — the first character of your reply is '[' and the
last is ']'. No markdown fences, no prose, no comments, no trailing commas.
Double-quoted keys and strings; valid_from is a bare number. Each element:
  {"entity": "<canonical name>", "fact": "<one durable fact>",
   "valid_from": <unix_ts>, "source_event_ids": ["<id>", ...],
   "confidence": <0.0-1.0>}
If nothing qualifies, return [].

Activity log (chronological):
"""


def _prompt_header() -> str:
    """Return the extraction prompt header, honoring a file override if set."""
    override = os.environ.get("CONTEXTPULSE_PROBE_PROMPT_FILE")
    if override:
        try:
            return Path(override).read_text(encoding="utf-8")
        except OSError:
            logger.warning("prompt override %s unreadable; using built-in", override)
    return _PROMPT_HEADER


def build_extraction_prompt(events: list[dict[str, Any]]) -> str:
    """Assemble the Claude-CLI extraction prompt from a batch of events."""
    lines: list[str] = []
    for e in events:
        text = (e.get("text") or "").strip().replace("\n", " ")
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS] + "..."
        lines.append(
            f"[{e.get('event_id')}] ts={e.get('timestamp')} "
            f"{e.get('modality')} app={e.get('app_name')!r} "
            f"win={e.get('window_title')!r} :: {text}"
        )
    body = "\n".join(lines) if lines else "(no events in window)"
    return _prompt_header() + body + "\n\nJSON array:"


# ── tolerant parsing ────────────────────────────────────────────────


def parse_facts(llm_output: str) -> list[dict[str, Any]]:
    """Extract a fact list from raw LLM output, tolerant of fences/prose.

    Drops entries missing ``entity`` or ``fact``; defaults optional fields.
    Returns [] on any parse failure (never raises).
    """
    if not llm_output:
        return []
    start = llm_output.find("[")
    end = llm_output.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        raw = json.loads(llm_output[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    facts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity")
        fact = item.get("fact")
        if not entity or not fact:
            continue
        ids = item.get("source_event_ids") or []
        if not isinstance(ids, list):
            ids = []
        try:
            conf = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        facts.append(
            {
                "entity": str(entity),
                "fact": str(fact),
                "valid_from": _coerce_ts(item.get("valid_from")),
                "source_event_ids": ids,
                "confidence": conf,
            }
        )
    return facts
