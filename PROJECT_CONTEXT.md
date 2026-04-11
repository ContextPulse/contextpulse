# Project Context — ContextPulse

## Overview
ContextPulse is a unified always-on context platform for AI agents. One process, one tray icon — captures screen, voice, and input to give AI assistants persistent awareness of what the user sees, says, and does.

**Tagline:** "Always-on context for AI agents"
**Version:** 0.1.0
**Status:** Production-ready (open-core, AGPL-3.0) — memory module production hardened, Lambda deployed, pricing finalized

## Architecture

```
ContextPulse Daemon (single process)
├── EventBus (spine) ──── activity.db (shared SQLite + FTS5)
│   ├── events table (cross-modal, FTS-indexed)
│   └── activity/clipboard/mcp_calls tables (legacy, dual-write)
├── SightModule ── screen capture + OCR + clipboard
│   ├── Auto-capture (5s timer + event-driven)
│   ├── OCRWorker (background, smart storage)
│   ├── ClipboardMonitor (Win32 polling)
│   └── Hotkeys: Ctrl+Shift+S/A/Z/P
├── VoiceModule ── hold-to-dictate + transcribe + paste
│   ├── Recorder (sounddevice, 16kHz mono)
│   ├── LocalTranscriber (faster-whisper, small model, relaxed quality filters)
│   ├── Cleanup (rule-based + optional LLM)
│   ├── Vocabulary (user + auto-learned corrections)
│   └── Hotkeys: Ctrl+Space (dictate), Ctrl+Shift+Space (fix-last)
├── TouchModule ── keyboard + mouse capture
│   ├── KeyboardListener (typing bursts, paste detection)
│   ├── MouseListener (clicks, scrolls, drags)
│   ├── BurstTracker (WPM, backspace ratio)
│   └── CorrectionDetector + VoiceasyBridge
├── Watchdog ── restarts Voice/Touch on crash (3 retries)
├── Crash Reporter ── contextpulse_crash.log
└── System Tray ── green=active, yellow=paused
```

## Packages

| Package | Version | Tests | MCP Tools | Status |
|---------|---------|-------|-----------|--------|
| **contextpulse-core** | 0.1.0 | 151 | — | Spine, config, licensing, settings, GUI theme |
| **contextpulse-sight** | 0.1.0 | 283 | 12 (10 free + 2 Pro) | Screen capture, OCR, clipboard, activity DB |
| **contextpulse-voice** | 0.1.0 | 244 | 3 | Hold-to-dictate, Whisper transcription, vocabulary |
| **contextpulse-touch** | 0.1.0 | 56 | 3 | Typing bursts, mouse events, correction detection |
| **contextpulse-project** | 0.1.0 | 38 | 5 | Project detection, journal routing |
| **contextpulse-memory** | 0.1.0 | 80 | 7 (5 free + 2 Pro) | Three-tier memory (hot/warm/cold), FTS5+semantic search, quota cap |
| **contextpulse-agent** | 0.1.0 | — | — | Coming soon (v0.2 — agent coordination) |

**Total: 1,052 tests across all packages, 30 MCP tools (25 free + 5 Pro) + 4 voice tools = 34 total**

### Test Infrastructure (added 2026-04-10)
- **Integration tests:** `tests/integration/` — thread safety, GUI survival, daemon lifecycle (63 new tests)
- **Dev deps:** pytest-timeout (30s default), pytest-threadleak, hypothesis
- **Markers:** unit, integration, property, slow, windows_only
- **CI:** Separate integration test job on Windows, property test job
- **Model coupling:** `_MODEL_THRESHOLDS` with monotonicity invariant tests
- **Skill:** `robust-testing` — patterns for thread/GUI/daemon/hotkey/audio/parameter testing

## Memory Module (contextpulse-memory) — Production Hardened 2026-03-30
- Free CRUD tools: memory_store, memory_recall, memory_list, memory_forget, memory_stats
- Pro search tools: memory_search (hybrid/keyword/semantic), memory_semantic_search
- MiniLM ONNX model pinned to HuggingFace commit 10244843, SHA-256 checksums filled
- Hourly maintenance thread: prune expired + PRAGMA optimize (warm + cold tiers)
- Quota cap: DEFAULT_MAX_WARM_ENTRIES=50_000 (~150 MB), raises MemoryQuotaExceeded
- Lambda deployed to AWS with Pro-only tier (no Starter paid tier)

## MCP Tools

### Sight (Free)
| Tool | Description |
|------|-------------|
| get_screenshot | Capture screen (active/all/monitor/region) |
| get_recent | Recent frames from rolling buffer |
| get_screen_text | OCR current screen at full resolution |
| get_buffer_status | Daemon health + token cost estimates |
| get_activity_summary | App usage over last N hours |
| search_history | FTS5 search across window titles + OCR |
| get_context_at | Frame + metadata from N minutes ago |
| get_clipboard_history | Recent clipboard entries |
| search_clipboard | Search clipboard by text |
| get_agent_stats | MCP client usage stats |

### Sight (Pro-gated)
| Tool | Description |
|------|-------------|
| search_all_events | Cross-modal FTS search (screen + voice + clipboard + keys) |
| get_event_timeline | Temporal view of all events across modalities |

### Voice
| Tool | Description |
|------|-------------|
| get_recent_transcriptions | Recent voice dictation history |
| get_voice_stats | Dictation count, duration, accuracy |
| get_vocabulary | Current word corrections |
| learn_from_session | Batch-analyze transcription history, auto-write corrections |
| rebuild_context_vocabulary | Regenerate context vocab from projects + skills dirs |
| consolidate_learning | Full cross-modal consolidation pipeline (session+OCR+clipboard+escalation) |
| check_corrections | Find repeated touch corrections for vocabulary promotion |

### Touch
| Tool | Description |
|------|-------------|
| get_recent_touch_events | Typing bursts, clicks, scrolls |
| get_touch_stats | Keystroke count, WPM, corrections |
| get_correction_history | Voice dictation corrections detected |

### Project
| Tool | Description |
|------|-------------|
| identify_project | Score text against all projects |
| get_active_project | Detect project from CWD/window title |
| list_projects | All indexed projects |
| get_project_context | Full PROJECT_CONTEXT.md for a project |
| route_to_journal | Route insight to project journal |

## Build & Deployment

### Development (current)
```bash
# Run from venv (editable install)
pythonw.exe -m contextpulse_core.daemon

# Auto-starts on login via Startup folder
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ContextPulse.cmd
```

### EXE Build
```bash
# PyInstaller → dist3/ContextPulse/ContextPulse.exe (386MB)
python -m PyInstaller contextpulse.spec --noconfirm

# Inno Setup → installer_output/ContextPulseSetup-0.1.0.exe (110MB)
ISCC.exe installer.iss

# Or one-click:
build.cmd
```

### MCP Server Processes (separate from daemon, stdio transport)
```
contextpulse-sight-mcp    # Read-only queries on activity.db
contextpulse-voice-mcp    # Read-only voice analytics
contextpulse-touch-mcp    # Read-only touch analytics
```

## Key Files

| File | Purpose |
|------|---------|
| `packages/core/src/contextpulse_core/daemon.py` | Unified launcher — starts all modules |
| `packages/core/src/contextpulse_core/settings.py` | Settings UI (capture, hotkeys, voice, touch, privacy, license) |
| `packages/core/src/contextpulse_core/spine/bus.py` | EventBus — SQLite + FTS5 event storage |
| `packages/screen/src/contextpulse_sight/app.py` | Sight app — capture loop, tray, hotkeys |
| `packages/screen/src/contextpulse_sight/mcp_server.py` | Sight MCP tools (12 tools) |
| `packages/voice/src/contextpulse_voice/voice_module.py` | Voice — hotkey, record, transcribe, paste |
| `packages/touch/src/contextpulse_touch/touch_module.py` | Touch — keyboard + mouse capture |
| `contextpulse.spec` | PyInstaller build config |
| `installer.iss` | Inno Setup installer script |
| `build.cmd` | One-click build script |

## Companion Skills (bundled via `contextpulse --setup`)

| Skill | Installs to | Purpose |
|-------|-------------|---------|
| `using-contextpulse` | `~/.claude/skills/`, `~/.gemini/skills/` | Full Sight+Voice+Touch tool reference with fallback and daemon restart |
| `analyzing-dictation` | `~/.claude/skills/`, `~/.gemini/skills/` | Voice vocabulary analysis workflow + safety rules |

`using-contextpulse-sight` (old sight-only skill) is deprecated — replaced by `using-contextpulse`.

## Voice Self-Learning Architecture

Three-layer vocabulary system (priority: user > learned > context):
1. **Context vocab** (`vocabulary_context.json`): Auto-rebuilt from `~/Projects/*/PROJECT_CONTEXT.md` + `~/.claude/skills/*/SKILL.md` → 72 entries
2. **Learned vocab** (`vocabulary_learned.json`): Written by `session_learner.py` (end-of-session batch) + `_harvest_screen_corrections()` (OCR, per-dictation)
3. **User vocab** (`vocabulary.json`): Hand-edited, highest priority

LLM cleanup uses recent window titles (last 2 min from Sight events) as proper noun context hints — not current window (always "Claude" during dictation).

## macOS Port — Complete

All platform methods fully implemented in `packages/core/src/contextpulse_core/platform/macos.py`:
- Clipboard: `NSPasteboard` ✅
- Window info: `NSWorkspace` + `CGWindowListCopyWindowInfo` ✅
- Cursor: `Quartz` (`CGEventCreate` + `CGEventGetLocation`) ✅
- Caret: Accessibility API (`AXUIElement`) ✅
- Session lock: `NSDistributedNotificationCenter` ✅
- Single-instance: `fcntl.flock` ✅
- TCC permissions: `macos_permissions.py` (Screen Recording, Accessibility, Input Monitoring, Microphone) ✅
- Menu bar: `tray_macos.py` via rumps ✅
- OCR: Native Apple Vision framework via `ocr_macos.py` ✅
- Transcription: mlx-whisper for Apple Silicon, faster-whisper fallback for Intel ✅
- PyInstaller: `packaging/macos/contextpulse_macos.spec` ✅

## Domain
- **Primary:** contextpulse.ai

## Monetization Backend — Complete (2026-04-06)

Key discovery: significant monetization infrastructure already existed before this build session. New additions layer on top cleanly.

### Key Files
- `packages/core/src/contextpulse_core/licensing.py` — Public API wrapper: `LicenseTier` enum, `LicenseInfo` dataclass, `verify_license(key, pem)`, `verify_license_embedded()`, `is_pro_feature_enabled()` (reads pro_features.yaml), 1-hour in-memory cache
- `config/pro_features.yaml` — Config-driven Pro gate list (cloud_memory, advanced_screen_capture, voice_transcription_pro + stubs)
- Backend infra (Lambda, DynamoDB, deploy scripts) kept in a private repo

## Licensing Model

ContextPulse is open source (open-core model). License: AGPL-3.0-or-later.

- **Community edition (free):** All core tools — Sight capture/OCR/clipboard, Voice dictation, Touch analytics, Project routing, Memory CRUD (store/recall/list/forget/stats)
- **Pro ($49/yr or $249 lifetime):** Advanced features requiring more compute — semantic/hybrid memory search, cross-modal analytics, priority support
- **Pro tools:** memory_search, memory_semantic_search, search_all_events, get_event_timeline
- **License delivery:** Gumroad webhook -> Ed25519 license key -> SES email
- **No Starter paid tier** — Community is fully functional, Pro adds advanced capabilities

## Performance Budget
| Metric | Target | Actual |
|--------|--------|--------|
| CPU | <1% | ~0.5% (idle) |
| RAM | <20 MB | ~15 MB (Sight only), ~80 MB (with Whisper model) |
| Disk writes | <2 MB/min | ~0.5 MB/min (smart mode) |
| Startup time | <2s | ~2s (Sight), ~5s (Voice model load) |
