# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""SQLite-backed activity tracking with FTS5 full-text search.

Records which apps/windows the user visits, links to buffer frames,
and stores OCR text for searchable screen history.
"""

import logging
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

from contextpulse_core.redact import redact_sensitive
from contextpulse_core.search_filter import keep_rows_matching_redacted_text

from contextpulse_sight.config import ACTIVITY_DB_PATH, ACTIVITY_MAX_AGE

logger = logging.getLogger("contextpulse.sight.activity")

# activity_fts is declared with no `tokenize=` argument, so it gets FTS5's
# default. The shadow index the oracle filter builds over the REDACTED text
# must name the same one, or it answers a different question from the one that
# produced the rows.
_ACTIVITY_FTS_TOKENIZER = "unicode61"

# The columns activity_fts indexes, which are also the ones a returned row
# must not carry raw.
_ACTIVITY_TEXT_COLUMNS = ("window_title", "app_name", "ocr_text")


def _searchable_activity_text(row: dict) -> str:
    """What activity_fts matches on, rebuilt from a result row."""
    return " ".join(str(row.get(c) or "") for c in _ACTIVITY_TEXT_COLUMNS)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    app_name TEXT NOT NULL DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    frame_path TEXT,
    ocr_text TEXT,
    ocr_confidence REAL DEFAULT 0.0,
    diff_score REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
"""

# Migration for existing databases that lack the diff_score column
_MIGRATIONS = [
    "ALTER TABLE activity ADD COLUMN diff_score REAL DEFAULT 0.0",
]

_CLIPBOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS clipboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clipboard_timestamp ON clipboard(timestamp);
"""

_MCP_CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    tool_name TEXT NOT NULL,
    client_id TEXT DEFAULT 'unknown',
    call_count INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_mcp_calls_timestamp ON mcp_calls(timestamp);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS activity_fts USING fts5(
    window_title, app_name, ocr_text,
    content=activity, content_rowid=id
);
"""

_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS activity_ai AFTER INSERT ON activity BEGIN
    INSERT INTO activity_fts(rowid, window_title, app_name, ocr_text)
    VALUES (new.id, new.window_title, new.app_name, COALESCE(new.ocr_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS activity_au AFTER UPDATE ON activity BEGIN
    INSERT INTO activity_fts(activity_fts, rowid, window_title, app_name, ocr_text)
    VALUES ('delete', old.id, old.window_title, old.app_name, COALESCE(old.ocr_text, ''));
    INSERT INTO activity_fts(rowid, window_title, app_name, ocr_text)
    VALUES (new.id, new.window_title, new.app_name, COALESCE(new.ocr_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS activity_ad AFTER DELETE ON activity BEGIN
    INSERT INTO activity_fts(activity_fts, rowid, window_title, app_name, ocr_text)
    VALUES ('delete', old.id, old.window_title, old.app_name, COALESCE(old.ocr_text, ''));
END;
"""


class ActivityDB:
    """SQLite activity database with FTS5 search."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or ACTIVITY_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL journaling reduces writer-vs-reader lock contention — critical on
        # Windows where antivirus/search indexers can briefly hold the file.
        # busy_timeout makes SQLite wait instead of raising SQLITE_BUSY, which
        # was causing intermittent pytest-timeout hangs in test_activity.py on
        # the Windows CI runners.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_CLIPBOARD_SCHEMA)
            self._conn.executescript(_MCP_CALLS_SCHEMA)
            # Run schema migrations for existing databases
            for migration in _MIGRATIONS:
                try:
                    self._conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column/table already exists
            try:
                self._conn.executescript(_FTS_SCHEMA)
                self._conn.executescript(_FTS_TRIGGERS)
            except sqlite3.OperationalError as e:
                # FTS5 triggers may already exist
                if "already exists" not in str(e):
                    logger.warning("FTS5 setup issue: %s", e)
            self._conn.commit()

    def record(
        self,
        timestamp: float,
        window_title: str,
        app_name: str,
        monitor_index: int = 0,
        frame_path: str | None = None,
        diff_score: float = 0.0,
    ) -> int:
        """Insert an activity record. Returns row ID."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO activity (timestamp, window_title, app_name, monitor_index, frame_path, diff_score) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp, window_title, app_name, monitor_index, frame_path, diff_score),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_ocr(self, row_id: int, ocr_text: str, confidence: float):
        """Update OCR text for a previously recorded activity."""
        with self._lock:
            self._conn.execute(
                "UPDATE activity SET ocr_text = ?, ocr_confidence = ? WHERE id = ?",
                (ocr_text, confidence, row_id),
            )
            self._conn.commit()

    def get_summary(self, hours: float = 8.0) -> dict:
        """Summarize activity over the last N hours.

        Returns dict with:
            apps: {app_name: count}
            titles: list of recent unique titles
            total_captures: int
            time_range: (start_ts, end_ts)
        """
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            rows = self._conn.execute(
                "SELECT app_name, window_title, timestamp FROM activity "
                "WHERE timestamp >= ? ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()

        if not rows:
            return {
                "apps": {},
                "titles": [],
                "total_captures": 0,
                "time_range": (0, 0),
            }

        apps = defaultdict(int)
        seen_titles = []
        seen_set = set()
        for row in rows:
            app = row["app_name"] or "unknown"
            apps[app] += 1
            title = row["window_title"]
            if title and title not in seen_set:
                seen_set.add(title)
                seen_titles.append(title)

        return {
            "apps": dict(sorted(apps.items(), key=lambda x: -x[1])),
            "titles": seen_titles[:20],  # top 20 recent unique titles
            "total_captures": len(rows),
            "time_range": (rows[-1]["timestamp"], rows[0]["timestamp"]),
        }

    def search(self, query: str, minutes_ago: int = 60) -> list[dict]:
        """Full-text search across window titles and OCR text, MATCHING REDACTED TEXT.

        `activity` is the largest text store in the product and this method was
        the one search surface the first redaction pass missed: it matched raw
        `ocr_text` through activity_fts while the MCP tool redacted only the
        rendered snippet. That is an extraction oracle, and the second review
        ran it -- activity_fts uses the default tokenizer with no stemmer, so a
        prefix query is a clean binary signal, and 13 characters of a planted
        key came back from result counts alone while every snippet shown was
        correctly `[REDACTED:...]` (review B-1).

        Output redaction cannot close a query oracle. The candidates the index
        returns are re-matched against their REDACTED rendering through the same
        tokenizer, so the count a caller sees is the count it would have got
        from a store that never held the secret. The LIKE fallback is filtered
        the same way, under substring semantics to mirror `LIKE '%q%'`.

        The rows returned carry the REDACTED text, so a caller cannot
        reintroduce the leak by rendering what it got back.
        """
        cutoff = time.time() - (minutes_ago * 60)
        used_fts = True
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT a.id, a.timestamp, a.window_title, a.app_name, "
                    "a.monitor_index, a.frame_path, a.ocr_text, a.ocr_confidence, a.diff_score "
                    "FROM activity a "
                    "JOIN activity_fts f ON a.id = f.rowid "
                    "WHERE activity_fts MATCH ? AND a.timestamp >= ? "
                    "ORDER BY a.timestamp DESC LIMIT 20",
                    (query, cutoff),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback to LIKE if FTS match syntax fails
                used_fts = False
                like_query = f"%{query}%"
                rows = self._conn.execute(
                    "SELECT id, timestamp, window_title, app_name, "
                    "monitor_index, frame_path, ocr_text, ocr_confidence, diff_score "
                    "FROM activity "
                    "WHERE (window_title LIKE ? OR ocr_text LIKE ? OR app_name LIKE ?) "
                    "AND timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (like_query, like_query, like_query, cutoff),
                ).fetchall()

        kept = keep_rows_matching_redacted_text(
            query,
            [dict(row) for row in rows],
            _searchable_activity_text,
            tokenize=_ACTIVITY_FTS_TOKENIZER if used_fts else None,
        )
        for row in kept:
            for column in _ACTIVITY_TEXT_COLUMNS:
                value = row.get(column)
                if isinstance(value, str) and value:
                    row[column] = redact_sensitive(value)
        return kept

    def get_context_at(self, minutes_ago: float) -> dict | None:
        """Get the frame + metadata from approximately N minutes ago."""
        target_ts = time.time() - (minutes_ago * 60)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, timestamp, window_title, app_name, "
                "monitor_index, frame_path, ocr_text, ocr_confidence, diff_score "
                "FROM activity "
                "ORDER BY ABS(timestamp - ?) LIMIT 1",
                (target_ts,),
            ).fetchone()

        if not row:
            return None
        return dict(row)

    def search_by_frame(self, frame_path: str) -> dict | None:
        """Look up an activity record by its frame path."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, timestamp, diff_score FROM activity WHERE frame_path = ? LIMIT 1",
                (frame_path,),
            ).fetchone()
        return dict(row) if row else None

    def prune(self, max_age_seconds: int | None = None):
        """Delete records older than max_age_seconds."""
        age = max_age_seconds if max_age_seconds is not None else ACTIVITY_MAX_AGE
        cutoff = time.time() - age
        with self._lock:
            self._conn.execute("DELETE FROM activity WHERE timestamp < ?", (cutoff,))
            self._conn.commit()

    def count(self) -> int:
        """Return total number of activity records."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM activity").fetchone()
            return row[0]

    # -- Clipboard tracking ------------------------------------------------

    def record_clipboard(self, timestamp: float, text: str) -> int:
        """Record a clipboard capture. Returns row ID."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO clipboard (timestamp, text) VALUES (?, ?)",
                (timestamp, text),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_clipboard_history(self, count: int = 10) -> list[dict]:
        """Get the most recent clipboard entries."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, timestamp, text FROM clipboard "
                "ORDER BY timestamp DESC LIMIT ?",
                (count,),
            ).fetchall()
        return [dict(row) for row in rows]

    # How many rows the clipboard search is willing to redact and scan in
    # Python before giving up on the window. Generous relative to a clipboard
    # history (the store is one row per distinct copy) and bounded so a caller
    # cannot ask for a full-table scan by passing a huge minutes_ago.
    _SEARCH_SCAN_LIMIT = 2000

    def clipboard_rows_in_window(self, minutes_ago: int = 60) -> int:
        """How many clipboard rows the window holds, ignoring the scan cap.

        Lets a caller tell "no matches" from "no matches in the part I looked
        at". A COUNT over a time range says nothing about any row's CONTENT, so
        this is not a way back into the oracle search_clipboard closed.
        """
        cutoff = time.time() - (minutes_ago * 60)
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM clipboard WHERE timestamp >= ?", (cutoff,)
            ).fetchone()[0]

    def search_clipboard(self, query: str, minutes_ago: int = 60) -> list[dict]:
        """Search clipboard history by text content, MATCHING REDACTED TEXT.

        This used to be `WHERE text LIKE ?` against the raw stored column, with
        the MCP tool redacting the rendered rows afterwards. That is an
        extraction oracle, and the adversarial review proved it: a client that
        can call search_clipboard issues "sk-", "sk-a", "sk-ab" ... and reads
        the result COUNT to recover a pre-fix secret one character at a time,
        while every response it sees is correctly redacted.

        Output redaction cannot close a query oracle. The fix is that no count
        and no match is ever computed against raw text: rows are fetched by
        time window, redacted, and matched in Python. A caller therefore
        learns exactly what it could learn from reading the redacted rows,
        which is the guarantee the product claims.

        The scan is bounded by _SEARCH_SCAN_LIMIT so a large minutes_ago
        cannot turn this into a full-table scan, and the returned rows carry
        the REDACTED text, so a caller of this method cannot reintroduce the
        leak by rendering what it got back.
        """
        cutoff = time.time() - (minutes_ago * 60)
        needle = query.lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, timestamp, text FROM clipboard "
                "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                (cutoff, self._SEARCH_SCAN_LIMIT),
            ).fetchall()

        out: list[dict] = []
        for row in rows:
            entry = dict(row)
            entry["text"] = redact_sensitive(entry.get("text") or "")
            if needle in entry["text"].lower():
                out.append(entry)
            if len(out) >= 20:
                break
        return out

    # -- MCP call tracking -------------------------------------------------

    def record_mcp_call(self, tool_name: str, client_id: str = "unknown") -> int:
        """Record an MCP tool call. Returns row ID."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO mcp_calls (timestamp, tool_name, client_id) VALUES (?, ?, ?)",
                (time.time(), tool_name, client_id),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_agent_stats(self, hours: float = 24.0) -> dict:
        """Get MCP tool call statistics grouped by client.

        Returns dict with:
            clients: {client_id: {tool_name: count, ...}}
            total_calls: int
            time_range: (start_ts, end_ts) or (0, 0)
        """
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_id, tool_name, COUNT(*) as cnt, "
                "MIN(timestamp) as first_call, MAX(timestamp) as last_call "
                "FROM mcp_calls WHERE timestamp >= ? "
                "GROUP BY client_id, tool_name ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()

        if not rows:
            return {"clients": {}, "total_calls": 0, "time_range": (0, 0)}

        clients: dict[str, dict[str, int]] = {}
        total = 0
        min_ts = float("inf")
        max_ts = 0.0
        for row in rows:
            cid = row["client_id"]
            clients.setdefault(cid, {})[row["tool_name"]] = row["cnt"]
            total += row["cnt"]
            min_ts = min(min_ts, row["first_call"])
            max_ts = max(max_ts, row["last_call"])

        return {
            "clients": clients,
            "total_calls": total,
            "time_range": (min_ts, max_ts),
        }

    def get_monitor_states(self) -> list[dict]:
        """Get the latest activity record per monitor.

        Returns list of dicts with: monitor_index, window_title, app_name,
        timestamp, diff_score.  Ordered by monitor_index.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.monitor_index, a.window_title, a.app_name, "
                "a.timestamp, a.diff_score "
                "FROM activity a "
                "INNER JOIN ("
                "  SELECT monitor_index, MAX(timestamp) AS max_ts "
                "  FROM activity GROUP BY monitor_index"
                ") latest ON a.monitor_index = latest.monitor_index "
                "AND a.timestamp = latest.max_ts "
                "ORDER BY a.monitor_index",
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self):
        self._conn.close()
