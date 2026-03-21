"""SQLite-backed activity tracking with FTS5 full-text search.

Records which apps/windows the user visits, links to buffer frames,
and stores OCR text for searchable screen history.
"""

import logging
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from contextpulse_sight.config import ACTIVITY_DB_PATH, ACTIVITY_MAX_AGE

logger = logging.getLogger("contextpulse.sight.activity")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    app_name TEXT NOT NULL DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    frame_path TEXT,
    ocr_text TEXT,
    ocr_confidence REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity(timestamp);
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
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(_SCHEMA)
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
    ) -> int:
        """Insert an activity record. Returns row ID."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO activity (timestamp, window_title, app_name, monitor_index, frame_path) "
                "VALUES (?, ?, ?, ?, ?)",
                (timestamp, window_title, app_name, monitor_index, frame_path),
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
        """Full-text search across window titles and OCR text."""
        cutoff = time.time() - (minutes_ago * 60)
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT a.id, a.timestamp, a.window_title, a.app_name, "
                    "a.monitor_index, a.frame_path, a.ocr_text, a.ocr_confidence "
                    "FROM activity a "
                    "JOIN activity_fts f ON a.id = f.rowid "
                    "WHERE activity_fts MATCH ? AND a.timestamp >= ? "
                    "ORDER BY a.timestamp DESC LIMIT 20",
                    (query, cutoff),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback to LIKE if FTS match syntax fails
                like_query = f"%{query}%"
                rows = self._conn.execute(
                    "SELECT id, timestamp, window_title, app_name, "
                    "monitor_index, frame_path, ocr_text, ocr_confidence "
                    "FROM activity "
                    "WHERE (window_title LIKE ? OR ocr_text LIKE ? OR app_name LIKE ?) "
                    "AND timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (like_query, like_query, like_query, cutoff),
                ).fetchall()

        return [dict(row) for row in rows]

    def get_context_at(self, minutes_ago: float) -> dict | None:
        """Get the frame + metadata from approximately N minutes ago."""
        target_ts = time.time() - (minutes_ago * 60)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, timestamp, window_title, app_name, "
                "monitor_index, frame_path, ocr_text, ocr_confidence "
                "FROM activity "
                "ORDER BY ABS(timestamp - ?) LIMIT 1",
                (target_ts,),
            ).fetchone()

        if not row:
            return None
        return dict(row)

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

    def close(self):
        self._conn.close()
