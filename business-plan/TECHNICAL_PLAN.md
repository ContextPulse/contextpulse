# ContextPulse — Technical Implementation Plan
**March 2026 | Jerard Ventures LLC | CONFIDENTIAL**

---

## 1. Current State Audit

### What's Built

**contextpulse-sight** (`packages/screen/`) — Production-ready, Phase 3.0 complete

| File | Purpose | Est. LOC |
|------|---------|---------|
| `app.py` | Main daemon: tray, hotkeys, auto-capture loop, watchdog | 470 |
| `capture.py` | mss wrapper: per-monitor, region, cursor detection | ~200 |
| `buffer.py` | Rolling buffer with per-monitor change detection, token estimation | ~250 |
| `classifier.py` | Dual-threshold OCR classifier (100 chars / 70% confidence) | ~100 |
| `mcp_server.py` | 10 MCP tools via FastMCP, @_track_call decorator | 494 |
| `activity.py` | SQLite+FTS5 activity DB, clipboard, mcp_calls tables | ~350 |
| `ocr_worker.py` | Background queue-based OCR pipeline | ~150 |
| `clipboard.py` | Win32 clipboard monitoring with deduplication/debounce | ~120 |
| `events.py` | Event detector: window focus, idle, monitor crossing | ~150 |
| `privacy.py` | Window blocklist, session lock monitor, process name lookup | ~150 |
| `redact.py` | Pre-storage OCR redaction (10+ PII/credential categories) | ~120 |
| `setup.py` | MCP config generator for Claude Code/Cursor/Gemini | ~100 |

**contextpulse-core** (`packages/core/`) — Productized

| File | Purpose | Est. LOC |
|------|---------|---------|
| `config.py` | Persistent JSON config at %APPDATA%/ContextPulse/ | ~150 |
| `license.py` | Ed25519 license verification, tiers, expiration | ~200 |
| `gui_theme.py` | Singleton tkinter root, brand colors, dialog factory | ~120 |
| `settings.py` | Full settings panel (capture, hotkeys, privacy, license) | ~200 |
| `first_run.py` | Welcome dialog with hotkey reference | ~80 |

**Lambda** (`lambda/license_webhook.py`) — Deployed: Gumroad webhook to Ed25519 key to SES email (~150 LOC)

### Test Coverage

| Package | Tests | Coverage Areas |
|---------|-------|---------------|
| contextpulse-sight | 145 | Capture, buffer, OCR, clipboard, privacy, MCP tools, redaction |
| contextpulse-core | 35 | Config persistence, license verification |
| **Total** | **180** | — |

### Tech Stack (Current)

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3.14 | Type hints throughout |
| Screen capture | mss | 3ms/frame, zero deps, DPI-aware |
| Hotkeys/mouse | pynput | No admin required |
| System tray | pystray + Pillow | Proven in Voiceasy |
| OCR | rapidocr-onnxruntime | On-demand, on-device |
| MCP | mcp Python SDK (FastMCP) | stdio transport |
| Database | SQLite + FTS5 | Activity, clipboard, mcp_calls |
| Config | python-dotenv + JSON | %APPDATA%/ContextPulse/config.json |
| Licensing | PyNaCl (Ed25519) | Asymmetric key verification |
| GUI | tkinter | Settings, first-run, nag dialog |
| Cloud infra | AWS Lambda + SES | License delivery only |

### Performance Baseline (Measured 2026-03-21)

| Metric | Current | Target |
|--------|---------|--------|
| CPU (idle) | <1% | <1% |
| RAM | <20 MB | <20 MB |
| Disk write rate | <2 MB/min | <2 MB/min |
| Capture latency | 3ms | <10ms |
| Storage savings | 59% (text-only frames) | >=50% |
| Startup time | <2s | <2s |

---

## 2. Target Architecture

```
+---------------------------------------------------------------------+
|                        AI AGENT LAYER                                |
|   Claude Code | Cursor | Copilot | Gemini CLI | Any MCP Client      |
+---------------------------+-----------------------------------------+
                            | MCP stdio transport
+---------------------------v-----------------------------------------+
|                    CONTEXTPULSE MCP SERVER                           |
|  CURRENT (10 tools):          FUTURE (Phase 2-5):                   |
|  get_screenshot               search_audio_history                   |
|  get_recent                   get_recent_speech                      |
|  get_screen_text              get_typing_patterns                    |
|  get_buffer_status            get_fatigue_estimate                   |
|  get_activity_summary         get_attention_heatmap                  |
|  search_history               get_cognitive_load                     |
|  get_context_at               get_cross_modal_context                |
|  get_agent_stats              get_memory_summary                     |
|  get_clipboard_history        search_all_modalities                  |
|  search_clipboard             get_user_model_stats                   |
+---------+----------+----------+----------+--------------------------+
          |          |          |          |
          v          v          v          v
+-----------+ +----------+ +--------+ +----------+
|   SIGHT   | |  VOICE   | |  KEYS  | |  FLOW    |
|  (LIVE)   | | (Q3 2026)| |(Q4 26) | | (Q1 2027)|
| mss 3ms   | | faster-  | |pynput  | | pynput   |
| Per-mon.  | | whisper  | |keylog  | | mouse    |
| OCR+redact| | VAD gate | |Sensitive| | listener |
| Smart stor| | Speaker  | | exclus.| | Click ID |
| Diff detec| | diarize  | | WPM/   | | Heatmaps |
| Activity  | | Temporal | | fatigue| | Dwell    |
| Clipboard | | 30s bufs | | Burst  | | Tremor   |
+-----------+ +----------+ +--------+ +----------+
       |            |          |           |
       +------------+----------+-----------+
                         |
              +----------v----------+
              |  UNIFIED ACTIVITY   |
              |  DATABASE (SQLite)  |
              |                     |
              |  screen_frames      |
              |  audio_segments     |
              |  keystroke_events   |
              |  pointer_events     |
              |  clipboard_entries  |
              |  mcp_calls          |
              |  correction_pairs   | <- Phase 5
              |  vocabulary_model   | <- Phase 5
              +----------+----------+
                         |
              +----------v----------+
              |  CROSS-MODAL        |
              |  LEARNING ENGINE    |
              |  (Q2 2027)          |
              |                     |
              | Voice->Keys vocab   |
              | Screen ground truth |
              | Pointer attention   |
              | Cognitive load est. |
              +---------------------+
                         |
              +----------v----------+
              |  LOCAL STORAGE ONLY |
              |  (no cloud upload)  |
              +---------------------+
```

---

## 3. Module Specifications

### 3.1 Sight — LIVE

**Input:** Display framebuffer (mss), Win32 window APIs, clipboard API
**Output:** JPEG images + OCR sidecar .txt files, SQLite records, MCP tool responses
**LOC:** ~3,750 | **Tests:** 180 | **Complexity:** Complete

Key challenges solved: per-monitor DPI-aware capture, event-replacement scheduler, background OCR queue, Win32 WM_CLIPBOARDUPDATE monitoring without polling.

---

### 3.2 Voice (ContextVoice) — Q2-Q3 2026

**Input:** System microphone (sounddevice, 16kHz mono)
**Output:** Opus segments (deleted post-transcription), transcriptions, speaker labels, FTS5 index

**Key challenges:**
- VAD: `silero-vad` (ONNX, 2MB, SOTA) gates Whisper — eliminates silence transcription, reduces CPU 60-80%
- Rolling 30-second buffer with 5-second overlap prevents transcription boundary artifacts
- Temporal alignment: link audio segment timestamps to nearest screen frame in shared DB
- Correction detection: user types correction of transcript — record pair for vocabulary model
- Speaker diarization: `pyannote.audio` (optional V1.1, HF token) or energy-based fallback (V1)

**From Voiceasy (direct reuse):** `recorder.py`, `transcriber.py`, `vocabulary.py`, `model_manager.py`, Ed25519 licensing

**MCP tools:** `search_audio_history`, `get_recent_speech`, `get_speaker_stats`

**LOC:** ~1,200 new + ~600 ported from Voiceasy | **Complexity:** Medium (Voiceasy provides 60%)

---

### 3.3 Keys (ContextPulse Keys) — Q4 2026

**Input:** Global keyboard events via pynput
**Output:** Keystroke records (key, timestamp, app_name), WPM timeseries, fatigue signals

**Key challenges:**
- Sensitive detection: Win32 UIA `IsPassword` attribute via `comtypes`. Cannot rely on window title alone. Novel approach — most keyloggers skip accessibility API inspection.
- Per-app policy: `full` (default) / `metadata-only` (WPM/timing, no key content) / `block`. Config-driven.
- Fatigue regression: 15-minute sliding WPM window, `scipy.stats.linregress`, negative slope > threshold = fatigue
- Burst/pause: inter-keystroke interval > 2s = pause event (cognitive load signal)

**MCP tools:** `get_typing_patterns`, `get_recent_keystrokes`, `get_fatigue_estimate`

**LOC:** ~900 | **Complexity:** Medium-high

**Trade secrets:** Fatigue regression parameters, WPM window size, UIA inspection method

---

### 3.4 Flow (ContextPulse Flow) — Q1 2027

**Input:** Global mouse events via pynput
**Output:** Pointer records, per-app heatmap images, dwell events, efficiency metrics

**Key challenges:**
- Click target ID: Win32 UIA `ElementFromPoint(x, y)` — identifies button/control clicked. No competitor does this.
- Hover dwell: state machine per UIA element, threshold 1.5s = `hover_dwell` event
- Movement efficiency: `straight_line_distance / actual_path_distance`
- Heatmap: Pillow Gaussian kernel overlay on click+dwell positions per app session
- Tremor: FFT on pointer position timeseries, rolling 2-second windows, ~8 Hz oscillation detection

**MCP tools:** `get_attention_heatmap`, `get_pointer_efficiency`, `get_interaction_summary`

**LOC:** ~1,000 | **Complexity:** High

---

### 3.5 Memory — Q2 2027

Port SynapseAI journal pattern. End-of-day summarization reads activity DB, calls Claude API or local Ollama, writes to `memory.db`. **LOC:** ~600 | **Complexity:** Medium

---

### 3.6 Heart (ContextPulse Heart) — Q3-Q4 2027

**Input:** User-defined profile (mission, values, goals, passions, life domains)
**Output:** Priority weights, goal alignment scores, life balance metrics, context filtering decisions

**Purpose:** Heart is the compass for the entire ecosystem. Without it, the spine stores data. With Heart, the spine makes *judgments* — what to surface, what to filter, what to flag as important.

**Key components:**
- Structured profile store: mission statement, goals (short/medium/long-term), values, passions, life domains (work, family, health, creative)
- Weighting API: given a ContextEvent, return a relevance score weighted by user's stated priorities
- Goal tracker: connect daily Sight/Voice/Keys activity patterns to declared objectives
- Boundary enforcement: user-defined time blocks (e.g., "family dinners 6-7pm are sacred")
- Drift detection: alert when daily activity diverges significantly from stated priorities

**Schema:**
```sql
CREATE TABLE heart_profile (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL, domain TEXT, -- 'work', 'family', 'health', 'creative'
    weight REAL DEFAULT 1.0, updated_at REAL NOT NULL
);
CREATE TABLE heart_goals (
    id INTEGER PRIMARY KEY, description TEXT NOT NULL,
    domain TEXT, priority TEXT DEFAULT 'medium', -- 'critical', 'high', 'medium', 'low'
    target_date REAL, status TEXT DEFAULT 'active',
    progress_notes TEXT, created_at REAL NOT NULL
);
CREATE TABLE heart_boundaries (
    id INTEGER PRIMARY KEY, rule_type TEXT NOT NULL, -- 'time_block', 'app_limit', 'focus_mode'
    config TEXT NOT NULL, -- JSON: {"days": ["Mon-Fri"], "start": "18:00", "end": "19:00"}
    active INTEGER DEFAULT 1
);
```

**MCP tools:** `get_priorities`, `check_goal_alignment`, `get_life_balance`, `get_drift_report`

**LOC:** ~400 | **Complexity:** Low (structured profile + weighting API). Highest impact-to-effort ratio in the entire roadmap.

---

### 3.7 Contacts (ContextPulse Contacts) — Q1 2028

**Input:** Entity mentions from Sight OCR, Voice transcripts, email/calendar integrations
**Output:** People records, interaction history, relationship graph, follow-up tracking

**Purpose:** Personal CRM powered by the spine. When you say "email Sarah about the proposal," the system knows which Sarah, your last conversation, and the communication tone.

**Key components:**
- PersonEntity extractor: NER on OCR text and voice transcripts to detect names, link to contact records
- Interaction logger: auto-populate from email (Gmail MCP), calendar, screen mentions, voice mentions
- Relationship graph: who connects to whom, frequency, recency, sentiment (simple keyword-based)
- Communication preferences: per-contact tone, channel preference, timezone
- Follow-up tracker: pending conversations, last interaction, communication cadence alerts

**Schema:**
```sql
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    email TEXT, phone TEXT, organization TEXT,
    relationship TEXT, -- 'colleague', 'friend', 'client', 'family'
    communication_tone TEXT, -- 'formal', 'casual', 'technical'
    notes TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE contact_interactions (
    id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id),
    timestamp REAL NOT NULL, channel TEXT, -- 'email', 'voice', 'screen_mention', 'calendar'
    summary TEXT, sentiment TEXT, -- 'positive', 'neutral', 'negative'
    source_event_id INTEGER -- links to ContextEvent
);
CREATE TABLE contact_follow_ups (
    id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(id),
    description TEXT NOT NULL, due_date REAL,
    status TEXT DEFAULT 'pending', created_at REAL NOT NULL
);
CREATE VIRTUAL TABLE contacts_fts USING fts5(
    name, email, organization, notes, content='contacts', content_rowid='id'
);
```

**MCP tools:** `search_contacts`, `get_person_context`, `get_interaction_history`, `get_follow_ups`, `add_contact`, `add_follow_up`

**LOC:** ~1,200 | **Complexity:** Medium (entity extraction + integration adapters)

---

### 3.8 Signals (ContextPulse Signals) — Q2 2028+

**Input:** External sources (email, Slack, RSS, GitHub, AWS, market data, news)
**Output:** Filtered, Heart-weighted alerts and contextual intelligence

**Purpose:** The external antenna. Other products look inward (what you're doing). Signals looks outward (what's happening that affects you).

**Key components:**
- Integration adapters: each source implements `SignalSource` protocol (poll interval, parse, emit ContextEvent)
- Heart-weighted filtering: relevance scored against user's goals and active projects (eliminates noise)
- Project tagging: signals auto-tagged to relevant projects via keyword matching
- Alert engine: configurable thresholds for notification (desktop toast, MCP tool response, daily digest)
- Deduplication: same signal from multiple sources collapsed into single event

**Schema:**
```sql
CREATE TABLE signals (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    source TEXT NOT NULL, -- 'gmail', 'slack', 'github', 'rss', 'aws'
    signal_type TEXT, -- 'competitor_move', 'dependency_update', 'cost_alert', 'mention'
    title TEXT NOT NULL, body TEXT,
    relevance_score REAL, -- Heart-weighted 0-1
    project_tag TEXT, -- auto-linked project
    acknowledged INTEGER DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE signal_sources (
    id INTEGER PRIMARY KEY, source_type TEXT NOT NULL,
    config TEXT NOT NULL, -- JSON: poll interval, credentials ref, filters
    enabled INTEGER DEFAULT 1, last_poll REAL
);
CREATE VIRTUAL TABLE signals_fts USING fts5(
    title, body, source, project_tag, content='signals', content_rowid='id'
);
```

**MCP tools:** `get_signals`, `search_external_context`, `get_alerts`, `configure_signal_sources`, `acknowledge_signal`

**LOC:** ~2,500 | **Complexity:** High (integration adapters + filtering engine + alert system)

**Trade secrets:** Heart-weighted relevance scoring algorithm, signal deduplication heuristics, project auto-tagging model.

---

### 3.9 Cloud Sync — 2028+

Metadata-only sync (no images/audio). E2E encrypted before upload. **LOC:** ~1,500 | **Complexity:** High

---

## 4. Data Models & APIs

### 4.1 Unified SQLite Schema

**Current (Sight):**
```sql
CREATE TABLE activity (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    window_title TEXT, app_name TEXT, monitor_index INTEGER DEFAULT 0,
    frame_path TEXT, ocr_text TEXT, diff_score REAL DEFAULT 0.0
);
CREATE VIRTUAL TABLE activity_fts USING fts5(
    window_title, app_name, ocr_text, content='activity', content_rowid='id'
);
CREATE TABLE clipboard (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    text TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TABLE mcp_calls (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    tool_name TEXT NOT NULL, client_id TEXT DEFAULT 'unknown'
);
```

**Phase 2 additions (Voice):**
```sql
CREATE TABLE audio_segments (
    id INTEGER PRIMARY KEY,
    timestamp_start REAL NOT NULL, timestamp_end REAL NOT NULL,
    speaker_id TEXT, transcript TEXT, confidence REAL,
    audio_path TEXT,  -- deleted after transcription
    language TEXT DEFAULT 'en',
    nearest_frame_id INTEGER REFERENCES activity(id)
);
CREATE VIRTUAL TABLE audio_fts USING fts5(
    transcript, speaker_id, content='audio_segments', content_rowid='id'
);
```

**Phase 3 additions (Keys):**
```sql
CREATE TABLE keystroke_events (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    app_name TEXT, window_title TEXT,
    capture_mode TEXT NOT NULL,  -- 'full', 'metadata_only', 'blocked'
    key_char TEXT,               -- NULL when capture_mode != 'full'
    is_shortcut INTEGER DEFAULT 0, wpm_snapshot REAL
);
CREATE TABLE typing_sessions (
    id INTEGER PRIMARY KEY, timestamp_start REAL NOT NULL, timestamp_end REAL,
    app_name TEXT, wpm_avg REAL,
    wpm_slope REAL,  -- negative = fatigue
    error_rate REAL, keystrokes INTEGER
);
```

**Phase 4 additions (Flow):**
```sql
CREATE TABLE pointer_events (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,  -- 'click', 'scroll', 'dwell', 'drag_start', 'drag_end'
    x INTEGER, y INTEGER, app_name TEXT,
    target_element TEXT, target_control TEXT,
    dwell_duration REAL, efficiency_ratio REAL
);
CREATE TABLE attention_heatmaps (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    app_name TEXT NOT NULL, image_path TEXT NOT NULL, session_id TEXT
);
```

**Phase 5 additions (Learning Engine):**
```sql
CREATE TABLE correction_pairs (
    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL,
    source_modality TEXT NOT NULL,  -- 'voice', 'screen'
    target_modality TEXT NOT NULL,  -- 'keyboard', 'voice'
    original_text TEXT NOT NULL, corrected_text TEXT NOT NULL,
    context_window TEXT, applied INTEGER DEFAULT 0
);
CREATE TABLE vocabulary_model (
    id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, correction TEXT NOT NULL,
    confidence REAL DEFAULT 0.5, occurrence_count INTEGER DEFAULT 1, last_seen REAL
);
```

### 4.2 MCP Protocol Extensions

No transport changes. Each module adds tools via FastMCP decorators to the unified server.

**Cross-modal unified tool (Phase 5):**
```python
@mcp_app.tool()
def get_cross_modal_context(
    minutes_ago: float = 5.0,
    modalities: list[str] = None  # ["screen", "voice", "keys", "pointer"]
) -> list:
    """Unified context from all modalities at a given time."""
```

### 4.3 Inter-Module Communication

Single-process daemon. All modules are daemon threads. Shared SQLite (WAL mode) + `queue.Queue` for hot paths. No IPC framework needed through Phase 5.

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=5000")
```

Phase 6: extract DB access through repository pattern for cloud sync swap.

### 4.4 Storage Format

```
%APPDATA%/ContextPulse/
  config.json, activity.db, vocab_model.db, first_run_complete

C:\Users\{user}\screenshots\
  buffer/{ts}_m0.jpg           -- Frame, monitor 0
  buffer/{ts}_m0.txt           -- OCR sidecar JSON {text, lines, confidence}
  screen_latest.png            -- Latest quick capture (overwritten)
  screen_monitor_N.png         -- Per-monitor latest (overwritten)
  screen_region.png            -- Region capture (overwritten)
  audio/{ts}_seg.opus          -- Phase 2, deleted post-transcription
  contextpulse_sight.log
```

---

## 5. Cross-Modal Learning Engine (CRITICAL — Most Complex Component)

### 5.1 Overview

The deepest technical moat. Runs entirely on-device using lightweight statistical models (no neural network weights). Creates compounding user-specific accuracy improvements that competitors cannot replicate without years of user-specific data.

### 5.2 Correction Pair Detection

**Voice to keyboard correction detection:**

```python
def detect_voice_correction(
    transcript: str, typed_text: str, time_delta_seconds: float
) -> CorrectionPair | None:
    """
    If within TIME_WINDOW seconds after transcript, user types text where
    levenshtein_similarity(typed, transcript) is in (0.4, 0.95):
      -- related (>0.4) but meaningfully different (<0.95) = a correction
      -- typing speed indicates deliberate edit, not fast continuation
    -> Record (transcript, typed) as correction pair.

    Example: Whisper heard "context pulse", user typed "contextpulse"
    -> pair detected, vocab model updated for next transcription.
    """
    if time_delta_seconds > TIME_WINDOW:
        return None
    sim = levenshtein_similarity(transcript, typed_text)
    if 0.4 < sim < 0.95:
        return CorrectionPair(original=transcript, corrected=typed_text)
    return None
```

**Keyboard to voice vocabulary transfer:** Tokens typed >= 5 times and absent from English dictionary are injected into Whisper `initial_prompt` as vocabulary hints for the next transcription session.

### 5.3 Vocabulary Model Architecture

SQLite-backed dictionary. No neural weights. User-inspectable and deletable from settings.

- Initial confidence: 0.5
- Each repeated correction: +0.1 (cap at 0.95)
- Conflicting correction (same original, different corrected): -0.2
- Apply threshold: 0.7 (configurable)
- Expire after 30 days without reinforcement

```python
class VocabularyModel:
    def update(self, original: str, corrected: str) -> None: ...
    def get_corrections(self, text: str) -> str: ...
    def get_whisper_hotwords(self) -> list[str]: ...
    def export(self) -> dict: ...
```

### 5.4 Screen OCR as Ground Truth Validation

When transcript overlaps in time with a screen frame: check if transcript terms appear in screen OCR text. High overlap confirms transcription accuracy. Low overlap + subsequent user correction = reinforced correction pair.

### 5.5 Pointer Attention-Weighted Context Scoring

For each screen frame: sum pointer dwell durations within 30 seconds. Normalize to 0-1. Use as frame relevance weight when selecting historical context for AI agents.

MCP enhancement: `get_recent(attention_weighted=True)` ranks frames by attention rather than recency.

### 5.6 Cognitive Load Estimation

| Signal | Low Load | High Load | Weight |
|--------|---------|---------|--------|
| Typing WPM slope | Stable/increasing | Declining | 40% |
| Pointer efficiency | High straight-line ratio | Many corrections | 30% |
| Screen change rate | Steady flow | Rapid switching or long idle | 30% |

Returns score 0-100. New MCP tool: `get_cognitive_load()`. High score triggers suggestion to take a break or simplifies AI responses.

### 5.7 Training Data Pipeline

```
User events occur (screen/audio/keystroke/pointer)
    |
    v  (background, every 15 minutes)
Correction detector processes last 15 min of events
    -> Detected pair -> vocabulary_model.update()
    |
    v  (every 60 minutes)
Attention weights updated for all frames
Cognitive load estimate updated
Whisper hotwords regenerated from vocabulary_model
    |
    v  (on next voice capture)
Updated hotwords injected into Whisper initial_prompt
High-confidence corrections applied post-transcription
```

### 5.8 Privacy-Preserving Design

Learning engine never: sends data externally, stores raw audio after transcription, stores keystrokes from blocked apps, includes redacted content. Vocabulary model is user-deletable from settings panel.

---

## 6. Build Sequence

### Phase 1 — Sight + Core (COMPLETE, March 2026)

180 tests passing. Per-monitor capture, rolling buffer, OCR, redaction, smart storage (59% savings), 10 MCP tools, FTS5 activity DB, clipboard, diff-aware capture, Ed25519 licensing, settings, first-run.

**Remaining (gates Gumroad revenue):**
- [ ] Deploy Lambda infrastructure (Lambda + DynamoDB + SES) — DO THIS FIRST
- [ ] Create Gumroad product listings (Free + Pro tiers)
- [ ] End-to-end daemon testing (first-run to license to full lifecycle)
- [ ] Publish to PyPI (`contextpulse-sight`, `contextpulse-core`)

---

### Phase 2 — Voice (Q2-Q3 2026)

**Week 1-2:** Create `packages/voice/`, port Voiceasy: `recorder.py`, `transcriber.py`, `vocabulary.py`, `model_manager.py`. Adapt to contextpulse-core config.

**Week 3-4:** `silero-vad` integration, rolling 30s buffer with 5s overlap.

**Week 5-6:** Background transcription thread (queue pattern, same as OCR worker). `audio_segments` table. Temporal alignment to nearest screen frame.

**Week 7-8:** Speaker diarization (optional V1 — `pyannote.audio` or energy-based fallback).

**Week 9-10:** MCP tools + 40+ tests: `search_audio_history`, `get_recent_speech`, `get_speaker_stats`.

**Week 11-12:** Passive correction pair detection, vocabulary model storage layer, publish to PyPI.

Parallelizable: macOS port of Sight. **Estimated LOC:** ~1,200 new + ~600 ported.

---

### Phase 3 — Keys (Q4 2026)

**Week 1-2:** `packages/keys/`, extend pynput listener, per-app capture policy engine.

**Week 3-4:** Win32 UIA `IsPassword` via `comtypes`, window title blocklist, per-keystroke classification.

**Week 5-6:** WPM (rolling 1-min window), fatigue regression (`scipy.stats.linregress`, 15-min sliding), burst/pause detection, `typing_sessions` table.

**Week 7-8:** MCP tools + 40+ tests.

**Week 9-10:** Link timestamps to voice/screen frames. Enable correction pair detection (completes pipeline). Vocabulary model begins applying corrections.

**Estimated LOC:** ~900.

---

### Phase 4 — Flow (Q1 2027)

**Week 1-3:** pynput mouse listener, Win32 UIA `ElementFromPoint`, `pointer_events` table.

**Week 4-5:** Hover dwell state machine, movement efficiency (numpy), scroll reversal.

**Week 6-7:** Per-app heatmaps (Pillow Gaussian kernel), tremor detection (FFT via scipy.signal).

**Week 8-9:** MCP tools + 35+ tests.

**Estimated LOC:** ~1,000.

---

### Phase 5 — Cross-Modal Learning Engine (Q2 2027)

**Week 1-3:** Background learning thread (every 15 min), correction detection at scale, vocabulary updates, Whisper hotword injection, apply corrections post-transcription.

**Week 4-5:** Attention weighting, `attention_weighted=True` in `get_recent()`.

**Week 6-7:** Cognitive load estimator, `get_cognitive_load()` MCP tool.

**Week 8-9:** Memory module (SynapseAI journal pattern), end-of-day summarization.

**Week 10-12:** Cross-modal integration tests, performance regression, privacy audit.

**Estimated LOC:** ~2,200.

---

### Phase 6 — Heart (Q3-Q4 2027)

**Week 1-2:** `packages/heart/`, profile schema, structured profile store (mission, goals, values, domains).

**Week 3-4:** Weighting API — given a ContextEvent, return relevance score based on profile. Boundary enforcement (time blocks, app limits).

**Week 5-6:** Goal tracker — connect daily activity patterns to declared objectives. Drift detection.

**Week 7-8:** MCP tools + 20+ tests: `get_priorities`, `check_goal_alignment`, `get_life_balance`, `get_drift_report`.

**Estimated LOC:** ~400. **Highest impact-to-effort ratio in the entire roadmap.**

---

### Phase 7 — Contacts (Q1 2028)

**Week 1-3:** `packages/contacts/`, PersonEntity extractor (spaCy NER or simple regex-based), contact store, FTS5 index.

**Week 4-6:** Interaction logger — adapters for email (Gmail MCP), calendar, screen mentions (from Sight OCR), voice mentions (from Voice transcripts).

**Week 7-9:** Relationship graph, communication preferences, follow-up tracker.

**Week 10-12:** MCP tools + 30+ tests: `search_contacts`, `get_person_context`, `get_interaction_history`, `get_follow_ups`.

**Estimated LOC:** ~1,200.

---

### Phase 8 — Signals (Q2 2028+)

**Week 1-4:** `packages/signals/`, `SignalSource` protocol, integration adapters (Gmail, GitHub, RSS as initial sources).

**Week 5-7:** Heart-weighted filtering, project auto-tagging, deduplication engine.

**Week 8-10:** Alert engine (desktop toast, MCP responses, daily digest).

**Week 11-14:** Additional adapters (Slack, AWS CloudWatch, market data), 40+ tests.

**Estimated LOC:** ~2,500.

---

### Critical Path

```
Lambda Deploy --> Gumroad Revenue --> Phase 2 funding
    |
Phase 2 (Voice) --> Cross-modal groundwork
    |                      |
Phase 3 (Keys) --> Correction pairs ----+
    |                                   |
Phase 4 (Flow) ----------------------------+
                                           |
Phase 5 (Learning Engine) <---------------+
    |                                      |
Phase 6 (Heart) -- weights everything -----+
    |
Phase 7 (Contacts) -- people intelligence
    |
Phase 8 (Signals) -- external antenna
```

Parallelizable: macOS port, marketing/GTM, SOC 2 prep, memory scaffolding.

**Phase 2 is the highest-leverage next step** — most user-visible, most differentiated, foundation for cross-modal learning.

**Phase 6 (Heart) is the highest impact-to-effort ratio** — ~400 LOC that transforms the spine from data storage into an intelligent system that makes judgments.

---

## 7. Platform Strategy

### 7.1 Windows (Current)

APIs: `ctypes.windll.kernel32` (mutex), `user32` (window focus), `win32gui/win32process`, `win32clipboard` (WM_CLIPBOARDUPDATE), `comtypes` UIA (Phase 3+).

Distribution: PyInstaller EXE + Inno Setup (proven in Voiceasy).

---

### 7.2 macOS (Q3 2026)

`mss` and `pynput` are cross-platform. macOS requires Accessibility permission on first run (standard for any global hotkey app).

| Windows API | macOS Equivalent | Effort |
|------------|-----------------|--------|
| `win32gui.GetForegroundWindow()` | `NSWorkspace.sharedWorkspace().activeApplication()` | Low |
| Win32 clipboard (WM_CLIPBOARDUPDATE) | `NSPasteboard` polling or `NSDistributedNotificationCenter` | Medium |
| Win32 UIA IsPassword | macOS `AXIsPasswordField` via `pyobjc` | Medium |
| Named mutex | POSIX semaphore or lockfile | Low |

Distribution: `.dmg` via `create-dmg` + PyInstaller bundle. $99/yr Apple Developer for code signing.

**Effort:** 2-3 weeks for Sight, 1 week per additional module.

---

### 7.3 Linux (Q1 2027)

mss on X11. Wayland: subprocess `grim`/PipeWire fallback. pynput via Xlib (X11/XWayland first — Wayland needs `libinput`, high complexity). `pyatspi` for AT-SPI password detection. pystray via AppIndicator3. Distribution: AppImage.

---

### 7.4 Abstraction Layer (Build by Phase 2)

```python
# packages/core/src/contextpulse_core/platform.py

class PlatformAdapter(Protocol):
    def get_foreground_window_title(self) -> str: ...
    def get_foreground_process_name(self) -> str: ...
    def is_focused_field_password(self) -> bool: ...
    def get_cursor_position(self) -> tuple[int, int]: ...
    def get_click_target_name(self, x: int, y: int) -> str | None: ...

def get_platform_adapter() -> PlatformAdapter:
    if sys.platform == "win32":    return Win32Adapter()
    elif sys.platform == "darwin": return MacOSAdapter()
    else:                          return LinuxAdapter()
```

Migrate all Win32 calls in `privacy.py`, `events.py`, `clipboard.py` to this adapter before macOS port. ~1 week of refactoring, no functional change.

---

### 7.5 Browser Extension (2028+)

Richer in-page context than OCR alone. Architecture: JS content script -> native messaging host (Python daemon) -> unified activity DB. High complexity, low priority until core platform proven.

---

## 8. Infrastructure

### 8.1 On-Device Processing

All processing is local. No data leaves the machine.

**Thread budget (all modules active):**

| Thread | CPU | RAM | Priority |
|--------|-----|-----|---------|
| Auto-capture loop | <0.5% | 5 MB | Normal |
| OCR worker | 1-3% burst | 50 MB | Below Normal |
| Audio capture | <0.2% | 5 MB | Normal |
| Whisper transcription | 5-15% burst | 200 MB | Below Normal |
| Keystroke listener | <0.1% | 2 MB | Normal |
| Mouse listener | <0.1% | 2 MB | Normal |
| Learning engine | <1% batch | 20 MB | Idle |
| MCP server | <0.1% | 10 MB | Normal |
| **Total** | **<5% avg** | **<300 MB** | — |

Whisper dominates. Mitigated by VAD gating (60-80% trigger reduction), int8 quantization, Below Normal priority.

---

### 8.2 Cloud Sync (Phase 6, 2027+)

Images and audio never leave the device. Only metadata syncs (OCR text, window titles, transcripts, keystroke stats), E2E encrypted client-side before upload. Server is zero-knowledge.

AWS components: API Gateway + Lambda (sync), DynamoDB (per-user metadata + TTL), KMS (BYOK enterprise), CloudFront (Team dashboard).

---

### 8.3 Privacy Architecture

**Never leaves device:** Screenshot images, raw audio recordings (deleted after Whisper transcription), keystroke content from blocked apps, redacted content.

**Stored locally (deletable):** OCR text (PII redacted), transcripts, keystroke metadata (WPM/timing only in default mode), pointer events, vocabulary model.

**GDPR note:** Data never reaches Jerard Ventures servers — NOT a GDPR data processor. Eliminates DPA requirements and cross-border transfer restrictions. User is the sole data controller.

**User controls:** Per-modality enable/disable, per-app blocklist, retention sliders (per data type), "Delete all my data" (drops + recreates all tables + deletes output dir), vocabulary model export/import (JSON).

---

### 8.4 Storage Budget

| Data Type | Size/Day | 30-Day Total |
|-----------|----------|--------------|
| Screen frames (buffer, 30min TTL) | ~50 MB | ~1.5 MB |
| OCR text sidecars | ~2 MB | ~60 MB |
| Activity DB (SQLite) | ~5 MB | ~150 MB |
| Audio (deleted post-transcription) | ~100 MB | ~0 MB |
| Transcripts | ~1 MB | ~30 MB |
| Keystroke metadata | ~2 MB | ~60 MB |
| Pointer events | ~5 MB | ~150 MB |
| **Total** | **~15 MB/day** | **~450 MB** |

Auto-pruning runs nightly. Configurable retention per data type.

---

## 9. Testing Strategy

### 9.1 Per-Module Targets

- **Sight (existing):** 145 tests — capture, buffer, OCR, redaction, MCP, privacy, clipboard
- **Voice (target: 50+):** VAD accuracy, WER on standard sentences, temporal alignment +-1s, correction pair detection
- **Keys (target: 45+):** `IsPassword` mock, WPM accuracy +-5%, fatigue regression, burst/pause
- **Flow (target: 40+):** Dwell, efficiency ratio, heatmap generation, tremor FFT detection
- **Learning Engine (target: 50+):** Correction detection, vocab confidence, attention weighting, cognitive load

### 9.2 Integration Tests

| Scenario | Pass Criteria |
|---------|--------------|
| Screen + Voice temporal alignment | Frame-segment delta <= 1s |
| Voice + Keys correction pair | Pair in DB within 15min learning cycle |
| Privacy: locked screen | 0 new frames in buffer |
| Privacy: blocked app keystroke | 0 keystroke events stored |
| Full stack MCP (all 10 tools) | No exceptions, correct return types |
| Cross-modal context query | All 4 modalities present in response |

### 9.3 Performance Benchmarks

| Metric | Target | Method |
|--------|--------|--------|
| CPU (steady state, all modules) | <5% | `psutil.cpu_percent(interval=60)` |
| RAM (all modules) | <300 MB | `psutil.Process().memory_info().rss` |
| Capture latency | <10ms | `timeit` on `capture_active_monitor()` |
| Whisper latency | <3s per 30s audio | `timeit` on transcription pipeline |
| MCP tool response | <500ms | Integration test timer |

Extend `packages/screen/scripts/auto_benchmark.py` for all modules.

### 9.4 Privacy Compliance Tests

| Test | Pass Criteria |
|------|--------------|
| 10 PII patterns through OCR pipeline | 0 PII in stored text |
| `IsPassword=True` mock UIA, type, check DB | 0 keystrokes stored |
| Check audio file after transcription completes | File does not exist |
| Session lock event, then capture attempt | 0 new frames in buffer |
| Blocked app foreground, capture, check activity DB | `title == "[BLOCKED]"` |

---

## 10. Security & Privacy Architecture

**Encryption at rest:**
- Current: unencrypted SQLite
- Pro tier: `sqlcipher3` (AES-256), key from Windows Credential Manager / macOS Keychain
- Enterprise: BYOK via AWS KMS or Azure Key Vault

**Encryption in transit:**
- Current: fully local, no transmission
- Phase 6: TLS 1.3 + client-side E2E encryption (zero-knowledge server)

**Data retention defaults:**
```json
{
  "retention_days_screen": 30,
  "retention_days_audio": 0,
  "retention_days_transcripts": 30,
  "retention_days_keystrokes": 30,
  "retention_days_pointer": 30,
  "retention_days_clipboard": 7,
  "retention_days_learning": 365
}
```

**Right to erasure:** "Delete all my data" drops + recreates all SQLite tables + deletes entire output directory.

**Consent flow:** Each new modality (Voice, Keys, Flow) requires explicit opt-in. Default is off. First-run explains Sight defaults.

**Audit logging:** Current: `contextpulse_sight.log`. Phase 6 Enterprise: structured JSON (append-only, SIEM-compatible).

---

## 11. Technical Risks & Mitigations

| Risk | Probability | Impact | Key Mitigations |
|------|------------|--------|----------------|
| CPU spike (Whisper + OCR simultaneous) | High | High | VAD gating (60-80% trigger reduction), Below Normal priority, Lite mode preset, configurable interval |
| Privacy regulatory changes (keystroke capture) | Medium | Medium | Metadata-only mode is default. Full capture opt-in. Monitor ICO/CNIL quarterly. |
| Platform API changes (Win32 UIA, clipboard hooks) | Low | High | Platform adapter pattern isolates breakage. Fallback: window-title blocklist. Quarterly Windows Insider testing. |
| Learning accuracy (false-positive corrections) | Medium | Medium | 0.7 confidence threshold. User-reviewable vocab table. 30-day expiry. A/B view at 0.5-0.7 confidence. |
| Solo developer capacity | High | Medium | Phase gates (revenue funds next phase). Voice first. Keys/Flow independently shippable. $30-60K contractor budget. |

---

## 12. Technology Choices

### 12.1 Languages & Frameworks

| Choice | Decision | Why |
|--------|----------|-----|
| Python 3.14 | Keep | All code Python-native. pynput/mss/faster-whisper ecosystem. Rewrite = 6+ months. |
| mcp Python SDK (FastMCP) | Keep | 10 tools working. Handles serialization and transport automatically. |
| pynput | Keep + extend | No admin required. Proven in Voiceasy. Hotkeys + Keys + Flow share same listener. |
| mss | Keep | 3ms/frame, cross-platform, actively maintained. D3DShot abandoned and Windows-only. |
| tkinter | Keep | Zero extra deps. PyQt6 adds 30 MB to distribution. |

### 12.2 ML/AI Libraries

| Choice | Decision | Why |
|--------|----------|-----|
| faster-whisper | Add for Voice | int8 quantization, 2x faster on CPU vs openai-whisper. Proven in Voiceasy. |
| rapidocr-onnxruntime | Keep | No C++ compiler, fast (0.3s), good accuracy on code/text. |
| silero-vad | Add for Voice | 2MB ONNX, SOTA accuracy, Python-native, no internet required. |
| pyannote.audio | Optional Phase 2.1 | Best diarization. HF token for model download only; no user data upload. Skip V1. |
| scipy.stats.linregress | Add for Keys | Single-feature regression already in scipy. sklearn is overkill. |
| numpy | Add for Flow | FFT (tremor) and path geometry. Pillow pulls it in indirectly anyway. |
| Vocabulary model | SQLite (custom) | User-inspectable, no neural weights, millisecond updates, portable (one file). |

### 12.3 Storage

| Choice | Decision | Why |
|--------|----------|-----|
| SQLite + FTS5 | Keep | Zero infra, single file, WAL concurrent access, FTS5 built-in. Single-device sufficient. |
| SQLCipher | Add for Pro | AES-256 at rest, no API change required, pip installable, ~500KB overhead. |
| JPEG 85% | Keep | Best compat/quality tradeoff. WebP saves ~20% but slower Pillow encode speed. |
| Opus audio | Add for Voice | Near-lossless at 32kbps. 16x smaller than WAV. Files deleted post-transcription anyway. |

### 12.4 IPC

None needed. Single-process daemon with shared SQLite (WAL mode) and `queue.Queue` for hot paths. Sufficient through Phase 5.

Phase 6 fallback: Unix domain sockets or ZeroMQ if multi-process becomes necessary.

### 12.5 Distribution

| Platform | Method | Tooling |
|---------|--------|---------|
| Windows | Installer EXE | PyInstaller + Inno Setup (proven in Voiceasy) |
| macOS | DMG + App Bundle | PyInstaller + create-dmg |
| Linux | AppImage | PyInstaller + AppImage tooling |
| Developers | pip install | PyPI |
| AI ecosystem | JSON manifest | MCP server registry |

---

## Appendix A: LOC Summary by Phase

| Phase | Module | Est. New LOC | Cumulative |
|-------|--------|-------------|-----------|
| Complete | Sight + Core | 3,750 | 3,750 |
| Phase 2 | Voice | 1,200 | 4,950 |
| Phase 3 | Keys | 900 | 5,850 |
| Phase 4 | Flow | 1,000 | 6,850 |
| Phase 5 | Learning Engine + Memory | 2,200 | 9,050 |
| Phase 6 | Cloud + Enterprise | 1,500 | 10,550 |
| Platform | macOS + Linux adapters | 800 | 11,350 |
| **Total** | — | **~11,350** | — |

*Excludes tests (~40% of production LOC) and documentation.*

---

## Appendix B: Financial Alignment

| Phase | Ship Target | Revenue Event | Financial Milestone |
|-------|------------|--------------|---------------------|
| Sight (done) | Q1 2026 | Gumroad Pro ($29) | 500 Pro users = $14,500 (2026) |
| Voice | Q3 2026 | Cross-modal story; Voice Pro tier | 2,000 Pro users (2027) |
| Keys | Q4 2026 | Keys add-on | TAM expansion; enterprise demo-able |
| Flow | Q1 2027 | Full quad-modal press release | Team tier launch ($20/seat/mo) |
| Learning Engine | Q2 2027 | "Gets smarter over time" | Acquisition-grade moat visible |
| Cloud | 2027+ | Enterprise tier ($50K ACV) | Series A metrics ($500K–$1M ARR) |

**Break-even:** Q2 2028 at ~$285K ARR. Technical plan funds itself before Cloud/Enterprise require significant build investment.

---

*This document is CONFIDENTIAL — Jerard Ventures LLC trade secret. Do not distribute without NDA.*
