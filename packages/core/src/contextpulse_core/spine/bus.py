"""ContextPulse Spine — EventBus for routing events to storage and listeners.

The EventBus:
1. Validates incoming ContextEvent objects
2. Persists them to the `events` table in activity.db
3. Updates the FTS5 index for cross-modal search
4. Notifies registered listeners

The EventBus opens the SAME activity.db used by Sight's ActivityDB.
It adds an `events` table alongside existing tables (activity, clipboard,
mcp_calls) without modifying them.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from .events import ContextEvent, Modality

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    modality TEXT NOT NULL,
    event_type TEXT NOT NULL,
    app_name TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    payload TEXT NOT NULL,
    correlation_id TEXT,
    attention_score REAL DEFAULT 0.0,
    cognitive_load REAL DEFAULT 0.0,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_modality ON events(modality, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(app_name, timestamp DESC);
"""

# FTS5 and triggers are created separately because they require special handling
_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    window_title, app_name, text_content,
    content='events', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""

_TRIGGER_INSERT_SQL = """
CREATE TRIGGER IF NOT EXISTS events_fts_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, window_title, app_name, text_content)
    VALUES (
        new.rowid,
        new.window_title,
        new.app_name,
        COALESCE(
            json_extract(new.payload, '$.ocr_text'),
            json_extract(new.payload, '$.transcript'),
            json_extract(new.payload, '$.text'),
            ''
        )
    );
END;
"""

_TRIGGER_DELETE_SQL = """
CREATE TRIGGER IF NOT EXISTS events_fts_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, window_title, app_name, text_content)
    VALUES ('delete', old.rowid, old.window_title, old.app_name, '');
END;
"""


class EventBus:
    """Central event router for the ContextPulse spine.

    Opens the same activity.db file used by Sight's ActivityDB.
    Adds events + events_fts tables alongside existing tables.
    Thread-safe via threading.Lock().
    """

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[ContextEvent], None]] = []
        self._conn = self._open_connection()
        self._init_schema()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=10,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        """Create events table and FTS index if they don't exist."""
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            # FTS5 creation needs special handling — check if it exists first
            try:
                self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='events_fts'"
                )
                row = self._conn.fetchone() if hasattr(self._conn, 'fetchone') else None
                # Use cursor properly
                cursor = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events_fts'"
                )
                if cursor.fetchone() is None:
                    self._conn.executescript(_FTS_SQL)
                    self._conn.executescript(_TRIGGER_INSERT_SQL)
                    self._conn.executescript(_TRIGGER_DELETE_SQL)
            except sqlite3.OperationalError:
                # FTS5 table doesn't exist yet
                self._conn.executescript(_FTS_SQL)
                self._conn.executescript(_TRIGGER_INSERT_SQL)
                self._conn.executescript(_TRIGGER_DELETE_SQL)
            self._conn.commit()

    def emit(self, event: ContextEvent) -> None:
        """Validate and persist an event, then notify listeners.

        Args:
            event: A ContextEvent to store.

        Raises:
            ValueError: If the event fails validation.
        """
        if not event.validate():
            raise ValueError(f"Invalid event: {event.event_id}")

        row = event.to_row()
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, timestamp, modality, event_type, app_name,
                    window_title, monitor_index, payload, correlation_id,
                    attention_score, cognitive_load)
                   VALUES (:event_id, :timestamp, :modality, :event_type,
                           :app_name, :window_title, :monitor_index, :payload,
                           :correlation_id, :attention_score, :cognitive_load)""",
                row,
            )
            self._conn.commit()

        # Notify listeners outside the lock to avoid deadlocks
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Listener error for event %s", event.event_id)

    def on(self, callback: Callable[[ContextEvent], None]) -> None:
        """Register a listener called on every emit."""
        self._listeners.append(callback)

    def query_recent(
        self,
        seconds: float = 300,
        modality: str | None = None,
        limit: int = 50,
    ) -> list[ContextEvent]:
        """Return recent events, optionally filtered by modality.

        Args:
            seconds: How far back to look (default 5 minutes).
            modality: Filter to a specific modality (e.g. "sight", "voice").
            limit: Maximum events to return.
        """
        import time as _time

        cutoff = _time.time() - seconds
        with self._lock:
            if modality:
                cursor = self._conn.execute(
                    """SELECT * FROM events
                       WHERE timestamp > ? AND modality = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (cutoff, modality, limit),
                )
            else:
                cursor = self._conn.execute(
                    """SELECT * FROM events
                       WHERE timestamp > ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (cutoff, limit),
                )
            rows = cursor.fetchall()

        return [ContextEvent.from_row(dict(r)) for r in rows]

    def search(
        self,
        query: str,
        minutes_ago: float = 30,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 search across all event text content.

        Args:
            query: Search terms (FTS5 syntax supported).
            minutes_ago: How far back to search.
            modality: Optional modality filter.

        Returns:
            List of dicts with event data and FTS rank.
        """
        import time as _time

        cutoff = _time.time() - (minutes_ago * 60)

        with self._lock:
            try:
                if modality:
                    cursor = self._conn.execute(
                        """SELECT e.*, rank
                           FROM events_fts fts
                           JOIN events e ON e.rowid = fts.rowid
                           WHERE events_fts MATCH ?
                             AND e.timestamp > ?
                             AND e.modality = ?
                           ORDER BY rank
                           LIMIT 50""",
                        (query, cutoff, modality),
                    )
                else:
                    cursor = self._conn.execute(
                        """SELECT e.*, rank
                           FROM events_fts fts
                           JOIN events e ON e.rowid = fts.rowid
                           WHERE events_fts MATCH ?
                             AND e.timestamp > ?
                           ORDER BY rank
                           LIMIT 50""",
                        (query, cutoff),
                    )
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                # FTS syntax error — fall back to LIKE search
                logger.warning("FTS query failed, falling back to LIKE: %s", query)
                like_pattern = f"%{query}%"
                if modality:
                    cursor = self._conn.execute(
                        """SELECT * FROM events
                           WHERE timestamp > ?
                             AND modality = ?
                             AND (window_title LIKE ? OR app_name LIKE ?
                                  OR payload LIKE ?)
                           ORDER BY timestamp DESC LIMIT 50""",
                        (cutoff, modality, like_pattern, like_pattern, like_pattern),
                    )
                else:
                    cursor = self._conn.execute(
                        """SELECT * FROM events
                           WHERE timestamp > ?
                             AND (window_title LIKE ? OR app_name LIKE ?
                                  OR payload LIKE ?)
                           ORDER BY timestamp DESC LIMIT 50""",
                        (cutoff, like_pattern, like_pattern, like_pattern),
                    )
                rows = cursor.fetchall()

        return [dict(r) for r in rows]

    def get_by_time(
        self,
        target_timestamp: float,
        window_seconds: float = 5,
    ) -> list[ContextEvent]:
        """Get events within a time window around a target timestamp.

        Useful for temporal correlation — finding what was happening
        across all modalities at a specific moment.
        """
        with self._lock:
            cursor = self._conn.execute(
                """SELECT * FROM events
                   WHERE timestamp BETWEEN ? AND ?
                   ORDER BY timestamp""",
                (target_timestamp - window_seconds,
                 target_timestamp + window_seconds),
            )
            rows = cursor.fetchall()

        return [ContextEvent.from_row(dict(r)) for r in rows]

    def count(self, modality: str | None = None) -> int:
        """Return total event count, optionally filtered by modality."""
        with self._lock:
            if modality:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM events WHERE modality = ?",
                    (modality,),
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM events")
            return cursor.fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
