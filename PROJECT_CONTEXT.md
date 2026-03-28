# Project Context — ContextPulse

## Overview
ContextPulse is a unified always-on context platform for AI agents. One process, one tray icon — captures screen, voice, and input to give AI assistants persistent awareness of what the user sees, says, and does.

**Tagline:** "Always-on context for AI agents"
**Version:** 0.1.0
**Status:** Production-testable (unified daemon, EXE installer, 408+ tests, skills package)

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
│   ├── LocalTranscriber (faster-whisper, base model)
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
| **contextpulse-voice** | 0.1.0 | 195 | 3 | Hold-to-dictate, Whisper transcription, vocabulary |
| **contextpulse-touch** | 0.1.0 | 56 | 3 | Typing bursts, mouse events, correction detection |
| **contextpulse-project** | 0.1.0 | 38 | 5 | Project detection, journal routing |
| **contextpulse-memory** | 0.1.0 | 80 | 5 | Three-tier memory (hot/warm/cold), FTS5 search |
| **contextpulse-agent** | 0.1.0 | — | — | Coming soon (v0.2 — agent coordination) |

**Total: 408 tests (voice+core), 28 MCP tools**

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

## Next: macOS Port

`packages/core/src/contextpulse_core/platform/macos.py` — all methods raise `NotImplementedError`. Needs:
- Clipboard: `NSPasteboard`
- Window info: `NSWorkspace` + `CGWindowListCopyWindowInfo`
- Cursor: `Quartz`
- Caret: Accessibility API (`AXUIElement`)
- Session lock: `NSDistributedNotificationCenter`
- Single-instance: `fcntl.flock`

## Domain Strategy
- **Primary:** contextpulse.ai ($80/yr, Cloudflare)
- **Backup:** contextpulse.dev ($12/yr), contextpulse.io ($34/yr)
- **Bonus:** context-pulse.com ($10/yr)

## Licensing Model
- **Sight:** Always free (screen capture, OCR, clipboard, activity)
- **Memory/Agent:** Licensed (Ed25519 key verification, 7-day trial)
- **Pro tools:** search_all_events, get_event_timeline gated behind Pro tier
- **Lambda:** Gumroad webhook → Ed25519 license key → SES email delivery

## Performance Budget
| Metric | Target | Actual |
|--------|--------|--------|
| CPU | <1% | ~0.5% (idle) |
| RAM | <20 MB | ~15 MB (Sight only), ~80 MB (with Whisper model) |
| Disk writes | <2 MB/min | ~0.5 MB/min (smart mode) |
| Startup time | <2s | ~2s (Sight), ~5s (Voice model load) |
