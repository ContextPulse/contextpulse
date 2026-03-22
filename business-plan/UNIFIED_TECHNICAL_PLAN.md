# ContextPulse — Unified Technical Implementation Plan

**March 2026 | Jerard Ventures LLC | CONFIDENTIAL**

> This document replaces TECHNICAL_PLAN.md and CENTRAL_MEMORY_ENGINE.md with a single actionable specification.

---

## 1. Architecture Position

### The Problem with Two Plans

TECHNICAL_PLAN.md builds bottom-up: Sight → Voice → Keys → Flow → Learning Engine (Phase 5, Q2 2027). The learning engine arrives last and retroactively correlates data that was never designed for correlation.

CENTRAL_MEMORY_ENGINE.md builds top-down: full spinal cord first (EventBus, memory tiers, correlation engine, learning engine), then adapt all modules. Architecturally correct but overengineered for a product with zero paying users.

### The Unified Approach

**Contract-first, not engine-first.** Define the spine's interfaces (ContextEvent, EventBus, ModalityModule) before writing any module code. Ship Sight Pro on the contract. Port Voice onto the same contract. The spine gets smarter as cross-modal data accumulates — but the contract never changes.

**Evolve, don't replace.** No third database. activity.db gains a unified `events` table alongside existing tables. shared-knowledge.db stays as cross-project journal.

**Ship revenue early.** Sight Pro ships in Phase 1, before Voice work begins. Revenue funds development.

### Build Sequence

```
Phase 0: Spine Contract (3-4 days)
    Define ContextEvent + EventBus + ModalityModule
    Add events table to activity.db (alongside existing tables)
    Zero runtime change — Sight runs exactly as today
         |
Phase 1: Sight Adaptation + Ship Pro (7-10 days)
    Sight dual-writes to legacy tables AND events table
    2 new Pro-gated MCP tools (search_all_events, get_event_timeline)
    Free/Pro feature gate via license check
    Landing page deploy, Gumroad listing, PyPI publish
         |
Phase 2: Voice (4-6 weeks)
    Port Voiceasy onto spine contract as VoiceModule
    Both ambient (VAD-gated) and push-to-talk modes
    Cross-modal search works: screen + voice from single query
         |
Phase 3+: Keys, Flow, Correlation, Learning (contracts only)
    Interfaces defined in Phase 0
    Implementation deferred until cross-modal data exists
```

---

## 2. Current State Audit

### What's Built

**contextpulse-sight** (`packages/screen/`) — Production-ready, 145 tests

| File | Purpose | LOC |
|------|---------|-----|
| app.py | Daemon: tray, hotkeys, auto-capture loop, watchdog | 470 |
| mcp_server.py | 10 MCP tools via FastMCP, @_track_call decorator | 494 |
| activity.py | SQLite+FTS5 activity DB, clipboard, mcp_calls tables | ~350 |
| buffer.py | Rolling buffer with per-monitor change detection | ~250 |
| capture.py | mss wrapper: per-monitor, region, cursor detection | ~200 |
| ocr_worker.py | Background queue-based OCR pipeline | ~150 |
| clipboard.py | Win32 clipboard monitoring with deduplication | ~120 |
| events.py | Event detector: window focus, idle, monitor crossing | ~150 |
| privacy.py | Window blocklist, session lock monitor | ~150 |
| redact.py | Pre-storage OCR redaction (10+ PII categories) | ~120 |
| classifier.py | OCR-based text/image classification | ~100 |
| setup.py | MCP config generator for Claude Code/Cursor/Gemini | ~100 |

**contextpulse-core** (`packages/core/`) — 35 tests

| File | Purpose | LOC |
|------|---------|-----|
| config.py | Persistent JSON config at %APPDATA%/ContextPulse/ | ~150 |
| license.py | Ed25519 license verification, tiers, expiration | ~200 |
| gui_theme.py | Singleton tkinter root, brand colors | ~120 |
| settings.py | Full settings panel UI | ~200 |
| first_run.py | Welcome dialog with hotkey reference | ~80 |

**Lambda** — Gumroad webhook → Ed25519 license key → SES email. Deployed.

**Voiceasy** (`Projects/Voiceasy/src/voiceasy/`) — Separate product, 3,255 LOC

| File | Purpose | LOC | Portability |
|------|---------|-----|-------------|
| recorder.py | sounddevice 16kHz capture → WAV bytes | 68 | Direct port |
| transcriber.py | faster-whisper local + OpenAI API | 87 | Direct port |
| vocabulary.py | Regex word replacement, hot-reload from JSON | 232 | Direct port (change paths) |
| cleanup.py | Rule-based + Claude LLM text polish | 277 | Port (remove proxy endpoint) |
| analyzer.py | Speech pattern learning, auto-vocabulary | 385 | Port (read events table) |
| model_manager.py | Whisper model download/cache | 64 | Direct port (change paths) |
| app.py | Daemon, hotkey listener, pipeline orchestration | 395 | Rewrite (different architecture) |

### Current Database Schema (activity.db)

```sql
-- Screen captures
CREATE TABLE activity (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    window_title TEXT, app_name TEXT, monitor_index INTEGER DEFAULT 0,
    frame_path TEXT, ocr_text TEXT, ocr_confidence REAL, diff_score REAL DEFAULT 0.0
);
CREATE VIRTUAL TABLE activity_fts USING fts5(
    window_title, app_name, ocr_text, content='activity', content_rowid='id'
);

-- Clipboard history
CREATE TABLE clipboard (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    text TEXT NOT NULL
);

-- MCP tool usage tracking
CREATE TABLE mcp_calls (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    tool_name TEXT NOT NULL, client_id TEXT DEFAULT 'unknown', call_count INTEGER DEFAULT 1
);
```

**Write paths** (4, all protected by threading.Lock()):
1. `app.py` auto-capture → `activity_db.record()`
2. `ocr_worker.py` → `activity_db.update_ocr()`
3. `clipboard.py` → `activity_db.record_clipboard()`
4. `mcp_server.py` @_track_call → `activity_db.record_mcp_call()`

**Read paths**: All 10 MCP tools read via ActivityDB methods.

### Shared-Knowledge Journal (shared-knowledge.db)

Cross-project, multi-agent SQLite journal. 345 entries, 9 entry types, 3 SQL views (open_actions, completed_actions, recent_activity). Write via log-entry.py, read via query-journal.py.

**Relationship to ContextPulse:** Read-only. ContextPulse can query shared-knowledge.db for cross-project context (e.g., "what was I working on?") but never writes to it. Agents write session/action entries via the existing scripts.

---

## 3. The Spine Contract

### 3.1 ContextEvent

The universal event format. Every modality emits `ContextEvent` objects. No exceptions.

```python
from __future__ import annotations
import time, uuid, json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Modality(Enum):
    SIGHT = "sight"
    VOICE = "voice"
    CLIPBOARD = "clipboard"
    SYSTEM = "system"
    # Future: KEYS = "keys", FLOW = "flow"


class EventType(Enum):
    # Sight
    SCREEN_CAPTURE = "screen_capture"
    OCR_RESULT = "ocr_result"
    # Voice
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    TRANSCRIPTION = "transcription"
    # Clipboard
    CLIPBOARD_CHANGE = "clipboard_change"
    # System
    WINDOW_FOCUS = "window_focus"
    IDLE_START = "idle_start"
    IDLE_END = "idle_end"
    SESSION_LOCK = "session_lock"
    SESSION_UNLOCK = "session_unlock"
    # Future: KEYSTROKE, TYPING_BURST, TYPING_PAUSE, SHORTCUT, CLICK, SCROLL, HOVER_DWELL


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """Universal event format for the Central Memory Engine."""

    # Required
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = field(default_factory=time.time)
    modality: Modality = Modality.SYSTEM
    event_type: EventType = EventType.WINDOW_FOCUS

    # Context (populated by capture module)
    app_name: str = ""
    window_title: str = ""
    monitor_index: int = 0

    # Modality-specific payload
    payload: dict[str, Any] = field(default_factory=dict)

    # Engine-populated (defaults until correlation/learning engine exists)
    correlation_id: str | None = None
    attention_score: float = 0.0

    def validate(self) -> bool:
        if not self.event_id or not isinstance(self.timestamp, float):
            return False
        if self.timestamp <= 0 or self.timestamp > time.time() + 60:
            return False
        if not isinstance(self.modality, Modality):
            return False
        if not isinstance(self.event_type, EventType):
            return False
        return True

    def to_row(self) -> dict:
        """Flatten for SQLite insert."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "modality": self.modality.value,
            "event_type": self.event_type.value,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "monitor_index": self.monitor_index,
            "payload": json.dumps(self.payload),
            "correlation_id": self.correlation_id,
            "attention_score": self.attention_score,
        }

    def text_content(self) -> str:
        """Extract searchable text from payload for FTS indexing."""
        parts = []
        for key in ("ocr_text", "transcript", "text", "burst_text"):
            val = self.payload.get(key)
            if val:
                parts.append(str(val))
        return " ".join(parts)
```

### 3.2 Payload Contracts

Each modality defines what goes in `payload`. The EventBus doesn't enforce payload schema — modalities own that.

```python
# Sight payloads
SightCapturePayload = {
    "frame_path": str,        # Path to JPEG file
    "diff_score": float,      # Change from previous frame
    "token_estimate": int,    # Estimated API tokens
    "storage_mode": str,      # "image", "text_only", "hybrid"
}
SightOCRPayload = {
    "ocr_text": str,          # Extracted text
    "ocr_confidence": float,  # 0.0-1.0
    "frame_path": str,        # Associated frame
}

# Voice payloads
VoiceTranscriptionPayload = {
    "transcript": str,        # Cleaned transcript
    "raw_transcript": str,    # Pre-cleanup transcript
    "confidence": float,      # 0.0-1.0
    "language": str,          # ISO 639-1
    "duration_seconds": float,
    "cleanup_applied": bool,
}

# Clipboard payload
ClipboardPayload = {
    "text": str,
    "hash": str,              # For deduplication
    "source_app": str | None,
}
```

### 3.3 EventBus

Routes events to storage and notifies listeners.

```python
class EventBus:
    def __init__(self, db_path: Path):
        """Open SQLite connection to activity.db.
        Creates events + events_fts tables if they don't exist.
        Does NOT modify existing activity/clipboard/mcp_calls tables."""

    def emit(self, event: ContextEvent) -> None:
        """Validate event, persist to events table, update FTS, notify listeners."""

    def on(self, callback: Callable[[ContextEvent], None]) -> None:
        """Register listener called on every emit."""

    def query_recent(self, seconds: float = 300, modality: str | None = None,
                     limit: int = 50) -> list[ContextEvent]:
        """Return recent events, optionally filtered by modality."""

    def search(self, query: str, minutes_ago: float = 30,
               modality: str | None = None) -> list[dict]:
        """FTS5 search across all event text content."""

    def get_by_time(self, target_timestamp: float,
                    window_seconds: float = 5) -> list[ContextEvent]:
        """Get events within a time window. Used for temporal correlation."""
```

### 3.4 ModalityModule

Abstract base class for all capture modules.

```python
from abc import ABC, abstractmethod

class ModalityModule(ABC):
    @abstractmethod
    def get_modality(self) -> Modality: ...

    @abstractmethod
    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        """Store the EventBus callback for emitting events."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_alive(self) -> bool: ...

    @abstractmethod
    def get_status(self) -> dict:
        """Return {modality, running, events_emitted, last_event_timestamp, error}."""
```

### 3.5 Schema Migration

Added to activity.db via `EventBus._init_schema()`:

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    modality TEXT NOT NULL,
    event_type TEXT NOT NULL,
    app_name TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    payload TEXT NOT NULL,       -- JSON blob
    correlation_id TEXT,
    attention_score REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_modality ON events(modality, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(app_name, timestamp DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    window_title, app_name, text_content,
    content='events', content_rowid=rowid
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, window_title, app_name, text_content)
    VALUES (
        new.rowid, new.window_title, new.app_name,
        COALESCE(
            json_extract(new.payload, '$.ocr_text'),
            json_extract(new.payload, '$.transcript'),
            json_extract(new.payload, '$.text'),
            ''
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, window_title, app_name, text_content)
    VALUES ('delete', old.rowid, old.window_title, old.app_name, '');
END;
```

Existing activity, clipboard, mcp_calls tables **unchanged**.

---

## 4. Phase 0: Spine Contract Implementation

**Duration:** 3-4 days | **Risk:** None (additive only)

### Files Created

| File | Purpose |
|------|---------|
| `packages/core/src/contextpulse_core/spine/__init__.py` | Re-exports |
| `packages/core/src/contextpulse_core/spine/events.py` | ContextEvent, Modality, EventType |
| `packages/core/src/contextpulse_core/spine/bus.py` | EventBus |
| `packages/core/src/contextpulse_core/spine/module.py` | ModalityModule ABC |
| `packages/core/tests/test_spine.py` | 25-30 tests |

### Files Modified

None. Zero runtime change.

### Tests (25-30)

- ContextEvent: creation with defaults, explicit fields, validation pass/fail, to_row(), text_content()
- EventBus: emit + query_recent, emit + search FTS, schema migration idempotency, thread safety
- ModalityModule: mock implementation satisfies ABC, get_status() returns correct shape

### Acceptance Criteria

- `from contextpulse_core.spine import ContextEvent, EventBus, ModalityModule, Modality, EventType` works
- EventBus opens existing activity.db, adds events table without touching legacy tables
- All 180 existing tests pass unchanged
- All new spine tests pass

---

## 5. Phase 1: Sight Adaptation + Ship Pro

**Duration:** 7-10 days | **Risk:** Low (dual-write is additive)

### Phase 1A: Sight Adapter (4-5 days)

**`packages/screen/src/contextpulse_sight/sight_module.py`** (NEW)

```python
class SightModule(ModalityModule):
    """Wraps existing Sight capture pipeline to emit ContextEvents."""

    def __init__(self):
        self._callback = None
        self._events_emitted = 0
        self._last_timestamp = None
        self._running = False

    def get_modality(self) -> Modality:
        return Modality.SIGHT

    def register(self, event_callback):
        self._callback = event_callback

    def start(self): self._running = True
    def stop(self): self._running = False
    def is_alive(self): return self._running

    def emit_capture(self, timestamp, app_name, window_title, monitor_index,
                     frame_path, diff_score, token_estimate=0, storage_mode="image"):
        if not self._callback: return
        event = ContextEvent(
            timestamp=timestamp,
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name=app_name,
            window_title=window_title,
            monitor_index=monitor_index,
            payload={"frame_path": frame_path, "diff_score": diff_score,
                     "token_estimate": token_estimate, "storage_mode": storage_mode},
        )
        self._callback(event)
        self._events_emitted += 1
        self._last_timestamp = timestamp

    def emit_ocr(self, timestamp, frame_path, ocr_text, confidence,
                 app_name="", window_title=""):
        if not self._callback: return
        event = ContextEvent(
            timestamp=timestamp,
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            app_name=app_name, window_title=window_title,
            payload={"ocr_text": ocr_text, "ocr_confidence": confidence,
                     "frame_path": frame_path},
        )
        self._callback(event)
        self._events_emitted += 1

    def emit_clipboard(self, timestamp, text, hash, source_app=None):
        if not self._callback: return
        event = ContextEvent(
            timestamp=timestamp,
            modality=Modality.CLIPBOARD,
            event_type=EventType.CLIPBOARD_CHANGE,
            payload={"text": text, "hash": hash, "source_app": source_app},
        )
        self._callback(event)
        self._events_emitted += 1

    def emit_system(self, event_type, app_name="", window_title=""):
        if not self._callback: return
        event = ContextEvent(
            modality=Modality.SYSTEM,
            event_type=event_type,
            app_name=app_name, window_title=window_title,
        )
        self._callback(event)
        self._events_emitted += 1

    def get_status(self):
        return {
            "modality": "sight",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_timestamp,
            "error": None,
        }
```

**Modified: `app.py`** — Add to `__init__`:
```python
from contextpulse_core.spine import EventBus
from contextpulse_sight.sight_module import SightModule

self._event_bus = EventBus(db_path=ACTIVITY_DB_PATH)
self._sight_module = SightModule()
self._sight_module.register(self._event_bus.emit)
self._sight_module.start()
```

In `_do_auto_capture`, after `self.activity_db.record(...)`:
```python
self._sight_module.emit_capture(
    timestamp=ts, app_name=app, window_title=title,
    monitor_index=idx, frame_path=path, diff_score=diff,
)
```

Similar additions for OCR updates, clipboard records, and system events.

**Modified: `mcp_server.py`** — Add Pro-gated tools:

```python
import functools
from contextpulse_core.license import is_licensed

def _require_pro(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not is_licensed():
            return ("This tool requires ContextPulse Pro ($9/mo). "
                    "Get a license at contextpulse.ai/pro")
        return await func(*args, **kwargs)
    return wrapper

@mcp_app.tool()
@_require_pro
async def search_all_events(query: str, minutes_ago: float = 30,
                            modality: str | None = None) -> list[dict]:
    """Search across all captured events (screen, voice, clipboard)."""
    return _event_bus.search(query, minutes_ago, modality)

@mcp_app.tool()
@_require_pro
async def get_event_timeline(minutes: float = 5,
                             modalities: list[str] | None = None) -> list[dict]:
    """Get chronological event stream from all modalities."""
    events = _event_bus.query_recent(seconds=minutes * 60, limit=100)
    if modalities:
        events = [e for e in events if e.modality.value in modalities]
    return [e.to_row() for e in events]
```

### Phase 1B: Ship Pro (3-5 days)

| Step | Action |
|------|--------|
| Free/Pro gate | @_require_pro on 2 new tools; 10 existing tools remain free |
| Gumroad | Create listing: "ContextPulse Sight Pro" — $9/month or $79/year |
| Lambda | Verify existing webhook handles subscription (not just one-time) |
| Landing page | Deploy site/index.html → contextpulse.ai via Cloudflare Pages |
| PyPI | Publish contextpulse-sight + contextpulse-core |
| MCP registry | Update setup.py generator with new tools documentation |

### Tests (20-25 new)

- SightModule: lifecycle, emit_capture/ocr/clipboard/system
- Dual-write: verify both activity table AND events table populated
- Pro gate: search_all_events unlicensed → error message
- Pro gate: search_all_events licensed → results

### Acceptance Criteria

- Daemon starts normally, writes to both legacy + events tables
- All 10 existing MCP tools work identically (zero regression)
- 2 new Pro tools work when licensed, reject when unlicensed
- Landing page live at contextpulse.ai
- Gumroad listing published
- License flow end-to-end: purchase → email → paste key → Pro unlocked

---

## 6. Phase 2: ContextPulse Voice

**Duration:** 4-6 weeks | **Risk:** Medium (new dependency chain)

### Package: `packages/voice/`

```
packages/voice/
├── pyproject.toml
├── src/contextpulse_voice/
│   ├── __init__.py
│   ├── voice_module.py       # VoiceModule(ModalityModule) — main adapter
│   ├── recorder.py           # Port from Voiceasy — sounddevice capture
│   ├── transcriber.py        # Port from Voiceasy — faster-whisper
│   ├── vad.py                # NEW — Voice Activity Detection
│   ├── vocabulary.py         # Port from Voiceasy — word replacement
│   ├── cleanup.py            # Port from Voiceasy — text polish
│   ├── analyzer.py           # Port from Voiceasy — speech learning
│   └── model_manager.py      # Port from Voiceasy — Whisper model cache
└── tests/
    ├── test_voice_module.py
    ├── test_recorder.py
    ├── test_vad.py
    ├── test_vocabulary.py
    └── test_cleanup.py
```

### Voice Modes

Both ambient and push-to-talk, user-selectable:

| Mode | Trigger | CPU | Privacy | Default |
|------|---------|-----|---------|---------|
| Push-to-talk | Hotkey hold | Low (only during recording) | Explicit consent | Yes |
| Ambient | VAD auto-detect | Low idle, burst on speech | Opt-in with warning | No |

Config: `voice_mode: "push_to_talk" | "ambient"`

### VoiceModule Architecture

```python
class VoiceModule(ModalityModule):
    def __init__(self, config: dict):
        self._recorder = Recorder()
        self._transcriber = LocalTranscriber(model_size=config.get("voice_model", "base"))
        self._vocabulary = VocabularyManager()
        self._cleanup = TextCleanup()
        self._vad = VADDetector(threshold=config.get("voice_vad_threshold", 0.3))
        self._mode = config.get("voice_mode", "push_to_talk")
        self._callback = None

    def get_modality(self) -> Modality:
        return Modality.VOICE

    def register(self, callback):
        self._callback = callback

    def start(self):
        if self._mode == "ambient":
            self._start_ambient()  # Continuous mic monitoring with VAD
        # Push-to-talk waits for hotkey (registered in app.py)

    def process_audio(self, wav_bytes: bytes):
        """Pipeline: transcribe → cleanup → vocabulary → emit event."""
        raw = self._transcriber.transcribe(wav_bytes)
        cleaned = self._cleanup.clean(raw)
        final = self._vocabulary.apply(cleaned)
        self._callback(ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            payload={
                "transcript": final,
                "raw_transcript": raw,
                "confidence": 0.85,  # from Whisper
                "language": "en",
                "duration_seconds": len(wav_bytes) / (16000 * 2),
                "cleanup_applied": cleaned != raw,
            },
        ))
```

### VAD (Voice Activity Detection)

```python
class VADDetector:
    """Energy-based VAD for ambient mode. No neural network."""

    def __init__(self, threshold: float = 0.3, silence_duration: float = 2.0):
        self.threshold = threshold          # RMS energy threshold
        self.silence_duration = silence_duration  # Seconds of silence to end segment
        self._speaking = False
        self._silence_start = None

    def process_chunk(self, audio_chunk: np.ndarray) -> str:
        """Returns 'speech', 'silence', or 'end_of_speech'."""
        rms = np.sqrt(np.mean(audio_chunk ** 2))
        if rms > self.threshold:
            self._speaking = True
            self._silence_start = None
            return "speech"
        elif self._speaking:
            if self._silence_start is None:
                self._silence_start = time.time()
            if time.time() - self._silence_start > self.silence_duration:
                self._speaking = False
                self._silence_start = None
                return "end_of_speech"
            return "silence"
        return "silence"
```

### Integration with Sight Daemon

**Modified: `app.py`** — Conditional Voice loading:
```python
if config.get("voice_enabled") and is_licensed():
    from contextpulse_voice import VoiceModule
    self._voice_module = VoiceModule(config)
    self._voice_module.register(self._event_bus.emit)
    self._voice_module.start()
    # Add tray menu item: "Voice: Active/Paused"
```

### MCP Server Additions (Pro-gated)

```python
@mcp_app.tool()
@_require_pro
async def search_voice_history(query: str, minutes_ago: float = 30) -> list[dict]:
    """Search voice transcription history."""
    return _event_bus.search(query, minutes_ago, modality="voice")

@mcp_app.tool()
@_require_pro
async def get_recent_speech(minutes: float = 5, count: int = 10) -> list[dict]:
    """Get recent voice transcriptions."""
    events = _event_bus.query_recent(seconds=minutes*60, modality="voice", limit=count)
    return [e.to_row() for e in events]
```

### Schema Changes

None. Voice events use the existing `events` table with `modality='voice'`. The FTS trigger automatically indexes `$.transcript` via the COALESCE chain.

### Vocabulary Merge Strategy

On first Voice enable:
1. Check if `%APPDATA%/Voiceasy/vocabulary.json` exists
2. If yes, show dialog: "Import vocabulary from Voiceasy?"
3. Merge into `%APPDATA%/ContextPulse/vocabulary.json` (ContextPulse takes priority on conflicts)
4. Voiceasy file untouched (both products coexist)

### Tests (40-50)

- VoiceModule lifecycle (register, start, stop, is_alive, get_status)
- Recorder capture (mock sounddevice)
- Transcriber (mock faster-whisper)
- VAD detection (energy threshold, silence, end_of_speech)
- Vocabulary import/merge from Voiceasy
- Cleanup pipeline (basic rules, LLM mock)
- Event emission (SPEECH_START, TRANSCRIPTION, SPEECH_END)
- Cross-modal: search_all_events finds both screen OCR + voice transcripts

### Acceptance Criteria

- VoiceModule registers with EventBus and emits events
- `search_all_events("error")` finds both OCR text AND voice transcripts
- Voice events appear in get_event_timeline alongside screen events
- Voice toggle from settings panel and tray menu
- Push-to-talk and ambient modes both functional
- All 200+ existing tests pass (Sight unchanged)
- Voiceasy continues working independently

---

## 7. Phase 3+: Future Phases (Contracts Only)

Implementation deferred. Only interface contracts defined here.

### Keys Module

```python
class KeysModule(ModalityModule):
    """Keyboard capture with privacy-first design."""
    # Emits: KEYSTROKE, TYPING_BURST, TYPING_PAUSE, SHORTCUT
    # Default: metadata-only (WPM, timing, shortcuts)
    # Full keystroke capture: explicit opt-in via settings
    # Privacy: Win32 UIA IsPassword detection, per-app blocklist
```

Payload: `{capture_mode, wpm_snapshot, burst_duration, shortcut, burst_text (if full mode)}`

### Flow Module

```python
class FlowModule(ModalityModule):
    """Pointer/mouse capture with attention tracking."""
    # Emits: CLICK, SCROLL, HOVER_DWELL
    # Win32 UIA ElementFromPoint for click target ID
    # Movement efficiency: straight_line / actual_path
    # Heatmaps: Pillow Gaussian kernel per app session
```

Payload: `{x, y, target_element, target_control, dwell_duration, efficiency_ratio, scroll_delta}`

### Correlation Engine

```python
class TemporalCorrelationEngine:
    """Links events across modalities by time + context."""
    # Background thread, 5-second cycle
    # Scoring: time_proximity (within 5s) + same_app + text_overlap (FTS match)
    # Writes correlation_id back to linked events
    # Enables: "Show me what I was looking at when I said X"
```

### Learning Engine

```python
class LearningEngine:
    """Extracts patterns from cross-modal event streams."""
    # Background thread, 60-second cycle
    # Correction pairs: voice transcript → keyboard correction within 10s
    # Vocabulary model: SQLite-backed, confidence lifecycle (0.5 start, +0.1/match, -0.2/conflict)
    # Whisper hotwords: inject high-confidence vocabulary into initial_prompt
    # Attention scoring: pointer dwell + event density → frame relevance
```

### Memory Tiers (Deferred)

Current SQLite (events table) is sufficient through Phase 2. When volume justifies:
- Hot: in-memory deque (same as current RollingBuffer)
- Warm: events table (current)
- Cold: nightly summarization → compressed archive with FTS

---

## 8. Database Evolution

```
Phase 0 (now):
  activity.db
  ├── activity        (existing, unchanged)
  ├── activity_fts    (existing, unchanged)
  ├── clipboard       (existing, unchanged)
  ├── mcp_calls       (existing, unchanged)
  ├── events          (NEW — unified event store)
  └── events_fts      (NEW — cross-modal FTS)

Phase 1 (Sight dual-write):
  Sight writes to BOTH activity + events tables
  Legacy MCP tools read from activity (unchanged)
  New Pro MCP tools read from events

Phase 2 (Voice added):
  Voice writes to events only (no legacy table)
  Cross-modal search queries events_fts (screen + voice)

Phase 3+ (eventual consolidation):
  Migrate remaining MCP tools from activity → events
  Drop legacy tables when migration complete
  Add: correlations, correction_pairs, vocabulary_model tables
```

---

## 9. Performance Budget

### Current (Sight Only)

| Metric | Current | Target |
|--------|---------|--------|
| CPU (idle) | <1% | <1% |
| RAM | <20 MB | <20 MB |
| Disk write rate | <2 MB/min | <2 MB/min |
| Capture latency | 3ms | <10ms |

### Phase 1 (Sight + EventBus dual-write)

| Metric | Expected | Target |
|--------|----------|--------|
| CPU overhead from EventBus | <0.1% | <0.5% |
| Additional disk writes | ~12 events/min | <50 events/min |
| RAM for EventBus | <5 MB | <10 MB |

### Phase 2 (Sight + Voice)

| Metric | Expected | Target |
|--------|----------|--------|
| CPU (Voice idle, push-to-talk) | <0.5% | <1% |
| CPU (Voice ambient, no speech) | <1% | <2% |
| CPU (Whisper transcribing) | 5-15% burst | Below Normal priority |
| RAM (Whisper model loaded) | +200 MB | <300 MB total |

---

## 10. Testing Strategy

### Test Targets by Phase

| Phase | New Tests | Cumulative | Focus |
|-------|-----------|------------|-------|
| 0 | 25-30 | 210 | ContextEvent, EventBus, ModalityModule contract |
| 1A | 20-25 | 235 | SightModule, dual-write, Pro gate |
| 2 | 40-50 | 285 | VoiceModule, VAD, cross-modal search |

### Integration Tests

| Scenario | Pass Criteria |
|----------|---------------|
| Sight dual-write | Same frame appears in activity table AND events table |
| Pro gate (unlicensed) | Pro tools return upgrade message string |
| Pro gate (licensed) | Pro tools return data |
| Cross-modal search (Phase 2) | `search_all_events("error")` returns both OCR + voice results |
| Voice + Sight timeline | `get_event_timeline` returns interleaved screen + voice events |

### Performance Benchmarks

| Metric | Test Method | Pass Criteria |
|--------|-------------|---------------|
| EventBus emit latency | timeit on 1000 emits | <1ms per emit |
| FTS search latency | timeit on search() | <50ms |
| Dual-write overhead | CPU% with/without EventBus | <0.5% increase |

---

## 11. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Dual-write doubles SQLite load | Low | Events written at 12/min; trivial for SQLite WAL |
| EventBus + ActivityDB both open same file | Low | Separate connections, WAL mode supports this, separate tables |
| Voice adds 200MB RAM (Whisper model) | Medium | Voice is opt-in (Pro only), model loads lazily, Below Normal priority |
| Voiceasy users confused by ContextPulse Voice | Medium | Different products: Voiceasy = dictation (paste), ContextPulse Voice = context (store). Both can coexist |
| Subscription pricing churn | Medium | $79/yr option for committed users. Free tier generous enough to demonstrate value |
| Competitor ships similar product | High | Speed of execution. Spine contract enables faster feature velocity than competitors building ad-hoc |

---

## Appendix A: File Change Matrix

| File | Phase 0 | Phase 1A | Phase 1B | Phase 2 |
|------|---------|----------|----------|---------|
| `core/spine/__init__.py` | CREATE | — | — | — |
| `core/spine/events.py` | CREATE | — | — | — |
| `core/spine/bus.py` | CREATE | — | — | — |
| `core/spine/module.py` | CREATE | — | — | — |
| `core/tests/test_spine.py` | CREATE | — | — | — |
| `screen/sight_module.py` | — | CREATE | — | — |
| `screen/app.py` | — | MODIFY | — | MODIFY |
| `screen/mcp_server.py` | — | MODIFY | — | MODIFY |
| `core/config.py` | — | — | MODIFY | MODIFY |
| `site/index.html` | — | — | DEPLOY | — |
| `voice/**` | — | — | — | CREATE |

No changes to: capture.py, buffer.py, classifier.py, privacy.py, redact.py, events.py, icon.py, activity.py (schema migration handled by EventBus).

---

## Appendix B: Revenue Alignment

| Phase | Ship Target | Revenue Event |
|-------|------------|---------------|
| 1B | Q2 2026 | Sight Pro $9/mo or $79/yr on Gumroad |
| 2 | Q3 2026 | Voice bundled into Pro subscription (value-add, not separate charge) |
| 3+ | 2027+ | Memory/Agent/Project as premium tiers ($19-29/mo) |

---

*This document is CONFIDENTIAL — Jerard Ventures LLC trade secret. Do not distribute without NDA.*
