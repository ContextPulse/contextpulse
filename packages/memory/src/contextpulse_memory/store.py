"""ContextPulse Memory — SQLite-backed persistent memory store.

Provides key-value storage with full-text search, access tracking,
and automatic tier promotion based on usage frequency.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    modality TEXT DEFAULT '',
    created_at REAL NOT NULL,
    accessed_at REAL NOT NULL,
    access_count INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'hot'
);

CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_modality ON memories(modality);
"""

_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    key, value, content='memories', content_rowid=id
);
"""

_TRIGGER_INSERT_SQL = """
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, key, value)
    VALUES (new.id, new.key, new.value);
END;
"""

_TRIGGER_DELETE_SQL = """
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value)
    VALUES ('delete', old.id, old.key, old.value);
END;
"""

_TRIGGER_UPDATE_SQL = """
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value)
    VALUES ('delete', old.id, old.key, old.value);
    INSERT INTO memories_fts(rowid, key, value)
    VALUES (new.id, new.key, new.value);
END;
"""

# Tier promotion threshold
_WARM_THRESHOLD = 3


class MemoryStore:
    """SQLite-backed key-value memory with FTS5 search and tier promotion.

    Args:
        db_dir: Directory where memory.db will be created.
    """

    def __init__(self, db_dir: Path | str):
        self._db_dir = Path(db_dir)
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._db_dir / "memory.db"
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
        self._conn.executescript(_SCHEMA_SQL)
        try:
            cursor = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            )
            if cursor.fetchone() is None:
                self._conn.executescript(_FTS_SQL)
                self._conn.executescript(_TRIGGER_INSERT_SQL)
                self._conn.executescript(_TRIGGER_DELETE_SQL)
                self._conn.executescript(_TRIGGER_UPDATE_SQL)
        except sqlite3.OperationalError:
            self._conn.executescript(_FTS_SQL)
            self._conn.executescript(_TRIGGER_INSERT_SQL)
            self._conn.executescript(_TRIGGER_DELETE_SQL)
            self._conn.executescript(_TRIGGER_UPDATE_SQL)
        self._conn.commit()

    def store(self, key: str, value: Any, modality: str | None = None) -> dict:
        """Store or update a memory.

        Args:
            key: Unique identifier for this memory.
            value: Value to store (will be JSON-serialized).
            modality: Optional modality tag (e.g. "sight", "voice").

        Returns:
            Dict with the stored memory fields.
        """
        now = time.time()
        value_json = json.dumps(value) if not isinstance(value, str) else value

        self._conn.execute(
            """INSERT INTO memories (key, value, modality, created_at, accessed_at, access_count, tier)
               VALUES (?, ?, ?, ?, ?, 0, 'hot')
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   modality = COALESCE(excluded.modality, memories.modality),
                   accessed_at = excluded.accessed_at""",
            (key, value_json, modality or "", now, now),
        )
        self._conn.commit()

        return self._get_memory_dict(key)

    def recall(self, key: str) -> dict | None:
        """Retrieve a memory by key, updating access stats.

        Returns None if the key doesn't exist. Increments access_count
        and promotes to "warm" tier when access_count >= 3.
        """
        cursor = self._conn.execute(
            "SELECT * FROM memories WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        if row is None:
            return None

        now = time.time()
        new_count = row["access_count"] + 1
        new_tier = "warm" if new_count >= _WARM_THRESHOLD else row["tier"]

        self._conn.execute(
            """UPDATE memories
               SET accessed_at = ?, access_count = ?, tier = ?
               WHERE key = ?""",
            (now, new_count, new_tier, key),
        )
        self._conn.commit()

        return self._get_memory_dict(key)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search across keys and values.

        Args:
            query: Search terms (FTS5 syntax).
            limit: Maximum results.

        Returns:
            List of memory dicts matching the query.
        """
        try:
            cursor = self._conn.execute(
                """SELECT m.*
                   FROM memories_fts fts
                   JOIN memories m ON m.id = fts.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            )
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            # FTS syntax error — fall back to LIKE
            like = f"%{query}%"
            cursor = self._conn.execute(
                """SELECT * FROM memories
                   WHERE key LIKE ? OR value LIKE ?
                   ORDER BY accessed_at DESC
                   LIMIT ?""",
                (like, like, limit),
            )
            rows = cursor.fetchall()

        return [self._row_to_dict(r) for r in rows]

    def list_memories(
        self,
        modality: str | None = None,
        tier: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List memories with optional filters.

        Args:
            modality: Filter by modality tag.
            tier: Filter by tier ("hot", "warm", "cold").
            limit: Maximum results.
        """
        conditions = []
        params: list[Any] = []

        if modality:
            conditions.append("modality = ?")
            params.append(modality)
        if tier:
            conditions.append("tier = ?")
            params.append(tier)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        cursor = self._conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY accessed_at DESC LIMIT ?",
            params,
        )
        return [self._row_to_dict(r) for r in cursor.fetchall()]

    def forget(self, key: str) -> bool:
        """Delete a memory by key. Returns True if a row was deleted."""
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE key = ?", (key,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _get_memory_dict(self, key: str) -> dict:
        cursor = self._conn.execute(
            "SELECT * FROM memories WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        # Try to parse value as JSON for the return
        try:
            d["value"] = json.loads(d["value"])
        except (json.JSONDecodeError, TypeError):
            pass
        return d
