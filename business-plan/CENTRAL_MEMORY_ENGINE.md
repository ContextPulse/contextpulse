# ContextPulse — Central Memory Engine Architecture

**March 2026 | Jerard Ventures LLC | CONFIDENTIAL**

---

## Table of Contents

1. [Philosophy & Architecture Position](#1-philosophy--architecture-position)
2. [Core Architecture](#2-core-architecture)
3. [Unified Event Schema](#3-unified-event-schema)
4. [Memory Tiers](#4-memory-tiers)
5. [Temporal Correlation Engine](#5-temporal-correlation-engine)
6. [Learning Engine](#6-learning-engine)
7. [Module Interface Contract](#7-module-interface-contract)
8. [MCP Tool Design](#8-mcp-tool-design)
9. [Data Flow Diagrams](#9-data-flow-diagrams)
10. [Technical Specifications](#10-technical-specifications)
11. [Implementation Roadmap (Revised)](#11-implementation-roadmap-revised)

---

## 1. Philosophy & Architecture Position

### Why Memory-First, Not Module-First

The current technical plan builds ContextPulse bottom-up: Sight (done) -> Voice (Q3 2026) -> Keys (Q4 2026) -> Flow (Q1 2027) -> Learning Engine (Q2 2027). Each module is designed as a standalone capture pipeline that writes to its own table in a shared SQLite database. The learning engine arrives last — Phase 5, roughly fourteen months from now — and attempts to retroactively correlate data that was never designed to be correlated.

This is backwards. Here is why:

**The learning engine is not a feature. It is the product.** ContextPulse's deepest moat — the one the business plan correctly identifies as "cannot be replicated without the same four-year head start" — is cross-modal learning. A screen capture tool is commodity. A voice transcription tool is commodity. A keyboard logger is commodity. What is not commodity is a system that observes you type a correction three seconds after a voice transcription, links that correction to the screen content visible at the time, updates a personal vocabulary model, and uses that model to improve every future transcription. That system requires a unified memory architecture from day one.

**Bolting learning onto isolated modules produces inferior data.** When Sight writes `activity` rows and Voice writes `audio_segments` rows with a loose `nearest_frame_id` foreign key, the temporal relationship is an afterthought. The two modules never share a common event format, never flow through a common correlation pipeline, never contribute to a shared attention model. The learning engine in Phase 5 would spend most of its complexity budget on data normalization rather than actual learning.

**Each new modality should make ALL existing modalities smarter immediately.** When Voice comes online, Sight should instantly benefit — screen OCR text can validate transcriptions, transcription context can improve OCR confidence scoring. When Keys comes online, both Voice and Sight should benefit — typing patterns reveal what the user is focused on, correction pairs improve vocabulary for both OCR and Whisper. This compounding effect only works if all modalities feed into a single memory engine that was designed for cross-modal correlation from the start.

### The Spinal Cord Metaphor

The Central Memory Engine is the spinal cord. Modalities (Sight, Voice, Keys, Flow) are sensory organs — they capture raw signals and emit standardized events. The spinal cord receives all sensory input, correlates it temporally, routes it to the appropriate memory tier, feeds the learning engine, and serves queries from the MCP layer.

```
Sensory Organs (capture)          Spinal Cord (memory + learning)       Brain (AI agents)
┌──────────┐                     ┌─────────────────────────────┐      ┌──────────────┐
│  Sight   │──── events ────────>│                             │      │  Claude Code │
│  Voice   │──── events ────────>│   Central Memory Engine     │<─────│  Cursor      │
│  Keys    │──── events ────────>│                             │      │  Gemini CLI  │
│  Flow    │──── events ────────>│   - Event Bus               │      │  VS Code     │
└──────────┘                     │   - Context Graph            │      └──────────────┘
                                 │   - Memory Tiers             │           ^
                                 │   - Correlation Engine       │           |
                                 │   - Learning Engine          │      MCP tools
                                 │   - Query Interface          │           |
                                 └──────────────┬──────────────┘           |
                                                └──────────────────────────┘
```

No modality talks to another modality directly. No modality writes to the database directly. Every event flows through the engine. This constraint is what makes cross-modal learning possible without O(n^2) module-to-module integrations.

### The Compounding Moat

With isolated modules, adding a fourth modality creates four independent data streams. With a central memory engine, adding a fourth modality creates:

| Modalities | Isolated Streams | Cross-Modal Pairs | Compounding Factor |
|-----------|-----------------|-------------------|-------------------|
| 1 (Sight) | 1 | 0 | 1x |
| 2 (+Voice) | 2 | 1 | 1.5x |
| 3 (+Keys) | 3 | 3 | 2x |
| 4 (+Flow) | 4 | 6 | 2.5x |

Each cross-modal pair generates correction signals, attention weights, and cognitive load data that improves every other pair. A competitor who copies Sight alone gets 1x value. A competitor who copies all four modules but without the memory engine gets 4x value. ContextPulse with the memory engine delivers 2.5x per modality — 10x total. That gap widens with time because the learning engine improves with accumulated data.

---

## 2. Core Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CENTRAL MEMORY ENGINE                              │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        UNIFIED EVENT BUS                              │  │
│  │                                                                       │  │
│  │  Receives ContextEvent objects from all modalities                    │  │
│  │  Validates schema conformance                                         │  │
│  │  Assigns correlation IDs                                              │  │
│  │  Routes to memory tiers + correlation engine                         │  │
│  └───────┬───────────────────┬───────────────────┬──────────────────────┘  │
│          │                   │                   │                          │
│          v                   v                   v                          │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────────┐              │
│  │   HOT TIER   │   │  WARM TIER   │   │    COLD TIER      │              │
│  │  (ring buf)  │   │  (SQLite)    │   │  (summarized)     │              │
│  │  last 5 min  │   │  last 24h    │   │  30+ days         │              │
│  │  in-memory   │   │  WAL mode    │   │  FTS5 searchable  │              │
│  └──────┬───────┘   └──────┬───────┘   └─────────┬─────────┘              │
│         │                  │                      │                         │
│         └──────────┬───────┴──────────────────────┘                         │
│                    │                                                         │
│         ┌──────────v──────────┐                                             │
│         │  TEMPORAL           │                                             │
│         │  CORRELATION        │                                             │
│         │  ENGINE             │                                             │
│         │                     │                                             │
│         │  Links events by    │                                             │
│         │  time windows,      │                                             │
│         │  app context,       │                                             │
│         │  semantic overlap   │                                             │
│         └──────────┬──────────┘                                             │
│                    │                                                         │
│         ┌──────────v──────────┐                                             │
│         │  LEARNING ENGINE    │                                             │
│         │                     │                                             │
│         │  Correction pairs   │                                             │
│         │  Vocabulary model   │                                             │
│         │  Attention scoring  │                                             │
│         │  Cognitive load     │                                             │
│         │  Behavior patterns  │                                             │
│         └──────────┬──────────┘                                             │
│                    │                                                         │
│         ┌──────────v──────────┐                                             │
│         │  QUERY INTERFACE    │                                             │
│         │  (MCP tool layer)   │                                             │
│         └─────────────────────┘                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Thread Model |
|-----------|---------------|-------------|
| Event Bus | Receive, validate, route events | Main thread (queue consumer) |
| Hot Tier | Sub-second access to recent events | In-memory, lock-free ring buffer |
| Warm Tier | Indexed queryable storage for 24h | SQLite WAL, dedicated writer thread |
| Cold Tier | Compressed long-term searchable archive | Nightly batch job |
| Correlation Engine | Link events across modalities by time + context | Background thread, 5-second cycle |
| Learning Engine | Extract patterns, update models | Background thread, 60-second cycle |
| Query Interface | Serve MCP tool requests | MCP server thread (existing) |

---

## 3. Unified Event Schema

### The Core Principle

Every modality emits `ContextEvent` objects. No exceptions. A screen capture, a voice transcription, a keystroke burst, a mouse click — all are `ContextEvent` instances with modality-specific payloads. This is the contract that makes cross-modal correlation possible.

### Schema Definition

```python
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Modality(Enum):
    SIGHT = "sight"
    VOICE = "voice"
    KEYS = "keys"
    FLOW = "flow"
    CLIPBOARD = "clipboard"
    SYSTEM = "system"       # window focus, idle, lock events


class EventType(Enum):
    # Sight
    SCREEN_CAPTURE = "screen_capture"
    OCR_RESULT = "ocr_result"

    # Voice
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    TRANSCRIPTION = "transcription"

    # Keys
    KEYSTROKE = "keystroke"
    TYPING_BURST = "typing_burst"
    TYPING_PAUSE = "typing_pause"
    SHORTCUT = "shortcut"

    # Flow
    CLICK = "click"
    SCROLL = "scroll"
    HOVER_DWELL = "hover_dwell"
    DRAG = "drag"

    # Clipboard
    CLIPBOARD_CHANGE = "clipboard_change"

    # System
    WINDOW_FOCUS = "window_focus"
    IDLE_START = "idle_start"
    IDLE_END = "idle_end"
    SESSION_LOCK = "session_lock"
    SESSION_UNLOCK = "session_unlock"


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """The universal event format for the Central Memory Engine.

    Every modality module MUST emit events conforming to this schema.
    The engine rejects non-conforming events at the bus level.
    """

    # === Required fields (all modalities) ===
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = field(default_factory=time.time)
    modality: Modality = Modality.SYSTEM
    event_type: EventType = EventType.WINDOW_FOCUS

    # === Context fields (populated by capture module) ===
    app_name: str = ""
    window_title: str = ""
    monitor_index: int = 0

    # === Modality-specific payload ===
    # Stored as dict to allow modality-specific data without schema changes.
    # Each modality defines its own payload contract (see Section 7).
    payload: dict[str, Any] = field(default_factory=dict)

    # === Correlation fields (populated by the engine, not the module) ===
    correlation_id: str | None = None    # Set by correlation engine
    attention_score: float = 0.0         # Set by learning engine
    cognitive_load: float = 0.0          # Set by learning engine

    def validate(self) -> bool:
        """Check required fields are present and valid."""
        if not self.event_id or not isinstance(self.timestamp, float):
            return False
        if self.timestamp <= 0 or self.timestamp > time.time() + 60:
            return False
        if not isinstance(self.modality, Modality):
            return False
        if not isinstance(self.event_type, EventType):
            return False
        return True
```

### Payload Contracts by Modality

Each modality defines the structure of its `payload` dict. The engine does not enforce payload schema (modality-specific logic owns that), but these contracts are documented for cross-modal consumers.

```python
# Sight payload
SightCapturePayload = {
    "frame_path": str,          # Path to JPEG file
    "ocr_text": str | None,     # Extracted text (None if image-only)
    "ocr_confidence": float,    # 0.0 - 1.0
    "diff_score": float,        # Change from previous frame
    "token_estimate": int,      # Estimated API tokens for this frame
    "storage_mode": str,        # "image", "text_only", "hybrid"
}

# Voice payload
VoicePayload = {
    "transcript": str,          # Whisper output
    "confidence": float,        # 0.0 - 1.0
    "speaker_id": str | None,   # Diarization label
    "language": str,            # ISO 639-1
    "duration_seconds": float,  # Segment duration
    "audio_path": str | None,   # Temporary path (deleted post-transcription)
}

# Keys payload
KeysPayload = {
    "capture_mode": str,        # "full", "metadata_only", "blocked"
    "key_char": str | None,     # None when metadata_only or blocked
    "is_shortcut": bool,
    "wpm_snapshot": float,      # Current WPM at time of event
    "burst_text": str | None,   # For TYPING_BURST events: accumulated text
    "burst_duration": float,    # For TYPING_BURST events: seconds
}

# Flow payload
FlowPayload = {
    "x": int,
    "y": int,
    "target_element": str | None,    # UIA element name
    "target_control": str | None,    # UIA control type
    "dwell_duration": float | None,  # For HOVER_DWELL events
    "efficiency_ratio": float | None,# straight_line / actual_path
    "scroll_delta": int | None,      # For SCROLL events
}

# Clipboard payload
ClipboardPayload = {
    "text": str,
    "hash": str,                # For deduplication
    "source_app": str | None,   # App that owned clipboard
}
```

### SQLite Persistence Schema

```sql
-- The unified event store. ALL events from ALL modalities land here.
-- This replaces the per-module table design from the original technical plan.

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    modality TEXT NOT NULL,          -- 'sight', 'voice', 'keys', 'flow', 'clipboard', 'system'
    event_type TEXT NOT NULL,        -- 'screen_capture', 'transcription', etc.
    app_name TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    payload TEXT NOT NULL,           -- JSON blob
    correlation_id TEXT,             -- Links cross-modal events
    attention_score REAL DEFAULT 0.0,
    cognitive_load REAL DEFAULT 0.0,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);

-- Indexes for the three primary query patterns
CREATE INDEX idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX idx_events_modality_time ON events(modality, timestamp DESC);
CREATE INDEX idx_events_correlation ON events(correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_events_app_time ON events(app_name, timestamp DESC);

-- Full-text search across all modalities
-- Searchable columns: window_title, app_name, and extracted text content
CREATE VIRTUAL TABLE events_fts USING fts5(
    window_title,
    app_name,
    text_content,      -- Extracted from payload: ocr_text, transcript, burst_text, clipboard text
    content='events',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, window_title, app_name, text_content)
    VALUES (
        new.rowid,
        new.window_title,
        new.app_name,
        COALESCE(
            json_extract(new.payload, '$.ocr_text'),
            json_extract(new.payload, '$.transcript'),
            json_extract(new.payload, '$.burst_text'),
            json_extract(new.payload, '$.text'),
            ''
        )
    );
END;

CREATE TRIGGER events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, window_title, app_name, text_content)
    VALUES ('delete', old.rowid, old.window_title, old.app_name,
        COALESCE(
            json_extract(old.payload, '$.ocr_text'),
            json_extract(old.payload, '$.transcript'),
            json_extract(old.payload, '$.burst_text'),
            json_extract(old.payload, '$.text'),
            ''
        )
    );
END;

-- Correlation groups: sets of events linked by temporal/semantic proximity
CREATE TABLE correlations (
    correlation_id TEXT PRIMARY KEY,
    timestamp_start REAL NOT NULL,
    timestamp_end REAL NOT NULL,
    modalities TEXT NOT NULL,         -- JSON array: ["sight", "voice"]
    event_count INTEGER DEFAULT 0,
    summary TEXT,                     -- Human-readable summary (cold tier)
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);

CREATE INDEX idx_correlations_time ON correlations(timestamp_start DESC);

-- Correction pairs: cross-modal learning signals
CREATE TABLE correction_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    source_modality TEXT NOT NULL,
    target_modality TEXT NOT NULL,
    source_event_id TEXT NOT NULL REFERENCES events(event_id),
    target_event_id TEXT NOT NULL REFERENCES events(event_id),
    original_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    applied INTEGER DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);

-- Vocabulary model: learned corrections and domain terms
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    correction TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    source TEXT DEFAULT 'correction',   -- 'correction', 'frequency', 'manual'
    last_seen REAL NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);

CREATE INDEX idx_vocabulary_confidence ON vocabulary(confidence DESC);

-- User behavior model: aggregated patterns
CREATE TABLE behavior_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,         -- 'app_usage', 'peak_hours', 'fatigue_curve', 'focus_apps'
    pattern_key TEXT NOT NULL,          -- e.g., app name, hour of day
    pattern_value TEXT NOT NULL,        -- JSON blob with pattern data
    sample_count INTEGER DEFAULT 1,
    last_updated REAL NOT NULL,
    UNIQUE(pattern_type, pattern_key)
);

-- MCP call tracking (preserved from existing schema)
CREATE TABLE mcp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    tool_name TEXT NOT NULL,
    client_id TEXT DEFAULT 'unknown',
    response_time_ms REAL,
    modalities_used TEXT               -- JSON array of modalities consulted
);

-- Engine metadata: tracks tier migration, learning cycles, etc.
CREATE TABLE engine_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
```

---

## 4. Memory Tiers

### Tier Architecture

The three-tier memory system balances access speed against storage cost. Events flow from hot to warm to cold automatically, with each transition applying compression and summarization.

```
Time ──────────────────────────────────────────────────────>

│<── HOT (5 min) ──>│<──── WARM (24 hours) ────>│<── COLD (30+ days) ──>│
│                    │                            │                       │
│  Ring buffer       │  SQLite WAL               │  Summarized           │
│  In-memory         │  Full events indexed       │  FTS5 searchable     │
│  ~500 events       │  ~50,000 events/day        │  ~1,000 summaries/mo │
│  <1ms access       │  <10ms access              │  <100ms search       │
│  No persistence    │  WAL + checkpoints         │  Compressed JSON     │
│                    │                            │                       │
│  ALL fields        │  ALL fields               │  Aggregated:          │
│  ALL payloads      │  Full payloads             │  - text_content only │
│  Raw file refs     │  File refs valid           │  - no frame paths    │
│                    │                            │  - correlation sums  │
```

### Hot Tier: In-Memory Ring Buffer

```python
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class HotTierConfig:
    max_events: int = 500           # ~5 minutes at normal activity
    max_age_seconds: float = 300.0  # 5 minutes hard cutoff
    flush_interval: float = 5.0     # Seconds between warm-tier writes


class HotTier:
    """In-memory ring buffer for sub-millisecond access to recent events.

    Thread-safe. Lock-free reads via deque snapshot. Writers acquire a
    lightweight lock only for append (deque.append is O(1) amortized).
    """

    def __init__(self, config: HotTierConfig | None = None):
        self.config = config or HotTierConfig()
        self._buffer: deque[ContextEvent] = deque(maxlen=self.config.max_events)
        self._lock = threading.Lock()

    def push(self, event: ContextEvent) -> None:
        """Add event to hot tier. O(1). Oldest events auto-evicted."""
        with self._lock:
            self._buffer.append(event)

    def get_recent(
        self,
        seconds: float = 60.0,
        modality: Modality | None = None,
        limit: int = 50,
    ) -> list[ContextEvent]:
        """Retrieve recent events. Returns newest first."""
        cutoff = time.time() - seconds
        # Snapshot the deque (lock-free read of immutable tuple)
        snapshot = tuple(self._buffer)
        results = []
        for event in reversed(snapshot):
            if event.timestamp < cutoff:
                break
            if modality and event.modality != modality:
                continue
            results.append(event)
            if len(results) >= limit:
                break
        return results

    def get_at_timestamp(
        self, target: float, window_seconds: float = 5.0
    ) -> list[ContextEvent]:
        """Get all events within a time window around a target timestamp."""
        lo = target - window_seconds
        hi = target + window_seconds
        snapshot = tuple(self._buffer)
        return [e for e in snapshot if lo <= e.timestamp <= hi]

    def drain_for_warm(self, older_than: float) -> list[ContextEvent]:
        """Remove and return events older than threshold for warm-tier persistence."""
        with self._lock:
            to_persist = []
            while self._buffer and self._buffer[0].timestamp < older_than:
                to_persist.append(self._buffer.popleft())
            return to_persist

    def __len__(self) -> int:
        return len(self._buffer)
```

### Warm Tier: SQLite WAL

The warm tier is the primary queryable store. It holds 24 hours of full-resolution events with all indexes and FTS active.

```python
import json
import sqlite3
from pathlib import Path


class WarmTier:
    """SQLite WAL-mode storage for the last 24 hours of events.

    Handles persistence, indexing, FTS updates, and serves as the
    primary query backend for MCP tools.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA cache_size=-20000")  # 20MB cache
        self._conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        self._create_schema()

    def persist_events(self, events: list[ContextEvent]) -> int:
        """Batch-insert events from hot tier. Returns count inserted."""
        if not events:
            return 0
        rows = [
            (
                e.event_id,
                e.timestamp,
                e.modality.value,
                e.event_type.value,
                e.app_name,
                e.window_title,
                e.monitor_index,
                json.dumps(e.payload),
                e.correlation_id,
                e.attention_score,
                e.cognitive_load,
            )
            for e in events
        ]
        self._conn.executemany(
            """INSERT OR IGNORE INTO events
               (event_id, timestamp, modality, event_type, app_name,
                window_title, monitor_index, payload, correlation_id,
                attention_score, cognitive_load)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def query_time_range(
        self,
        start: float,
        end: float,
        modalities: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Query events within a time range, optionally filtered by modality."""
        sql = "SELECT * FROM events WHERE timestamp BETWEEN ? AND ?"
        params: list = [start, end]
        if modalities:
            placeholders = ",".join("?" * len(modalities))
            sql += f" AND modality IN ({placeholders})"
            params.extend(modalities)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor = self._conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def search_text(self, query: str, limit: int = 50) -> list[dict]:
        """Full-text search across all modalities."""
        cursor = self._conn.execute(
            """SELECT e.* FROM events e
               JOIN events_fts f ON e.rowid = f.rowid
               WHERE events_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def prune_to_cold(self, older_than: float) -> list[dict]:
        """Remove events older than threshold. Returns removed events
        for cold-tier summarization."""
        cursor = self._conn.execute(
            "SELECT * FROM events WHERE timestamp < ? ORDER BY timestamp",
            (older_than,),
        )
        old_events = [dict(row) for row in cursor.fetchall()]
        if old_events:
            self._conn.execute(
                "DELETE FROM events WHERE timestamp < ?", (older_than,)
            )
            self._conn.commit()
        return old_events

    def _create_schema(self) -> None:
        """Initialize schema if tables do not exist."""
        # Schema from Section 3 applied here
        ...
```

### Cold Tier: Summarized Archive

```python
class ColdTier:
    """Compressed long-term storage. Events are summarized into
    time-windowed blocks (15-minute windows). Individual events are
    discarded; text content and correlation data are preserved for search.

    Uses a separate SQLite database to keep warm-tier performance clean.
    """

    SUMMARY_WINDOW_SECONDS = 900  # 15 minutes

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def ingest_from_warm(self, events: list[dict]) -> int:
        """Summarize a batch of warm-tier events into cold storage.

        Groups events into 15-minute windows, extracts text content,
        counts events per modality, and stores a compressed summary.
        """
        if not events:
            return 0

        # Group by 15-minute window
        windows: dict[int, list[dict]] = {}
        for event in events:
            window_key = int(event["timestamp"] // self.SUMMARY_WINDOW_SECONDS)
            windows.setdefault(window_key, []).append(event)

        summaries_written = 0
        for window_key, window_events in windows.items():
            window_start = window_key * self.SUMMARY_WINDOW_SECONDS
            window_end = window_start + self.SUMMARY_WINDOW_SECONDS

            # Aggregate
            modality_counts: dict[str, int] = {}
            text_parts: list[str] = []
            app_names: set[str] = set()
            correlation_ids: set[str] = set()

            for ev in window_events:
                mod = ev["modality"]
                modality_counts[mod] = modality_counts.get(mod, 0) + 1
                app_names.add(ev.get("app_name", ""))

                # Extract searchable text from payload
                try:
                    payload = json.loads(ev["payload"]) if isinstance(ev["payload"], str) else ev["payload"]
                except (json.JSONDecodeError, TypeError):
                    payload = {}

                for text_key in ("ocr_text", "transcript", "burst_text", "text"):
                    if text_key in payload and payload[text_key]:
                        text_parts.append(str(payload[text_key]))

                if ev.get("correlation_id"):
                    correlation_ids.add(ev["correlation_id"])

            summary = {
                "modality_counts": modality_counts,
                "apps": sorted(app_names - {""}),
                "correlation_ids": sorted(correlation_ids),
                "event_count": len(window_events),
            }

            self._conn.execute(
                """INSERT OR REPLACE INTO cold_summaries
                   (window_start, window_end, summary_json, text_content,
                    event_count, modalities)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    window_start,
                    window_end,
                    json.dumps(summary),
                    "\n".join(text_parts),
                    len(window_events),
                    json.dumps(sorted(modality_counts.keys())),
                ),
            )
            summaries_written += 1

        self._conn.commit()
        return summaries_written

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Full-text search across cold storage summaries."""
        cursor = self._conn.execute(
            """SELECT s.* FROM cold_summaries s
               JOIN cold_fts f ON s.rowid = f.rowid
               WHERE cold_fts MATCH ?
               ORDER BY s.window_start DESC
               LIMIT ?""",
            (query, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cold_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_start REAL NOT NULL,
                window_end REAL NOT NULL,
                summary_json TEXT NOT NULL,
                text_content TEXT DEFAULT '',
                event_count INTEGER DEFAULT 0,
                modalities TEXT DEFAULT '[]',
                created_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
                UNIQUE(window_start)
            );

            CREATE INDEX IF NOT EXISTS idx_cold_time
                ON cold_summaries(window_start DESC);

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
        """)
```

### Tier Migration Schedule

| Transition | Trigger | Frequency | Action |
|-----------|---------|-----------|--------|
| Hot -> Warm | Age > 30s in hot buffer | Every 5 seconds | Batch INSERT to SQLite |
| Warm -> Cold | Age > 24 hours | Nightly at 3 AM | Summarize + DELETE from warm |
| Cold pruning | Age > retention setting | Nightly at 3 AM | DELETE old summaries |

Events remain queryable in hot tier for instant access, flow to warm tier for indexed persistence, and eventually compress into cold tier for long-term search. The hot-to-warm flush runs on a 5-second cycle (not 30-second) to ensure events are durable quickly — the 30-second threshold in `drain_for_warm` ensures we do not flush events that are still actively being correlated.

---

## 5. Temporal Correlation Engine

### Purpose

The correlation engine is the bridge between isolated events and cross-modal understanding. It runs continuously, examining recent events and linking them into correlation groups when they share temporal proximity, application context, or semantic content.

### Correlation Algorithm

```python
import json
import time
import threading
from dataclasses import dataclass


@dataclass
class CorrelationConfig:
    time_window_seconds: float = 5.0       # Events within 5s may correlate
    app_match_bonus: float = 0.3           # Same app increases correlation score
    text_overlap_threshold: float = 0.2    # Min Jaccard similarity for text match
    min_correlation_score: float = 0.4     # Below this, events are not linked
    cycle_interval: float = 5.0            # Seconds between correlation passes


class TemporalCorrelationEngine:
    """Links events across modalities by time, app context, and content.

    Runs as a background thread. Each cycle examines uncorrelated events
    in the hot tier and attempts to group them.
    """

    def __init__(
        self,
        hot_tier: HotTier,
        warm_tier: WarmTier,
        config: CorrelationConfig | None = None,
    ):
        self.hot = hot_tier
        self.warm = warm_tier
        self.config = config or CorrelationConfig()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._correlation_loop, daemon=True, name="correlation-engine"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _correlation_loop(self) -> None:
        while self._running:
            try:
                self._run_correlation_pass()
            except Exception:
                pass  # Log and continue; never crash the engine
            time.sleep(self.config.cycle_interval)

    def _run_correlation_pass(self) -> None:
        """Examine recent uncorrelated events and group them."""
        # Get events from hot tier that lack correlation IDs
        recent = self.hot.get_recent(
            seconds=self.config.time_window_seconds * 3, limit=200
        )
        uncorrelated = [e for e in recent if e.correlation_id is None]

        if len(uncorrelated) < 2:
            return

        # Group events that should be correlated
        groups: list[list[ContextEvent]] = []
        assigned: set[str] = set()

        for i, event_a in enumerate(uncorrelated):
            if event_a.event_id in assigned:
                continue
            group = [event_a]
            assigned.add(event_a.event_id)

            for event_b in uncorrelated[i + 1:]:
                if event_b.event_id in assigned:
                    continue
                if event_a.modality == event_b.modality:
                    continue  # Only correlate ACROSS modalities

                score = self._correlation_score(event_a, event_b)
                if score >= self.config.min_correlation_score:
                    group.append(event_b)
                    assigned.add(event_b.event_id)

            if len(group) >= 2:
                groups.append(group)

        # Assign correlation IDs
        for group in groups:
            corr_id = uuid.uuid4().hex[:12]
            modalities = sorted(set(e.modality.value for e in group))
            for event in group:
                # Since ContextEvent is frozen, we update via warm tier
                # (correlation_id is set when persisted)
                object.__setattr__(event, "correlation_id", corr_id)

            # Record the correlation group
            timestamps = [e.timestamp for e in group]
            self.warm._conn.execute(
                """INSERT OR IGNORE INTO correlations
                   (correlation_id, timestamp_start, timestamp_end,
                    modalities, event_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (corr_id, min(timestamps), max(timestamps),
                 json.dumps(modalities), len(group)),
            )
        if groups:
            self.warm._conn.commit()

    def _correlation_score(self, a: ContextEvent, b: ContextEvent) -> float:
        """Compute correlation likelihood between two events. Range 0.0 - 1.0."""
        score = 0.0

        # Temporal proximity (closer = higher score)
        time_delta = abs(a.timestamp - b.timestamp)
        if time_delta > self.config.time_window_seconds:
            return 0.0
        temporal_score = 1.0 - (time_delta / self.config.time_window_seconds)
        score += temporal_score * 0.5  # 50% weight on time

        # Application context match
        if a.app_name and a.app_name == b.app_name:
            score += self.config.app_match_bonus

        # Text content overlap (if both have text)
        text_a = self._extract_text(a)
        text_b = self._extract_text(b)
        if text_a and text_b:
            overlap = self._jaccard_similarity(text_a, text_b)
            if overlap >= self.config.text_overlap_threshold:
                score += overlap * 0.2  # 20% weight on content

        return min(score, 1.0)

    @staticmethod
    def _extract_text(event: ContextEvent) -> str:
        """Pull searchable text from event payload."""
        for key in ("ocr_text", "transcript", "burst_text", "text"):
            if key in event.payload and event.payload[key]:
                return str(event.payload[key])
        return ""

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        """Word-level Jaccard similarity."""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)
```

### Correlation Examples

| Event A | Event B | Time Delta | Score | Linked? |
|---------|---------|-----------|-------|---------|
| Screen capture (VS Code, Python file) | Keystroke burst ("def foo") | 1.2s | 0.72 | Yes |
| Voice transcription ("let's refactor the parser") | Screen capture (parser.py visible) | 3.1s | 0.61 | Yes |
| Mouse click on "Run" button | Screen capture showing test output | 0.8s | 0.78 | Yes |
| Clipboard copy (from browser) | Screen capture (VS Code) | 8.0s | 0.0 | No (>5s) |
| Voice transcription (meeting call) | Keystroke burst (Slack message) | 2.5s | 0.45 | Yes |

---

## 6. Learning Engine

### Design Philosophy

The learning engine is not Phase 5. It is Phase 0. It runs from the moment the first modality emits events, learning whatever it can from whatever modalities are active. With only Sight, it learns app usage patterns and screen content focus areas. Add Voice, and it immediately detects corrections and builds vocabulary. Add Keys, and correction pair detection becomes precise. Add Flow, and attention scoring enriches everything.

The engine degrades gracefully: fewer modalities means fewer learning signals, but never zero learning.

### 6.1 Correction Pair Detection

Correction pairs are the highest-value learning signal. They occur when the user corrects AI-generated or transcribed content, revealing the gap between machine output and user intent.

```python
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class CorrectionPair:
    source_event_id: str
    target_event_id: str
    source_modality: str
    target_modality: str
    original_text: str
    corrected_text: str
    similarity: float
    time_delta: float


class CorrectionDetector:
    """Detects correction pairs across modality combinations.

    Supported pair types (expands as modalities come online):
    - voice -> keys:  User types correction after speech transcription
    - sight -> keys:  User types correction after OCR misread
    - voice -> voice: User verbally corrects a previous statement
    - keys -> keys:   User deletes and retypes (backspace pattern)
    """

    # Detection windows per pair type (seconds)
    WINDOWS = {
        ("voice", "keys"): 15.0,    # Typing correction within 15s of transcript
        ("sight", "keys"): 30.0,    # OCR correction (may take longer to notice)
        ("voice", "voice"): 10.0,   # Verbal self-correction
    }

    # Similarity bounds: must be related (>0.3) but different (<0.92)
    SIM_LOW = 0.3
    SIM_HIGH = 0.92

    def detect(
        self, source: ContextEvent, candidates: list[ContextEvent]
    ) -> list[CorrectionPair]:
        """Check if any candidate events are corrections of the source event."""
        source_text = self._get_text(source)
        if not source_text or len(source_text) < 3:
            return []

        pairs = []
        for candidate in candidates:
            if candidate.modality == source.modality and \
               candidate.event_type == source.event_type:
                continue  # Same event type — not a cross-modal correction

            pair_key = (source.modality.value, candidate.modality.value)
            window = self.WINDOWS.get(pair_key)
            if window is None:
                continue

            time_delta = candidate.timestamp - source.timestamp
            if time_delta < 0 or time_delta > window:
                continue

            candidate_text = self._get_text(candidate)
            if not candidate_text or len(candidate_text) < 3:
                continue

            similarity = SequenceMatcher(
                None, source_text.lower(), candidate_text.lower()
            ).ratio()

            if self.SIM_LOW < similarity < self.SIM_HIGH:
                pairs.append(CorrectionPair(
                    source_event_id=source.event_id,
                    target_event_id=candidate.event_id,
                    source_modality=source.modality.value,
                    target_modality=candidate.modality.value,
                    original_text=source_text,
                    corrected_text=candidate_text,
                    similarity=similarity,
                    time_delta=time_delta,
                ))
        return pairs

    @staticmethod
    def _get_text(event: ContextEvent) -> str:
        for key in ("transcript", "burst_text", "ocr_text", "text"):
            if key in event.payload and event.payload[key]:
                return str(event.payload[key])
        return ""
```

### 6.2 Vocabulary Model

The vocabulary model is a first-class citizen from day one. It accumulates domain-specific terms, corrections, and user preferences. Even with only Sight active, frequently-appearing OCR terms build a domain vocabulary. When Voice arrives, that vocabulary immediately biases Whisper transcriptions.

```python
class VocabularyModel:
    """SQLite-backed vocabulary model. No neural weights.
    User-inspectable and deletable from settings.

    Confidence lifecycle:
    - Initial: 0.5
    - Each reinforcement: +0.1 (cap 0.95)
    - Conflicting correction: -0.2 (floor 0.1)
    - Apply threshold: 0.7
    - Expire: 30 days without observation
    """

    INITIAL_CONFIDENCE = 0.5
    REINFORCEMENT_DELTA = 0.1
    CONFLICT_PENALTY = 0.2
    APPLY_THRESHOLD = 0.7
    EXPIRY_DAYS = 30
    MAX_CONFIDENCE = 0.95
    MIN_CONFIDENCE = 0.1

    def __init__(self, db_conn: sqlite3.Connection):
        self._conn = db_conn

    def update(self, original: str, corrected: str) -> None:
        """Record a correction pair in the vocabulary model."""
        original_lower = original.strip().lower()
        corrected_clean = corrected.strip()
        now = time.time()

        existing = self._conn.execute(
            "SELECT id, correction, confidence, occurrence_count FROM vocabulary WHERE token = ?",
            (original_lower,),
        ).fetchone()

        if existing is None:
            self._conn.execute(
                """INSERT INTO vocabulary (token, correction, confidence, occurrence_count,
                   source, last_seen) VALUES (?, ?, ?, 1, 'correction', ?)""",
                (original_lower, corrected_clean, self.INITIAL_CONFIDENCE, now),
            )
        elif existing["correction"].lower() == corrected_clean.lower():
            # Reinforcement: same correction seen again
            new_conf = min(
                existing["confidence"] + self.REINFORCEMENT_DELTA,
                self.MAX_CONFIDENCE,
            )
            self._conn.execute(
                """UPDATE vocabulary SET confidence = ?, occurrence_count = occurrence_count + 1,
                   last_seen = ? WHERE id = ?""",
                (new_conf, now, existing["id"]),
            )
        else:
            # Conflict: different correction for same token
            new_conf = max(
                existing["confidence"] - self.CONFLICT_PENALTY,
                self.MIN_CONFIDENCE,
            )
            self._conn.execute(
                """UPDATE vocabulary SET confidence = ?, correction = ?,
                   occurrence_count = occurrence_count + 1, last_seen = ? WHERE id = ?""",
                (new_conf, corrected_clean, now, existing["id"]),
            )
        self._conn.commit()

    def get_corrections(self, text: str) -> str:
        """Apply high-confidence corrections to input text."""
        words = text.split()
        corrected = []
        for word in words:
            row = self._conn.execute(
                "SELECT correction FROM vocabulary WHERE token = ? AND confidence >= ?",
                (word.lower(), self.APPLY_THRESHOLD),
            ).fetchone()
            corrected.append(row["correction"] if row else word)
        return " ".join(corrected)

    def get_whisper_hotwords(self, limit: int = 50) -> list[str]:
        """Return high-confidence tokens for Whisper initial_prompt injection."""
        cursor = self._conn.execute(
            """SELECT correction FROM vocabulary
               WHERE confidence >= ? AND source IN ('correction', 'frequency')
               ORDER BY confidence DESC, occurrence_count DESC
               LIMIT ?""",
            (self.APPLY_THRESHOLD, limit),
        )
        return [row["correction"] for row in cursor.fetchall()]

    def get_domain_terms(self, min_occurrences: int = 3) -> list[dict]:
        """Return frequently observed domain-specific terms."""
        cursor = self._conn.execute(
            """SELECT token, correction, confidence, occurrence_count, last_seen
               FROM vocabulary
               WHERE occurrence_count >= ?
               ORDER BY occurrence_count DESC""",
            (min_occurrences,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def expire_stale(self) -> int:
        """Remove entries not seen in EXPIRY_DAYS. Returns count removed."""
        cutoff = time.time() - (self.EXPIRY_DAYS * 86400)
        cursor = self._conn.execute(
            "DELETE FROM vocabulary WHERE last_seen < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    def export_json(self) -> list[dict]:
        """Export full vocabulary for user inspection or backup."""
        cursor = self._conn.execute(
            "SELECT token, correction, confidence, occurrence_count, last_seen FROM vocabulary ORDER BY token"
        )
        return [dict(row) for row in cursor.fetchall()]
```

### 6.3 Attention Scoring

Attention scoring determines how important a given event is, based on signals from whatever modalities are active. Higher attention scores mean the user was actively engaged with that content. MCP queries use attention scores to rank context relevance.

```python
class AttentionScorer:
    """Computes attention scores for events based on available modality signals.

    Degrades gracefully: with only Sight, uses screen change rate and app
    focus duration. Each additional modality adds signal strength.

    Score range: 0.0 (no attention) to 1.0 (peak focus).
    """

    # Weight per signal (normalized to available signals)
    SIGNAL_WEIGHTS = {
        "screen_active": 0.15,       # Screen is changing (not idle)
        "app_foreground": 0.10,      # Event's app is foreground
        "pointer_dwell": 0.25,       # Mouse hovering/clicking in area
        "typing_active": 0.25,       # User is typing in this app
        "speech_about": 0.15,        # Voice mentions content on screen
        "clipboard_from": 0.10,      # User copied from this context
    }

    def score_event(
        self,
        event: ContextEvent,
        context_window: list[ContextEvent],
    ) -> float:
        """Score an event's attention level given surrounding context."""
        signals: dict[str, float] = {}
        available_weight = 0.0

        # Screen active signal (always available if Sight is running)
        sight_events = [
            e for e in context_window
            if e.modality == Modality.SIGHT
        ]
        if sight_events:
            # If there are screen changes near this event, screen is active
            nearby_changes = [
                e for e in sight_events
                if abs(e.timestamp - event.timestamp) < 10
                and e.payload.get("diff_score", 0) > 0.015
            ]
            signals["screen_active"] = min(len(nearby_changes) / 3.0, 1.0)
            available_weight += self.SIGNAL_WEIGHTS["screen_active"]

        # App foreground signal
        focus_events = [
            e for e in context_window
            if e.event_type == EventType.WINDOW_FOCUS
            and e.app_name == event.app_name
            and abs(e.timestamp - event.timestamp) < 30
        ]
        if focus_events:
            signals["app_foreground"] = 1.0
            available_weight += self.SIGNAL_WEIGHTS["app_foreground"]

        # Pointer dwell signal (requires Flow)
        flow_events = [
            e for e in context_window
            if e.modality == Modality.FLOW
            and abs(e.timestamp - event.timestamp) < 15
        ]
        if flow_events:
            dwell_total = sum(
                e.payload.get("dwell_duration", 0) or 0 for e in flow_events
            )
            signals["pointer_dwell"] = min(dwell_total / 5.0, 1.0)
            available_weight += self.SIGNAL_WEIGHTS["pointer_dwell"]

        # Typing active signal (requires Keys)
        keys_events = [
            e for e in context_window
            if e.modality == Modality.KEYS
            and e.app_name == event.app_name
            and abs(e.timestamp - event.timestamp) < 15
        ]
        if keys_events:
            signals["typing_active"] = min(len(keys_events) / 10.0, 1.0)
            available_weight += self.SIGNAL_WEIGHTS["typing_active"]

        # Speech about signal (requires Voice)
        voice_events = [
            e for e in context_window
            if e.modality == Modality.VOICE
            and abs(e.timestamp - event.timestamp) < 10
        ]
        if voice_events:
            event_text = self._get_text(event)
            if event_text:
                max_overlap = 0.0
                for ve in voice_events:
                    transcript = ve.payload.get("transcript", "")
                    if transcript:
                        overlap = self._word_overlap(event_text, transcript)
                        max_overlap = max(max_overlap, overlap)
                signals["speech_about"] = max_overlap
                available_weight += self.SIGNAL_WEIGHTS["speech_about"]

        # Clipboard signal
        clip_events = [
            e for e in context_window
            if e.modality == Modality.CLIPBOARD
            and e.app_name == event.app_name
            and abs(e.timestamp - event.timestamp) < 30
        ]
        if clip_events:
            signals["clipboard_from"] = 1.0
            available_weight += self.SIGNAL_WEIGHTS["clipboard_from"]

        # Normalize score to available signals
        if available_weight == 0:
            return 0.0

        raw_score = sum(
            signals.get(name, 0.0) * weight
            for name, weight in self.SIGNAL_WEIGHTS.items()
            if name in signals
        )
        return raw_score / available_weight

    @staticmethod
    def _get_text(event: ContextEvent) -> str:
        for key in ("ocr_text", "transcript", "burst_text", "text"):
            if key in event.payload and event.payload[key]:
                return str(event.payload[key])
        return ""

    @staticmethod
    def _word_overlap(text_a: str, text_b: str) -> float:
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / max(len(words_a), len(words_b))
```

### 6.4 Cognitive Load Estimation

Cognitive load estimation combines signals from available modalities to produce a 0-100 score indicating user mental load. The estimator works with as few as one modality and improves with each addition.

```python
@dataclass
class CognitiveLoadEstimate:
    score: float                    # 0-100
    confidence: float               # 0-1 (higher with more modalities)
    signals: dict[str, float]       # Individual signal contributions
    modalities_used: list[str]
    timestamp: float


class CognitiveLoadEstimator:
    """Multi-signal cognitive load estimation.

    Signals and their interpretation:
    - Typing WPM slope:      Declining WPM = increasing load (40% weight)
    - Pointer efficiency:    Erratic movement = high load (30% weight)
    - Screen switch rate:    Rapid app switching = high load (20% weight)
    - Speech hesitation:     Long pauses in speech = high load (10% weight)

    Each signal is independently normalized to 0-100.
    Final score is weighted average of available signals.
    Confidence = fraction of total weight covered by available signals.
    """

    SIGNAL_DEFS = {
        "typing_wpm_slope": {
            "weight": 0.40,
            "requires": Modality.KEYS,
        },
        "pointer_efficiency": {
            "weight": 0.30,
            "requires": Modality.FLOW,
        },
        "screen_switch_rate": {
            "weight": 0.20,
            "requires": Modality.SIGHT,
        },
        "speech_hesitation": {
            "weight": 0.10,
            "requires": Modality.VOICE,
        },
    }

    def estimate(
        self,
        events: list[ContextEvent],
        window_seconds: float = 300.0,
    ) -> CognitiveLoadEstimate:
        """Estimate cognitive load from recent events."""
        now = time.time()
        cutoff = now - window_seconds
        recent = [e for e in events if e.timestamp >= cutoff]

        signals: dict[str, float] = {}
        total_weight = 0.0
        used_weight = 0.0
        modalities_used = []

        for sig_name, sig_def in self.SIGNAL_DEFS.items():
            total_weight += sig_def["weight"]
            modality = sig_def["requires"]
            mod_events = [e for e in recent if e.modality == modality]

            if not mod_events:
                continue

            modalities_used.append(modality.value)

            if sig_name == "typing_wpm_slope":
                score = self._calc_wpm_slope(mod_events)
            elif sig_name == "pointer_efficiency":
                score = self._calc_pointer_efficiency(mod_events)
            elif sig_name == "screen_switch_rate":
                score = self._calc_switch_rate(mod_events)
            elif sig_name == "speech_hesitation":
                score = self._calc_speech_hesitation(mod_events)
            else:
                continue

            if score is not None:
                signals[sig_name] = score
                used_weight += sig_def["weight"]

        if used_weight == 0:
            return CognitiveLoadEstimate(
                score=0.0, confidence=0.0, signals={},
                modalities_used=[], timestamp=now,
            )

        weighted_sum = sum(
            signals[name] * self.SIGNAL_DEFS[name]["weight"]
            for name in signals
        )
        final_score = weighted_sum / used_weight  # Normalize to available
        confidence = used_weight / total_weight

        return CognitiveLoadEstimate(
            score=round(final_score, 1),
            confidence=round(confidence, 2),
            signals=signals,
            modalities_used=sorted(set(modalities_used)),
            timestamp=now,
        )

    def _calc_wpm_slope(self, events: list[ContextEvent]) -> float | None:
        """Declining WPM = high load. Returns 0-100."""
        wpm_values = [
            (e.timestamp, e.payload.get("wpm_snapshot", 0))
            for e in events
            if "wpm_snapshot" in e.payload and e.payload["wpm_snapshot"]
        ]
        if len(wpm_values) < 5:
            return None

        # Simple linear regression on WPM over time
        n = len(wpm_values)
        sum_x = sum(t for t, _ in wpm_values)
        sum_y = sum(w for _, w in wpm_values)
        sum_xy = sum(t * w for t, w in wpm_values)
        sum_xx = sum(t * t for t, _ in wpm_values)

        denom = n * sum_xx - sum_x * sum_x
        if denom == 0:
            return 50.0

        slope = (n * sum_xy - sum_x * sum_y) / denom

        # Normalize: slope of -2 WPM/min or worse = 100 load
        # slope of +1 WPM/min or better = 0 load
        slope_per_min = slope * 60
        load = max(0, min(100, 50 - slope_per_min * 25))
        return load

    def _calc_pointer_efficiency(self, events: list[ContextEvent]) -> float | None:
        """Low efficiency ratio = high load. Returns 0-100."""
        ratios = [
            e.payload.get("efficiency_ratio")
            for e in events
            if e.payload.get("efficiency_ratio") is not None
        ]
        if len(ratios) < 3:
            return None
        avg_ratio = sum(ratios) / len(ratios)
        # Efficiency 1.0 = perfect = 0 load; efficiency 0.3 = erratic = 100 load
        load = max(0, min(100, (1.0 - avg_ratio) * 143))
        return load

    def _calc_switch_rate(self, events: list[ContextEvent]) -> float | None:
        """Rapid app switching = high load. Returns 0-100."""
        focus_events = [
            e for e in events if e.event_type == EventType.WINDOW_FOCUS
        ]
        if len(focus_events) < 2:
            # Use screen captures: unique app names in window
            apps = set(e.app_name for e in events if e.app_name)
            if len(apps) <= 1:
                return 0.0
            # Rough: >8 unique apps in 5 min = high load
            return min(100, len(apps) * 12.5)

        # Switches per minute
        time_span = focus_events[-1].timestamp - focus_events[0].timestamp
        if time_span <= 0:
            return 50.0
        switches_per_min = (len(focus_events) - 1) / (time_span / 60)
        # 0-2 switches/min = low; 10+ = high
        load = max(0, min(100, switches_per_min * 10))
        return load

    def _calc_speech_hesitation(self, events: list[ContextEvent]) -> float | None:
        """Long pauses between speech segments = high load. Returns 0-100."""
        speech_events = sorted(
            [e for e in events if e.event_type == EventType.TRANSCRIPTION],
            key=lambda e: e.timestamp,
        )
        if len(speech_events) < 2:
            return None

        gaps = []
        for i in range(1, len(speech_events)):
            prev_end = speech_events[i - 1].timestamp + \
                speech_events[i - 1].payload.get("duration_seconds", 0)
            gap = speech_events[i].timestamp - prev_end
            if 0 < gap < 30:  # Ignore gaps > 30s (probably stopped talking)
                gaps.append(gap)

        if not gaps:
            return None

        avg_gap = sum(gaps) / len(gaps)
        # Avg gap 0-1s = low load; 5s+ = high load
        load = max(0, min(100, avg_gap * 20))
        return load
```

### 6.5 Behavior Pattern Recognition

The learning engine continuously builds a model of user behavior patterns. These patterns inform context ranking, proactive suggestions, and cognitive load baselines.

```python
class BehaviorTracker:
    """Tracks and stores long-term user behavior patterns.

    Pattern types:
    - app_usage:     Hours spent per app per day
    - peak_hours:    When the user is most active
    - focus_apps:    Apps that correlate with high attention scores
    - fatigue_curve: Typical cognitive load progression through the day
    - correction_rate: How often corrections happen per modality pair
    """

    def __init__(self, db_conn: sqlite3.Connection):
        self._conn = db_conn

    def update_app_usage(self, app_name: str, duration_seconds: float) -> None:
        """Accumulate app usage time."""
        today = time.strftime("%Y-%m-%d")
        self._conn.execute(
            """INSERT INTO behavior_patterns (pattern_type, pattern_key, pattern_value,
               sample_count, last_updated)
               VALUES ('app_usage', ?, ?, 1, ?)
               ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                 pattern_value = json_set(
                     pattern_value,
                     '$.' || ?,
                     COALESCE(json_extract(pattern_value, '$.' || ?), 0) + ?
                 ),
                 sample_count = sample_count + 1,
                 last_updated = ?""",
            (app_name, json.dumps({today: duration_seconds}), time.time(),
             today, today, duration_seconds, time.time()),
        )
        self._conn.commit()

    def update_peak_hours(self, hour: int, event_count: int) -> None:
        """Track activity level per hour of day."""
        key = f"hour_{hour:02d}"
        self._conn.execute(
            """INSERT INTO behavior_patterns (pattern_type, pattern_key, pattern_value,
               sample_count, last_updated)
               VALUES ('peak_hours', ?, ?, 1, ?)
               ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                 pattern_value = CAST(
                     (CAST(pattern_value AS REAL) * sample_count + ?) / (sample_count + 1)
                     AS TEXT
                 ),
                 sample_count = sample_count + 1,
                 last_updated = ?""",
            (key, str(float(event_count)), time.time(),
             float(event_count), time.time()),
        )
        self._conn.commit()

    def get_user_model(self) -> dict:
        """Return the full user behavior model for MCP consumption."""
        cursor = self._conn.execute(
            "SELECT pattern_type, pattern_key, pattern_value, sample_count, last_updated "
            "FROM behavior_patterns ORDER BY pattern_type, pattern_key"
        )
        model: dict[str, dict] = {}
        for row in cursor.fetchall():
            ptype = row[0]
            model.setdefault(ptype, {})[row[1]] = {
                "value": row[2],
                "samples": row[3],
                "last_updated": row[4],
            }
        return model
```

### 6.6 Learning Engine Orchestrator

```python
class LearningEngine:
    """Orchestrates all learning subsystems.

    Runs as a background thread on a 60-second cycle. Each cycle:
    1. Detects correction pairs from recent events
    2. Updates vocabulary model
    3. Recomputes attention scores for recent events
    4. Updates cognitive load estimate
    5. Updates behavior patterns
    """

    def __init__(
        self,
        hot_tier: HotTier,
        warm_tier: WarmTier,
        correction_detector: CorrectionDetector,
        vocabulary: VocabularyModel,
        attention_scorer: AttentionScorer,
        cognitive_estimator: CognitiveLoadEstimator,
        behavior_tracker: BehaviorTracker,
    ):
        self.hot = hot_tier
        self.warm = warm_tier
        self.corrections = correction_detector
        self.vocabulary = vocabulary
        self.attention = attention_scorer
        self.cognitive = cognitive_estimator
        self.behavior = behavior_tracker
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_cycle: float = 0.0
        self._latest_cognitive_load: CognitiveLoadEstimate | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._learning_loop, daemon=True, name="learning-engine"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)

    def _learning_loop(self) -> None:
        while self._running:
            try:
                self._run_learning_cycle()
            except Exception:
                pass  # Log and continue
            time.sleep(60.0)

    def _run_learning_cycle(self) -> None:
        now = time.time()
        window_start = max(self._last_cycle, now - 120)  # Last 2 min or since last cycle

        # Get recent events from warm tier
        recent_events_raw = self.warm.query_time_range(window_start, now, limit=500)
        if not recent_events_raw:
            self._last_cycle = now
            return

        # Reconstruct ContextEvent objects for processing
        recent_events = [self._row_to_event(row) for row in recent_events_raw]

        # 1. Correction detection
        text_events = [
            e for e in recent_events
            if any(k in e.payload for k in ("transcript", "ocr_text"))
        ]
        for source in text_events:
            # Look for corrections in subsequent events
            candidates = [
                e for e in recent_events
                if e.timestamp > source.timestamp
                and e.modality != source.modality
            ]
            pairs = self.corrections.detect(source, candidates)
            for pair in pairs:
                self.vocabulary.update(pair.original_text, pair.corrected_text)
                self.warm._conn.execute(
                    """INSERT OR IGNORE INTO correction_pairs
                       (timestamp, source_modality, target_modality,
                        source_event_id, target_event_id,
                        original_text, corrected_text, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (now, pair.source_modality, pair.target_modality,
                     pair.source_event_id, pair.target_event_id,
                     pair.original_text, pair.corrected_text, pair.similarity),
                )

        # 2. Attention scoring (batch update)
        for event in recent_events:
            context = [e for e in recent_events if abs(e.timestamp - event.timestamp) < 30]
            score = self.attention.score_event(event, context)
            if score > 0:
                self.warm._conn.execute(
                    "UPDATE events SET attention_score = ? WHERE event_id = ?",
                    (score, event.event_id),
                )

        # 3. Cognitive load
        self._latest_cognitive_load = self.cognitive.estimate(recent_events)

        # 4. Behavior patterns
        hour = time.localtime(now).tm_hour
        self.behavior.update_peak_hours(hour, len(recent_events))

        app_durations: dict[str, float] = {}
        for event in recent_events:
            if event.app_name:
                app_durations[event.app_name] = app_durations.get(event.app_name, 0) + 1
        for app, count in app_durations.items():
            self.behavior.update_app_usage(app, count * 5.0)  # Rough estimate

        # 5. Vocabulary maintenance
        self.vocabulary.expire_stale()

        self.warm._conn.commit()
        self._last_cycle = now

    def get_cognitive_load(self) -> CognitiveLoadEstimate | None:
        return self._latest_cognitive_load

    def get_learning_stats(self) -> dict:
        """Return summary of what the engine has learned."""
        vocab_count = self.warm._conn.execute(
            "SELECT COUNT(*) FROM vocabulary"
        ).fetchone()[0]
        high_conf_count = self.warm._conn.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE confidence >= ?",
            (VocabularyModel.APPLY_THRESHOLD,),
        ).fetchone()[0]
        correction_count = self.warm._conn.execute(
            "SELECT COUNT(*) FROM correction_pairs"
        ).fetchone()[0]
        pattern_count = self.warm._conn.execute(
            "SELECT COUNT(*) FROM behavior_patterns"
        ).fetchone()[0]

        return {
            "vocabulary_terms": vocab_count,
            "high_confidence_terms": high_conf_count,
            "correction_pairs_detected": correction_count,
            "behavior_patterns": pattern_count,
            "last_cycle": self._last_cycle,
            "cognitive_load": (
                {
                    "score": self._latest_cognitive_load.score,
                    "confidence": self._latest_cognitive_load.confidence,
                    "modalities": self._latest_cognitive_load.modalities_used,
                }
                if self._latest_cognitive_load
                else None
            ),
        }

    @staticmethod
    def _row_to_event(row: dict) -> ContextEvent:
        payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        return ContextEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            modality=Modality(row["modality"]),
            event_type=EventType(row["event_type"]),
            app_name=row.get("app_name", ""),
            window_title=row.get("window_title", ""),
            monitor_index=row.get("monitor_index", 0),
            payload=payload,
            correlation_id=row.get("correlation_id"),
            attention_score=row.get("attention_score", 0.0),
            cognitive_load=row.get("cognitive_load", 0.0),
        )
```

---

## 7. Module Interface Contract

### The Contract

Every modality module that plugs into the Central Memory Engine MUST implement the `ModalityModule` abstract base class. No exceptions. This is the enforcement mechanism that guarantees cross-modal interoperability.

```python
from abc import ABC, abstractmethod
from typing import Callable


class ModalityModule(ABC):
    """Abstract base class for all modality modules.

    Every module (Sight, Voice, Keys, Flow) MUST implement this interface
    to plug into the Central Memory Engine.

    Lifecycle:
    1. Engine calls register(callback) during startup
    2. Module calls callback(event) whenever it produces a ContextEvent
    3. Engine calls start() to begin capture
    4. Engine calls stop() to pause/shutdown
    5. Engine calls get_status() for health checks
    """

    @abstractmethod
    def get_modality(self) -> Modality:
        """Return the modality this module captures."""
        ...

    @abstractmethod
    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        """Register the callback the module will use to emit events.

        The engine provides this callback. The module MUST call it for
        every event it produces. The module MUST NOT write to the database
        directly — all persistence goes through the engine.
        """
        ...

    @abstractmethod
    def start(self) -> None:
        """Begin capturing. Called by engine after registration."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop capturing. Must be idempotent."""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """Return module health status.

        Must include at minimum:
        {
            "modality": str,
            "running": bool,
            "events_emitted": int,
            "last_event_timestamp": float | None,
            "error": str | None,
        }
        """
        ...

    @abstractmethod
    def get_config_schema(self) -> dict:
        """Return JSON schema for this module's configuration.

        Used by the settings panel to render module-specific controls.
        """
        ...
```

### Adapting Sight to the Contract

Sight is already built as a standalone daemon. Here is how it adapts to the module contract without breaking existing functionality.

```python
class SightModule(ModalityModule):
    """Adapter wrapping existing Sight capture pipeline into the
    ModalityModule interface.

    Changes from current architecture:
    1. Instead of writing directly to activity.db, emits ContextEvent
       via the registered callback.
    2. Retains its own internal buffer for frame storage (JPEG files),
       but event metadata flows through the engine.
    3. OCR worker remains internal — emits OCR_RESULT events when
       processing completes.
    """

    def __init__(self, existing_capture_engine, existing_ocr_worker):
        self._capture = existing_capture_engine
        self._ocr = existing_ocr_worker
        self._callback: Callable[[ContextEvent], None] | None = None
        self._events_emitted = 0
        self._last_event_ts: float | None = None
        self._running = False

    def get_modality(self) -> Modality:
        return Modality.SIGHT

    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        self._callback = event_callback

    def start(self) -> None:
        self._running = True
        # Hook into existing capture loop's on_frame callback
        self._capture.on_frame = self._on_frame_captured
        self._ocr.on_result = self._on_ocr_complete
        self._capture.start()
        self._ocr.start()

    def stop(self) -> None:
        self._running = False
        self._capture.stop()
        self._ocr.stop()

    def _on_frame_captured(self, frame_path: str, metadata: dict) -> None:
        """Called by existing capture engine when a new frame is captured."""
        event = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name=metadata.get("app_name", ""),
            window_title=metadata.get("window_title", ""),
            monitor_index=metadata.get("monitor_index", 0),
            payload={
                "frame_path": frame_path,
                "diff_score": metadata.get("diff_score", 0.0),
                "token_estimate": metadata.get("token_estimate", 0),
                "storage_mode": metadata.get("storage_mode", "image"),
            },
        )
        if self._callback:
            self._callback(event)
            self._events_emitted += 1
            self._last_event_ts = event.timestamp

    def _on_ocr_complete(self, frame_event_id: str, ocr_text: str, confidence: float) -> None:
        """Called when OCR worker finishes processing a frame."""
        event = ContextEvent(
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            payload={
                "source_event_id": frame_event_id,
                "ocr_text": ocr_text,
                "ocr_confidence": confidence,
            },
        )
        if self._callback:
            self._callback(event)
            self._events_emitted += 1
            self._last_event_ts = event.timestamp

    def get_status(self) -> dict:
        return {
            "modality": "sight",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_event_ts,
            "error": None,
        }

    def get_config_schema(self) -> dict:
        return {
            "capture_interval_seconds": {"type": "number", "default": 5.0, "min": 1.0, "max": 60.0},
            "buffer_max_frames": {"type": "integer", "default": 10, "min": 5, "max": 50},
            "diff_threshold": {"type": "number", "default": 0.015, "min": 0.005, "max": 0.1},
            "ocr_enabled": {"type": "boolean", "default": True},
            "monitors": {"type": "string", "default": "all", "enum": ["all", "active", "primary"]},
        }
```

### How Future Modules Plug In

Every future module follows the exact same pattern:

```python
# Voice module (Phase 2) — skeleton
class VoiceModule(ModalityModule):
    def get_modality(self) -> Modality:
        return Modality.VOICE

    def register(self, event_callback):
        self._callback = event_callback

    def start(self):
        # Start VAD listener, Whisper pipeline
        # On transcription complete: emit TRANSCRIPTION event via callback
        ...

    def stop(self): ...
    def get_status(self) -> dict: ...
    def get_config_schema(self) -> dict: ...


# Keys module (Phase 3) — skeleton
class KeysModule(ModalityModule):
    def get_modality(self) -> Modality:
        return Modality.KEYS

    def register(self, event_callback):
        self._callback = event_callback

    def start(self):
        # Start pynput keyboard listener
        # On keystroke: emit KEYSTROKE event via callback
        # On burst complete: emit TYPING_BURST event via callback
        ...

    def stop(self): ...
    def get_status(self) -> dict: ...
    def get_config_schema(self) -> dict: ...


# Flow module (Phase 4) — skeleton
class FlowModule(ModalityModule):
    def get_modality(self) -> Modality:
        return Modality.FLOW

    def register(self, event_callback):
        self._callback = event_callback

    def start(self):
        # Start pynput mouse listener
        # On click: emit CLICK event via callback
        # On dwell: emit HOVER_DWELL event via callback
        ...

    def stop(self): ...
    def get_status(self) -> dict: ...
    def get_config_schema(self) -> dict: ...
```

### Module Registration Flow

```
Engine startup:
    1. Initialize Central Memory Engine (hot, warm, cold tiers)
    2. Start correlation engine thread
    3. Start learning engine thread
    4. For each enabled module in config:
        a. Instantiate module
        b. Call module.register(engine.on_event)
        c. Call module.start()
    5. Start MCP server (passes query calls to engine)

engine.on_event(event):
    1. Validate event schema
    2. Push to hot tier
    3. (Hot tier flushes to warm on 5s cycle)
    4. (Correlation engine links events on 5s cycle)
    5. (Learning engine processes on 60s cycle)
```

---

## 8. MCP Tool Design

### Cross-Modal Query Tools

The MCP tools query the Central Memory Engine, not individual module databases. This means every tool automatically returns data from whatever modalities are active, and new modalities enrich existing tools without code changes.

```python
from mcp.server.fastmcp import FastMCP

mcp_app = FastMCP("contextpulse")


@mcp_app.tool()
def get_context_at(
    minutes_ago: float = 5.0,
    modalities: list[str] | None = None,
    include_correlations: bool = True,
) -> dict:
    """Get all context from all active modalities around a point in time.

    Returns screen captures, voice transcriptions, typing activity,
    and pointer interactions that occurred within a window around the
    specified time. Events are grouped by correlation ID when available.

    Args:
        minutes_ago: How many minutes back to look (default 5)
        modalities: Filter to specific modalities (default: all active)
        include_correlations: Group events by correlation (default True)
    """
    target_time = time.time() - (minutes_ago * 60)
    window = 30.0  # +-30 seconds around target

    # Try hot tier first for recent events
    if minutes_ago < 5:
        events = engine.hot_tier.get_at_timestamp(target_time, window)
    else:
        events = engine.warm_tier.query_time_range(
            target_time - window, target_time + window,
            modalities=modalities, limit=100,
        )

    if include_correlations and events:
        # Group by correlation_id
        groups: dict[str | None, list] = {}
        for event in events:
            cid = event.get("correlation_id") if isinstance(event, dict) else event.correlation_id
            groups.setdefault(cid, []).append(event)
        return {"correlated_groups": groups, "total_events": len(events)}

    return {"events": events, "total_events": len(events)}


@mcp_app.tool()
def search_memory(
    query: str,
    modalities: list[str] | None = None,
    hours_back: float = 24.0,
    limit: int = 20,
) -> dict:
    """Search across all modality data — screen text, voice transcripts,
    typed content, clipboard — using full-text search.

    The search spans both warm tier (recent 24h, full events) and cold
    tier (30+ days, summarized). Results are ranked by relevance and
    annotated with attention scores when available.

    Args:
        query: Search terms (supports FTS5 query syntax)
        modalities: Filter to specific modalities (default: all)
        hours_back: How far back to search (default 24h, max unlimited for cold)
        limit: Maximum results to return
    """
    results = []

    # Search warm tier
    warm_results = engine.warm_tier.search_text(query, limit=limit)
    if modalities:
        warm_results = [r for r in warm_results if r["modality"] in modalities]
    results.extend(warm_results)

    # Search cold tier if looking beyond 24h
    if hours_back > 24:
        cold_results = engine.cold_tier.search(query, limit=limit)
        results.extend(cold_results)

    # Sort by attention score (descending), then recency
    results.sort(
        key=lambda r: (r.get("attention_score", 0), r.get("timestamp", 0)),
        reverse=True,
    )

    return {
        "results": results[:limit],
        "total_found": len(results),
        "tiers_searched": ["warm"] + (["cold"] if hours_back > 24 else []),
    }


@mcp_app.tool()
def get_user_model() -> dict:
    """Get the engine's current understanding of user state and patterns.

    Returns:
    - Cognitive load estimate (0-100 with confidence)
    - Active modalities and their status
    - App usage patterns
    - Peak productivity hours
    - Vocabulary model statistics
    - Recent correction pairs

    This tool enables AI agents to adapt their behavior based on
    user state — e.g., simplifying responses during high cognitive load.
    """
    cognitive = engine.learning.get_cognitive_load()
    stats = engine.learning.get_learning_stats()
    model = engine.behavior.get_user_model()

    module_statuses = {}
    for mod in engine.modules.values():
        module_statuses[mod.get_modality().value] = mod.get_status()

    return {
        "cognitive_load": {
            "score": cognitive.score if cognitive else None,
            "confidence": cognitive.confidence if cognitive else None,
            "signals": cognitive.signals if cognitive else {},
            "recommendation": (
                "User is under high cognitive load. Keep responses concise."
                if cognitive and cognitive.score > 70
                else "Normal cognitive load."
                if cognitive
                else "Insufficient data for cognitive load estimation."
            ),
        },
        "active_modalities": module_statuses,
        "behavior_model": model,
        "learning_stats": stats,
    }


@mcp_app.tool()
def get_learning_stats() -> dict:
    """Get statistics about what the learning engine has discovered.

    Returns vocabulary model size, correction pair count, behavior
    pattern inventory, and the learning engine's confidence in its
    models. Useful for debugging and user transparency.
    """
    stats = engine.learning.get_learning_stats()
    hotwords = engine.vocabulary.get_whisper_hotwords(limit=20)
    domain_terms = engine.vocabulary.get_domain_terms(min_occurrences=3)

    return {
        **stats,
        "whisper_hotwords": hotwords,
        "domain_terms": domain_terms[:20],
        "engine_uptime_hours": (time.time() - engine.start_time) / 3600,
    }


# === Preserved existing tools (adapted to use engine) ===

@mcp_app.tool()
def get_screenshot(monitor: str = "active") -> dict:
    """Capture current screen. Delegates to Sight module via engine."""
    sight = engine.modules.get(Modality.SIGHT)
    if not sight:
        return {"error": "Sight module not active"}
    # Trigger on-demand capture through existing Sight pipeline
    return sight.capture_on_demand(monitor=monitor)


@mcp_app.tool()
def get_recent(
    count: int = 5,
    attention_weighted: bool = False,
    modalities: list[str] | None = None,
) -> dict:
    """Get recent context events, optionally weighted by attention score.

    With attention_weighted=True, returns the most-attended-to events
    rather than the most recent. This surfaces what the user was
    actually focused on rather than just what happened last.
    """
    events = engine.hot_tier.get_recent(seconds=300, limit=count * 3)

    if modalities:
        events = [e for e in events if e.modality.value in modalities]

    if attention_weighted:
        events.sort(key=lambda e: e.attention_score, reverse=True)

    return {"events": [_serialize_event(e) for e in events[:count]]}


@mcp_app.tool()
def get_buffer_status() -> dict:
    """Get engine health: tier sizes, module statuses, learning stats."""
    return {
        "hot_tier_events": len(engine.hot_tier),
        "modules": {
            name.value: mod.get_status()
            for name, mod in engine.modules.items()
        },
        "learning": engine.learning.get_learning_stats(),
        "memory_usage_mb": _get_process_memory_mb(),
    }
```

---

## 9. Data Flow Diagrams

### Flow 1: Screen Capture Event

```
User is working in VS Code, editing parser.py
    |
    v
[Sight Module] Timer fires (5s interval)
    |
    +--> mss captures monitor 0 (3ms)
    +--> Diff detection: 4.2% change from last frame (above 1.5% threshold)
    +--> Save JPEG to buffer/1711234567.89_m0.jpg
    +--> Create ContextEvent:
    |        modality: SIGHT
    |        event_type: SCREEN_CAPTURE
    |        app_name: "Code"
    |        window_title: "parser.py - VS Code"
    |        payload: {frame_path, diff_score: 0.042, storage_mode: "hybrid"}
    |
    +--> Call engine.on_event(event)
            |
            v
    [Event Bus] Validate schema -> OK
            |
            +--> Push to Hot Tier (ring buffer, <0.1ms)
            |
            +--> [5s later] Hot-to-Warm flush
            |        INSERT INTO events (SQLite WAL, <5ms)
            |        FTS trigger indexes window_title + app_name
            |
            +--> [5s later] Correlation Engine cycle
            |        Finds: Voice TRANSCRIPTION event 2.1s earlier
            |        ("let's fix the parser bug")
            |        Same app context: both reference "parser"
            |        Score: 0.67 -> LINKED
            |        Assigns correlation_id to both events
            |
            +--> [60s later] Learning Engine cycle
            |        Attention score: 0.82 (user typing in same app,
            |        voice mentioned parser, screen shows parser.py)
            |        UPDATE events SET attention_score = 0.82
            |
    [Meanwhile] OCR Worker picks up frame from queue
            |
            +--> rapidocr processes JPEG (300ms)
            +--> Redaction pipeline strips any detected PII
            +--> Emits OCR_RESULT event:
                     payload: {ocr_text: "def parse_token(...)...",
                               ocr_confidence: 0.91}
                     -> Also flows through Event Bus -> Hot -> Warm
                     -> FTS indexes ocr_text for search
```

### Flow 2: Voice Transcription with Cross-Modal Correction

```
User says: "Let's rename the context pulse config"
    |
    v
[Voice Module] VAD detects speech onset
    |
    +--> Audio buffer captures 8.2 seconds of speech
    +--> Whisper transcribes (with hotwords from vocabulary model):
    |        Raw: "Let's rename the context pulse config"
    |        Hotwords include "contextpulse" (confidence 0.85)
    |        Post-correction: "Let's rename the contextpulse config"
    |
    +--> Emits ContextEvent:
    |        modality: VOICE
    |        event_type: TRANSCRIPTION
    |        app_name: "Code" (foreground app at time of speech)
    |        payload: {transcript: "Let's rename the contextpulse config",
    |                  confidence: 0.87, duration_seconds: 8.2}
    |
    +--> engine.on_event(event)
            |
            v
    [Event Bus] -> Hot Tier -> Warm Tier (same flow as above)
            |
    [Correlation Engine]
            Finds: Screen capture 1.5s before showing config.json open
            Score: 0.71 -> LINKED (same app, temporal proximity,
            "config" appears in both OCR and transcript)
            |
    [12 seconds later] User types in terminal:
            "mv contextpulse_config.json contextpulse-config.json"
            |
    [Keys Module] Emits TYPING_BURST event:
            payload: {burst_text: "mv contextpulse_config.json contextpulse-config.json"}
            |
    [Learning Engine] Correction detection cycle:
            Source: Voice TRANSCRIPTION "contextpulse config"
            Target: Keys TYPING_BURST "contextpulse_config.json"
            Time delta: 12s (within 15s window for voice->keys)
            Similarity: 0.61 (related but different — correction!)
            -> CorrectionPair recorded
            -> Vocabulary model updated:
               "context pulse" -> "contextpulse" reinforced (+0.1 confidence)
```

### Flow 3: The Learning Loop

```
┌────────────────────────────────────────────────────────────────┐
│                    CONTINUOUS LEARNING LOOP                     │
│                                                                │
│  Events arrive ──> Correlation ──> Pattern Detection           │
│       ^                                    |                   │
│       |                                    v                   │
│       |                           Model Update                 │
│       |                           - Vocabulary                 │
│       |                           - Attention weights           │
│       |                           - Behavior patterns           │
│       |                           - Cognitive load baseline     │
│       |                                    |                   │
│       |                                    v                   │
│       |                    Improved Future Processing           │
│       |                    - Better Whisper transcriptions      │
│       |                    - Smarter context ranking            │
│       |                    - Personalized MCP responses         │
│       |                    - Proactive cognitive load alerts    │
│       |                                    |                   │
│       └────────────────────────────────────┘                   │
│                                                                │
│  Week 1: Vocabulary has 0 terms, attention scores are uniform  │
│  Week 4: 50 domain terms, attention varies 3x between events  │
│  Month 3: 200 terms, cognitive load baseline established,      │
│           Whisper accuracy up ~15% on domain vocabulary         │
│  Month 6: Behavior model predicts focus hours, fatigue onset,  │
│           AI agents adapt response complexity automatically     │
└────────────────────────────────────────────────────────────────┘
```

---

## 10. Technical Specifications

### Performance Budgets

The Central Memory Engine must be invisible to the user. It shares the process with modality modules, so its budget is a fraction of the total.

| Component | CPU Budget | RAM Budget | Disk I/O Budget |
|-----------|-----------|-----------|----------------|
| Event Bus + Hot Tier | <0.1% | 5 MB (ring buffer) | 0 (in-memory) |
| Warm Tier (SQLite writer) | <0.2% | 10 MB (cache) | <1 MB/min |
| Cold Tier (nightly batch) | <2% (burst) | 20 MB (batch) | <5 MB/run |
| Correlation Engine | <0.1% | 2 MB | <0.1 MB/min |
| Learning Engine | <0.5% | 10 MB | <0.5 MB/min |
| **Engine Total** | **<1%** | **<47 MB** | **<2 MB/min** |
| **Remaining for Modules** | **<4%** | **<253 MB** | **varies** |
| **System Total** | **<5%** | **<300 MB** | **<5 MB/min** |

### Thread Model

```
Main Thread
├── Event Bus (queue consumer)
├── MCP Server (FastMCP, stdio)
│
├── [daemon] Hot-to-Warm Flusher (5s cycle)
├── [daemon] Correlation Engine (5s cycle)
├── [daemon] Learning Engine (60s cycle)
├── [daemon] Cold Tier Migrator (daily at 3 AM)
│
├── [daemon] Sight: Capture Loop
├── [daemon] Sight: OCR Worker
├── [daemon] Voice: Audio Capture (Phase 2)
├── [daemon] Voice: Whisper Worker (Phase 2)
├── [daemon] Keys: Keyboard Listener (Phase 3)
├── [daemon] Flow: Mouse Listener (Phase 4)
```

All daemon threads use `threading.Thread(daemon=True)` — they terminate when the main thread exits. SQLite access is serialized through WAL mode with `busy_timeout=5000`.

### Engine Initialization

```python
class CentralMemoryEngine:
    """The spine of ContextPulse. Initializes all tiers, starts all
    background threads, registers modules, and serves the MCP layer.
    """

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.start_time = time.time()

        # Storage paths
        warm_db = config_dir / "memory.db"
        cold_db = config_dir / "memory_cold.db"

        # Initialize tiers
        self.hot_tier = HotTier()
        self.warm_tier = WarmTier(warm_db)
        self.cold_tier = ColdTier(cold_db)

        # Initialize subsystems
        self.correlation = TemporalCorrelationEngine(self.hot_tier, self.warm_tier)
        self.vocabulary = VocabularyModel(self.warm_tier._conn)
        self.attention = AttentionScorer()
        self.cognitive = CognitiveLoadEstimator()
        self.behavior = BehaviorTracker(self.warm_tier._conn)
        self.learning = LearningEngine(
            self.hot_tier, self.warm_tier,
            CorrectionDetector(), self.vocabulary,
            self.attention, self.cognitive, self.behavior,
        )

        # Module registry
        self.modules: dict[Modality, ModalityModule] = {}

        # Flush thread
        self._flush_thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Initialize database connections, start background threads,
        register and start all enabled modules."""
        self.warm_tier.connect()
        self.cold_tier.connect()
        self._running = True

        # Start background threads
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="hot-warm-flush"
        )
        self._flush_thread.start()
        self.correlation.start()
        self.learning.start()

        # Start registered modules
        for module in self.modules.values():
            module.start()

    def stop(self) -> None:
        """Graceful shutdown: stop modules, flush remaining events, close DB."""
        self._running = False
        for module in self.modules.values():
            module.stop()
        self.correlation.stop()
        self.learning.stop()

        # Final flush
        remaining = self.hot_tier.drain_for_warm(time.time() + 1)
        if remaining:
            self.warm_tier.persist_events(remaining)

    def register_module(self, module: ModalityModule) -> None:
        """Register a modality module with the engine."""
        modality = module.get_modality()
        module.register(self.on_event)
        self.modules[modality] = module

    def on_event(self, event: ContextEvent) -> None:
        """Central event handler. Called by all modality modules."""
        if not event.validate():
            return  # Reject invalid events silently (log in production)
        self.hot_tier.push(event)

    def _flush_loop(self) -> None:
        """Periodically move events from hot to warm tier."""
        while self._running:
            try:
                cutoff = time.time() - 30  # Keep last 30s in hot
                events = self.hot_tier.drain_for_warm(cutoff)
                if events:
                    self.warm_tier.persist_events(events)
            except Exception:
                pass
            time.sleep(5.0)
```

---

## 11. Implementation Roadmap (Revised)

### The Corrected Build Order

The original plan: Sight -> Voice -> Keys -> Flow -> Learning Engine

The corrected plan: **Memory Engine -> Sight Adapter -> Voice -> Keys -> Flow**

The learning engine is not a phase. It is the foundation that all phases build on.

### Phase 0: Central Memory Engine (4 weeks, before Voice)

**Goal:** Build the engine, adapt Sight to use it, prove the architecture.

**Week 1: Core Infrastructure**
- [ ] `ContextEvent` dataclass and `EventType`/`Modality` enums
- [ ] `HotTier` ring buffer with full test suite
- [ ] `WarmTier` SQLite WAL with unified schema
- [ ] Event Bus: validation, routing, hot-to-warm flush loop
- [ ] 30+ unit tests

**Week 2: Correlation + Cold Storage**
- [ ] `TemporalCorrelationEngine` with time+app+text scoring
- [ ] `ColdTier` with summarization and FTS5
- [ ] Nightly warm-to-cold migration job
- [ ] `correlations` table and correlation ID assignment
- [ ] 25+ tests (correlation accuracy, tier migration)

**Week 3: Learning Engine Foundation**
- [ ] `CorrectionDetector` (voice->keys, sight->keys pairs)
- [ ] `VocabularyModel` (SQLite-backed, confidence lifecycle)
- [ ] `AttentionScorer` (Sight-only signals initially)
- [ ] `CognitiveLoadEstimator` (Sight-only: screen switch rate)
- [ ] `BehaviorTracker` (app usage, peak hours)
- [ ] `LearningEngine` orchestrator with 60s cycle
- [ ] 35+ tests

**Week 4: Sight Adapter + MCP Integration**
- [ ] `SightModule` adapter wrapping existing capture pipeline
- [ ] `CentralMemoryEngine` initialization and module registration
- [ ] Adapt existing MCP tools to query engine instead of direct DB
- [ ] New MCP tools: `search_memory`, `get_user_model`, `get_learning_stats`
- [ ] Integration tests: full event flow from capture to MCP response
- [ ] Performance benchmarks: confirm <1% CPU overhead from engine

**Estimated LOC:** ~2,000 (engine) + ~300 (Sight adapter) + ~500 (tests)

### Phase 2 (Revised): Voice + Cross-Modal Learning

With the engine in place, Voice development changes significantly:

**What stays the same:**
- Port Voiceasy recorder, transcriber, model manager
- silero-vad integration
- 30s rolling buffer with 5s overlap

**What changes:**
- Voice module implements `ModalityModule` interface (not standalone daemon)
- Voice emits `ContextEvent` objects, not direct DB writes
- Whisper hotwords come from `VocabularyModel` automatically (no custom wiring)
- Correction pair detection is automatic (engine detects voice->keys pairs)
- Temporal alignment to screen frames happens via correlation engine (not manual `nearest_frame_id`)

**Time saved:** ~2 weeks (no DB schema work, no temporal alignment code, no correction detection code)

### Phase 3 (Revised): Keys

Keys implements `KeysModule(ModalityModule)`. On registration, the learning engine immediately begins:
- Detecting voice->keys correction pairs
- Detecting sight->keys correction pairs
- Computing typing-based cognitive load signals
- Building WPM-based fatigue baselines

No additional integration code needed. The engine does it all.

### Phase 4 (Revised): Flow

Flow implements `FlowModule(ModalityModule)`. The attention scorer immediately incorporates pointer dwell data into event scoring. Cognitive load estimation gains the pointer efficiency signal. Heatmap data enriches the behavior model.

### Phase 5 Eliminated

There is no Phase 5. The learning engine was Phase 0. By the time Flow ships, the engine has been running for 6+ months, accumulating vocabulary, refining attention models, and building behavior patterns. What was Phase 5 in the original plan is now just the engine getting better with more data — not a development milestone.

### Revised Dependency Graph

```
Phase 0: Central Memory Engine (4 weeks)
    |
    +--> Sight Adapter (included in Phase 0, Week 4)
    |        Engine learns: app_usage, peak_hours, screen_switch_rate
    |        Attention scoring: screen_active, app_foreground, clipboard
    |        Cognitive load: screen_switch_rate only (20% confidence)
    |
    +--> Phase 2: Voice (8 weeks, was 12)
    |        Engine gains: correction_pairs (voice->keys placeholder),
    |        vocabulary model starts building from OCR frequency terms
    |        Attention scoring adds: speech_about signal
    |        Cognitive load adds: speech_hesitation (30% confidence)
    |
    +--> Phase 3: Keys (8 weeks, was 10)
    |        Engine gains: correction_pairs fully active (voice->keys, sight->keys),
    |        vocabulary model accelerates (typing reinforces corrections)
    |        Attention scoring adds: typing_active signal
    |        Cognitive load adds: typing_wpm_slope (70% confidence)
    |
    +--> Phase 4: Flow (7 weeks, was 9)
             Engine gains: pointer dwell attention, efficiency cognitive signal
             Attention scoring adds: pointer_dwell signal
             Cognitive load adds: pointer_efficiency (100% confidence, all signals)
             Behavior model: complete (all modalities contributing)
```

### Revised Timeline

| Phase | Original Timeline | Revised Timeline | Weeks Saved |
|-------|------------------|-----------------|-------------|
| Phase 0 (Engine) | N/A | 4 weeks (before Voice) | -4 (investment) |
| Phase 2 (Voice) | 12 weeks | 8 weeks | +4 |
| Phase 3 (Keys) | 10 weeks | 8 weeks | +2 |
| Phase 4 (Flow) | 9 weeks | 7 weeks | +2 |
| Phase 5 (Learning) | 12 weeks | 0 weeks (eliminated) | +12 |
| **Total** | **43 weeks** | **27 weeks** | **+16 weeks** |

The 4-week upfront investment in the engine saves 20 weeks across subsequent phases, eliminates an entire phase, and delivers learning capabilities from month one instead of month fourteen.

### Migration Strategy for Existing Sight Data

Sight already has production data in `activity.db`. The migration path:

1. **Engine creates `memory.db`** with the new unified schema
2. **One-time migration script** reads `activity.db`, converts rows to `ContextEvent` format, writes to `events` table
3. **FTS re-index** builds `events_fts` from migrated data
4. **Sight adapter** begins writing to engine; old `activity.db` is read-only legacy
5. **After 30 days**, `activity.db` can be archived (all data is in warm/cold tiers)

```python
def migrate_sight_data(old_db: Path, engine: CentralMemoryEngine) -> int:
    """Migrate existing Sight activity records to unified event store."""
    old_conn = sqlite3.connect(str(old_db))
    old_conn.row_factory = sqlite3.Row
    cursor = old_conn.execute(
        "SELECT * FROM activity ORDER BY timestamp"
    )
    count = 0
    batch = []
    for row in cursor:
        event = ContextEvent(
            event_id=f"migrated_{row['id']}",
            timestamp=row["timestamp"],
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name=row.get("app_name", ""),
            window_title=row.get("window_title", ""),
            monitor_index=row.get("monitor_index", 0),
            payload={
                "frame_path": row.get("frame_path", ""),
                "ocr_text": row.get("ocr_text", ""),
                "diff_score": row.get("diff_score", 0.0),
                "storage_mode": "migrated",
            },
        )
        batch.append(event)
        if len(batch) >= 500:
            engine.warm_tier.persist_events(batch)
            count += len(batch)
            batch = []
    if batch:
        engine.warm_tier.persist_events(batch)
        count += len(batch)
    old_conn.close()
    return count
```

---

## Appendix A: Backward Compatibility

The existing 10 MCP tools continue to work exactly as before. The engine adapter layer translates existing tool signatures to engine queries:

| Existing Tool | Engine Query |
|--------------|-------------|
| `get_screenshot` | `SightModule.capture_on_demand()` |
| `get_recent` | `engine.hot_tier.get_recent(modality=SIGHT)` |
| `get_screen_text` | `SightModule.capture_on_demand(ocr=True)` |
| `get_buffer_status` | `engine.get_buffer_status()` |
| `get_activity_summary` | `engine.warm_tier.query_time_range()` + aggregation |
| `search_history` | `engine.warm_tier.search_text()` |
| `get_context_at` | `engine.warm_tier.query_time_range()` |
| `get_clipboard_history` | `engine.warm_tier.query_time_range(modalities=["clipboard"])` |
| `search_clipboard` | `engine.warm_tier.search_text()` filtered to clipboard |
| `get_agent_stats` | `engine.warm_tier.query_mcp_calls()` |

No MCP client (Claude Code, Cursor, Gemini) needs to change anything.

---

## Appendix B: Privacy Preservation

The Central Memory Engine inherits and strengthens the privacy architecture:

- **No new data collection.** The engine processes events that modules already capture. It adds correlation and learning metadata but does not capture additional user data.
- **Redaction happens before the engine.** Sight's pre-storage redaction pipeline runs before events reach the engine. The engine never sees raw PII.
- **Learning model is local and deletable.** Vocabulary model, correction pairs, behavior patterns — all stored in local SQLite, all deletable from settings panel.
- **Cold tier summarization is lossy.** Individual events are destroyed during warm-to-cold migration. Only aggregated text and statistics survive.
- **Per-modality opt-out.** Disabling a module stops its events from entering the engine. Existing events remain in warm/cold tier subject to retention settings.

---

## Appendix C: Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Engine overhead exceeds 1% CPU | Low | High | Ring buffer is O(1), SQLite WAL is proven. Benchmark weekly. Kill switch: bypass engine, write directly. |
| Correlation false positives | Medium | Medium | Conservative thresholds (0.4 min score). User-visible correlation UI in settings. Tunable per-user. |
| Vocabulary model drift | Medium | Low | 30-day expiry, conflict penalty, user-reviewable table. Export/import for backup. |
| Migration breaks existing Sight users | Low | High | Parallel-run: both old DB and engine active for 30 days. Rollback = config flag. |
| Thread contention on SQLite | Low | Medium | WAL mode + busy_timeout=5000 + NORMAL sync. Proven in existing Sight at higher write rates. |
| Schema migration for future modalities | Low | Low | JSON payload field absorbs modality-specific data. Only envelope fields are schema-locked. |

---

*This document is CONFIDENTIAL — Jerard Ventures LLC trade secret. Do not distribute without NDA.*
