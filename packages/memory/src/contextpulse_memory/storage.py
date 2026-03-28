"""ContextPulse Memory — Three-tier storage engine.

Tiers:
  Hot  — in-memory dict, 5 min TTL, sub-millisecond reads
  Warm — SQLite WAL, 24h retention, FTS5 search
  Cold — SQLite FTS5 summaries, 30+ days, compressed
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Hot tier — in-memory dict with TTL eviction
# ---------------------------------------------------------------------------

class HotTier:
    """In-memory key-value store with TTL. Thread-safe via lock."""

    DEFAULT_TTL = 300.0   # 5 minutes
    MAX_SIZE = 500

    def __init__(self, default_ttl: float = DEFAULT_TTL, max_size: int = MAX_SIZE):
        self._default_ttl = default_ttl
        self._max_size = max_size
        # key -> (value, tags, expires_at)
        self._store: OrderedDict[str, tuple[str, list[str], float]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, key: str, value: str, tags: list[str], ttl: float | None = None) -> None:
        expires_at = time.time() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, tags, expires_at)
            # Evict oldest if over capacity
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def get(self, key: str) -> tuple[str, list[str]] | None:
        """Return (value, tags) or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, tags, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value, tags

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def keys_matching_tag(self, tag: str) -> list[str]:
        now = time.time()
        with self._lock:
            return [
                k for k, (_, tags, exp) in self._store.items()
                if tag in tags and now <= exp
            ]

    def evict_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, (_, _, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            return len(expired)

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Warm tier — SQLite WAL mode, 24h retention
# ---------------------------------------------------------------------------

_WARM_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL,
    attention_score REAL DEFAULT 0.0,
    source_event_id TEXT,
    modality TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_key ON memories(key);
CREATE INDEX IF NOT EXISTS idx_mem_expires ON memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mem_updated ON memories(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_modality ON memories(modality) WHERE modality IS NOT NULL;
"""

_WARM_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    key, value, tags,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
"""

_WARM_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, key, value, tags)
    VALUES (new.rowid, new.key, new.value, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
    VALUES ('delete', old.rowid, old.key, old.value, old.tags);
    INSERT INTO memories_fts(rowid, key, value, tags)
    VALUES (new.rowid, new.key, new.value, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
    VALUES ('delete', old.rowid, old.key, old.value, old.tags);
END;
"""


class WarmTier:
    """SQLite WAL-mode memory store with FTS5 search."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_WARM_SCHEMA)
            cursor = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            )
            if cursor.fetchone() is None:
                self._conn.executescript(_WARM_FTS)
                self._conn.executescript(_WARM_TRIGGERS)
            self._conn.commit()

    def upsert(
        self,
        key: str,
        value: str,
        tags: list[str],
        expires_at: float | None,
        attention_score: float = 0.0,
        source_event_id: str | None = None,
        modality: str | None = None,
    ) -> None:
        now = time.time()
        tags_json = json.dumps(sorted(tags))
        with self._lock:
            self._conn.execute(
                """INSERT INTO memories
                   (key, value, tags, created_at, updated_at, expires_at,
                    attention_score, source_event_id, modality)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value,
                     tags = excluded.tags,
                     updated_at = excluded.updated_at,
                     expires_at = excluded.expires_at,
                     attention_score = excluded.attention_score,
                     source_event_id = excluded.source_event_id,
                     modality = excluded.modality""",
                (key, value, tags_json, now, now, expires_at,
                 attention_score, source_event_id, modality),
            )
            self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM memories WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def delete(self, key: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE key = ? RETURNING id", (key,)
            )
            deleted = cursor.fetchone() is not None
            self._conn.commit()
        return deleted

    def list_all(
        self, tag: str | None = None, limit: int = 50, include_expired: bool = False
    ) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            if tag:
                # Tags are stored as JSON arrays; use LIKE for simple containment check
                if include_expired:
                    cursor = self._conn.execute(
                        "SELECT * FROM memories WHERE tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
                        (f'%"{tag}"%', limit),
                    )
                else:
                    cursor = self._conn.execute(
                        """SELECT * FROM memories
                           WHERE tags LIKE ?
                             AND (expires_at IS NULL OR expires_at > ?)
                           ORDER BY updated_at DESC LIMIT ?""",
                        (f'%"{tag}"%', now, limit),
                    )
            else:
                if include_expired:
                    cursor = self._conn.execute(
                        "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,)
                    )
                else:
                    cursor = self._conn.execute(
                        """SELECT * FROM memories
                           WHERE expires_at IS NULL OR expires_at > ?
                           ORDER BY updated_at DESC LIMIT ?""",
                        (now, limit),
                    )
            rows = cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            try:
                cursor = self._conn.execute(
                    """SELECT m.* FROM memories_fts f
                       JOIN memories m ON m.rowid = f.rowid
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
                    "SELECT * FROM memories WHERE key LIKE ? OR value LIKE ? LIMIT ?",
                    (like, like, limit),
                )
                rows = cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def prune_expired(self) -> int:
        """Delete expired entries. Returns count removed."""
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            self._conn.commit()
            return cursor.rowcount

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
        return d


# ---------------------------------------------------------------------------
# Cold tier — FTS5 summarized archive, 30+ day retention
# ---------------------------------------------------------------------------

_COLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS cold_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start REAL NOT NULL,
    window_end REAL NOT NULL,
    summary_json TEXT NOT NULL,
    text_content TEXT DEFAULT '',
    entry_count INTEGER DEFAULT 0,
    modalities TEXT DEFAULT '[]',
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    UNIQUE(window_start)
);

CREATE INDEX IF NOT EXISTS idx_cold_time ON cold_summaries(window_start DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS cold_fts USING fts5(
    text_content,
    content='cold_summaries',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS cold_ai AFTER INSERT ON cold_summaries BEGIN
    INSERT INTO cold_fts(rowid, text_content)
    VALUES (new.rowid, new.text_content);
END;

CREATE TRIGGER IF NOT EXISTS cold_ad AFTER DELETE ON cold_summaries BEGIN
    INSERT INTO cold_fts(cold_fts, rowid, text_content)
    VALUES ('delete', old.rowid, old.text_content);
END;
"""

_COLD_WINDOW = 900  # 15-minute summary windows


class ColdTier:
    """Compressed long-term archive. Entries are summarized into 15-minute windows."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_COLD_SCHEMA)
            self._conn.commit()

    def ingest(self, entries: list[dict[str, Any]]) -> int:
        """Summarize a batch of warm entries into 15-minute cold windows."""
        if not entries:
            return 0

        windows: dict[int, list[dict]] = {}
        for entry in entries:
            wk = int(entry["updated_at"] // _COLD_WINDOW)
            windows.setdefault(wk, []).append(entry)

        written = 0
        with self._lock:
            for wk, batch in windows.items():
                window_start = wk * _COLD_WINDOW
                window_end = window_start + _COLD_WINDOW

                text_parts: list[str] = []
                modalities: set[str] = set()
                for e in batch:
                    text_parts.append(e.get("key", ""))
                    text_parts.append(e.get("value", ""))
                    if e.get("modality"):
                        modalities.add(e["modality"])

                summary = {"entry_count": len(batch), "keys": [e["key"] for e in batch]}
                self._conn.execute(
                    """INSERT OR REPLACE INTO cold_summaries
                       (window_start, window_end, summary_json, text_content,
                        entry_count, modalities)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        window_start,
                        window_end,
                        json.dumps(summary),
                        "\n".join(filter(None, text_parts)),
                        len(batch),
                        json.dumps(sorted(modalities)),
                    ),
                )
                written += 1
            self._conn.commit()
        return written

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            try:
                cursor = self._conn.execute(
                    """SELECT s.* FROM cold_summaries s
                       JOIN cold_fts f ON s.rowid = f.rowid
                       WHERE cold_fts MATCH ?
                       ORDER BY s.window_start DESC
                       LIMIT ?""",
                    (query, limit),
                )
            except sqlite3.OperationalError:
                cursor = self._conn.execute(
                    "SELECT * FROM cold_summaries WHERE text_content LIKE ? ORDER BY window_start DESC LIMIT ?",
                    (f"%{query}%", limit),
                )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM cold_summaries").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# MemoryStore — orchestrates all three tiers
# ---------------------------------------------------------------------------

class MemoryStore:
    """Three-tier memory store: Hot (in-memory) → Warm (SQLite WAL) → Cold (FTS5 archive).

    Default DB files:
      warm: <data_dir>/memory.db
      cold: <data_dir>/memory_cold.db
    """

    DEFAULT_HOT_TTL = 300.0   # 5 minutes
    DEFAULT_WARM_TTL = 86_400.0  # 24 hours

    def __init__(self, data_dir: Path | str):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.hot = HotTier(default_ttl=self.DEFAULT_HOT_TTL)
        self.warm = WarmTier(self._data_dir / "memory.db")
        self.cold = ColdTier(self._data_dir / "memory_cold.db")

    def store(
        self,
        key: str,
        value: str,
        tags: list[str] | None = None,
        ttl_hours: float | None = 24.0,
        attention_score: float = 0.0,
        source_event_id: str | None = None,
        modality: str | None = None,
    ) -> None:
        tags = tags or []
        expires_at = time.time() + (ttl_hours * 3600) if ttl_hours else None
        hot_ttl = min(ttl_hours * 3600, self.DEFAULT_HOT_TTL) if ttl_hours else self.DEFAULT_HOT_TTL
        self.hot.put(key, value, tags, ttl=hot_ttl)
        self.warm.upsert(
            key=key, value=value, tags=tags, expires_at=expires_at,
            attention_score=attention_score,
            source_event_id=source_event_id, modality=modality,
        )

    def recall(self, key: str) -> dict[str, Any] | None:
        """Recall a memory by key. Checks hot tier first, then warm."""
        hot_hit = self.hot.get(key)
        if hot_hit:
            value, tags = hot_hit
            return {"key": key, "value": value, "tags": tags, "tier": "hot"}
        return self.warm.get(key)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """FTS search across warm tier, with cold tier fallback label."""
        results = self.warm.search(query, limit=limit)
        if len(results) < limit:
            cold = self.cold.search(query, limit=limit - len(results))
            for r in cold:
                r["tier"] = "cold"
            results.extend(cold)
        return results

    def list_all(self, tag: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.warm.list_all(tag=tag, limit=limit)

    def forget(self, key: str) -> bool:
        self.hot.delete(key)
        return self.warm.delete(key)

    def prune(self) -> dict[str, int]:
        hot_pruned = self.hot.evict_expired()
        warm_pruned = self.warm.prune_expired()
        return {"hot": hot_pruned, "warm": warm_pruned}

    def close(self) -> None:
        self.warm.close()
        self.cold.close()
