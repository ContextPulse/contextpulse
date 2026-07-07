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
from pathlib import Path
from typing import Any

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
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_facts_valid_from ON facts(valid_from);
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
    """Insert parsed facts; return the number written."""
    written = 0
    for f in facts:
        conn.execute(
            "INSERT INTO facts (entity, fact, valid_from, source_event_ids, confidence)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                f["entity"],
                f["fact"],
                f.get("valid_from"),
                json.dumps(f.get("source_event_ids", [])),
                float(f.get("confidence", 0.5)),
            ),
        )
        written += 1
    conn.commit()
    return written


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
    return payload.get("ocr_text") or payload.get("transcript") or payload.get("text") or ""


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


_PROMPT_HEADER = """\
You are a memory consolidator for a personal activity-capture system. Below is a
timestamped log of one person's recent computer activity (app, window title, and
any OCR'd/transcribed text). Extract durable FACTS about entities the person was
working with — projects, people, files, decisions, tasks, tools, states.

Return ONLY a JSON array. Each element:
  {"entity": "<short name>", "fact": "<one concrete fact>",
   "valid_from": <unix timestamp when it became true>,
   "source_event_ids": ["<event_id>", ...], "confidence": <0.0-1.0>}

Rules:
- Prefer facts that a plain keyword search over the log could NOT answer:
  relationships, states, decisions, "who/what/when" fusion across events.
- One clear fact per element. No speculation. Skip UI chrome and noise.
- If nothing durable is present, return [].

Activity log:
"""


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
    return _PROMPT_HEADER + body + "\n\nJSON array:"


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
                "valid_from": item.get("valid_from"),
                "source_event_ids": ids,
                "confidence": conf,
            }
        )
    return facts
