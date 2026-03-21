# Project Context — ContextPulse

## Overview
ContextPulse is a platform that provides always-on context for AI agents. Screen capture is product #1 ("visual context"), but the brand scales to memory context, agent context, project context, and beyond.

**Tagline:** "Always-on context for AI agents"

## Goals
- Replace the slow Snip Tool workflow for AI assistants seeing the desktop
- AI assistants can "see" the desktop at any time via MCP tool call or file read
- Cross-session memory so agents don't start from zero each conversation
- Agent coordination — shared context between Claude Code, Gemini CLI, etc.
- Privacy-first: blocklist sensitive windows, pause hotkey, auto-pause on lock
- Smart storage: text-only when text-heavy, image+text for visual content
- Activity tracking: searchable history of what apps/windows were used

## Product Suite

| Package | Description | Status |
|---------|-------------|--------|
| **contextpulse-sight** | Always-on screen capture + MCP server | Phase 2.0 + Security: 118 tests, 7 MCP tools, activity DB, smart storage, OCR redaction, MCP hardening |
| **contextpulse-core** | Shared config, licensing, settings, GUI theme | Productized: 35 tests, persistent config, Ed25519 licensing, settings panel, first-run |
| **contextpulse-memory** | Cross-session persistent memory (journal pattern) | Planned — from SynapseAI |
| **contextpulse-agent** | Agent coordination, session protocol | Planned — from SynapseAI |
| **contextpulse-project** | Auto-generated project context | Planned |

## Tech Stack
- **Language:** Python 3.14
- **Screen capture:** mss (3ms/frame, zero deps, DPI-aware, multi-monitor)
- **Hotkeys:** pynput (no admin, proven in Voiceasy)
- **System tray:** pystray + Pillow (proven in Voiceasy)
- **Image processing:** Pillow (resize, compress, format conversion)
- **OCR:** rapidocr-onnxruntime (on-demand text extraction)
- **MCP server:** mcp Python SDK (stdio transport)
- **Database:** SQLite + FTS5 (activity tracking, full-text search)
- **Config:** python-dotenv + JSON persistent config (%APPDATA%/ContextPulse/config.json)
- **Licensing:** Ed25519 signature verification (PyNaCl)
- **GUI:** tkinter (settings panel, first-run dialog, license nag)
- **Lambda:** Gumroad webhook → Ed25519 license key → SES email delivery

## Architecture
```
ContextPulse/
├── pyproject.toml              # Workspace root
├── packages/
│   ├── core/                   # Shared config, licensing, settings, GUI
│   │   └── src/contextpulse_core/
│   │       ├── config.py        # Persistent JSON config with env var fallback
│   │       ├── license.py       # Ed25519 license verification (tiers, expiration)
│   │       ├── license_dialog.py # Nag dialog for unlicensed/expired Memory
│   │       ├── gui_theme.py     # Singleton tkinter root, brand colors, dialog factory
│   │       ├── settings.py      # Full settings panel (capture, hotkeys, privacy, license)
│   │       └── first_run.py     # Welcome dialog with hotkey reference
│   │   └── tests/              # 35 tests (config + license)
│   ├── screen/                 # Screen capture daemon + MCP
│   │   └── src/contextpulse_sight/
│   │       ├── app.py          # Main daemon: tray + hotkeys + auto-capture + activity
│   │       ├── capture.py      # mss wrapper: per-monitor capture, region crop
│   │       ├── buffer.py       # Rolling buffer with per-monitor change detection
│   │       ├── classifier.py   # OCR-based text/image classification
│   │       ├── mcp_server.py   # MCP stdio server (7 tools)
│   │       ├── config.py       # Env var loading (20+ settings)
│   │       ├── privacy.py      # Window blocklist + session lock + process name
│   │       ├── events.py       # Event-driven capture (window focus, idle, monitor cross)
│   │       ├── activity.py     # SQLite activity DB with FTS5 search
│   │       ├── ocr_worker.py   # Background OCR processing with smart storage
│   │       ├── icon.py         # System tray icon generation
│   │       └── redact.py       # OCR text redaction (API keys, tokens, passwords)
│   │   └── tests/              # 118 tests across 10 test files
│   │   └── scripts/
│   │       ├── auto_benchmark.py  # Automated storage mode benchmarking
│   │       └── benchmark_storage.py  # Manual capture benchmarking
│   ├── memory/                 # Cross-session memory (future)
│   ├── agent/                  # Agent coordination (future)
│   └── project/                # Project context (future)
├── lambda/
│   ├── license_webhook.py      # Gumroad webhook → Ed25519 license key → SES
│   └── DEPLOY_NOTES.md         # AWS deployment instructions
├── docs/
│   ├── NAMING.md               # Brand naming research
│   └── DOMAINS.md              # Domain registrations, pricing, DNS strategy
├── benchmark_results/          # Auto-benchmark JSON output
```

## Screen Capture Modes

| Mode | Hotkey | What It Captures | Output |
|------|--------|-----------------|--------|
| Quick shot | Ctrl+Shift+S | Active monitor (where cursor is) | screen_latest.png |
| All monitors | Ctrl+Shift+A | Each monitor separately | screen_monitor_0.png, screen_monitor_1.png |
| Cursor region | Ctrl+Shift+Z | 800x600 crop centered on cursor | screen_region.png |
| Auto-capture | Timer (5s) + events | All monitors individually | Rolling buffer + activity DB |

All files written to `C:\Users\david\screenshots\`.

## MCP Server Tools

| Tool | Description |
|------|-------------|
| get_screenshot(mode, monitor_index) | Capture screen (active/all/monitor/region) |
| get_recent(count, seconds) | Recent frames from rolling buffer |
| get_screen_text() | OCR current screen at full resolution |
| get_buffer_status() | Check daemon/buffer health + monitor count |
| get_activity_summary(hours) | App usage distribution over last N hours |
| search_history(query, minutes_ago) | FTS5 search across window titles + OCR text |
| get_context_at(minutes_ago) | Frame + metadata from N minutes ago |

## Storage Modes

| Mode | Env Var | Behavior |
|------|---------|----------|
| smart (default) | `CONTEXTPULSE_STORAGE_MODE=smart` | Always store text; delete image only if text-heavy |
| visual | `=visual` | Images only, no OCR |
| both | `=both` | Always keep image AND text |
| text | `=text` | Always try text-only, image only if OCR fails |

App-level override: `CONTEXTPULSE_ALWAYS_BOTH=thinkorswim.exe` forces both mode for specific apps (charts, design tools).

**Benchmark results (2026-03-21):** 45% of captures are text-only eligible, saving ~59% disk. Thresholds (100 chars, 0.70 confidence) correctly classify websites as text and Google Maps/File Explorer as visual.

## Event-Driven Capture
Polls at 2Hz (configurable) for:
- Window focus changes (title differs from cached)
- Monitor boundary crosses (cursor moves between monitors)
- Activity after idle (>30s quiet, then movement)

Events trigger immediate capture, replacing the next timer tick. Keeps CPU flat.

## Domain Strategy
- **Primary:** contextpulse.ai — REGISTERED ($80/yr, Cloudflare)
- **Backup:** contextpulse.dev ($12/yr), contextpulse.io ($34/yr) — REGISTERED (Cloudflare)
- **Bonus:** context-pulse.com ($10/yr) — REGISTERED (Cloudflare)

## Build Phases (Screen Package)

### Phase 1: Core Capture + Hotkeys (MVP) — COMPLETE
- [x] All items from Phase 1 (see git history)
- [x] 73 tests passing

### Phase 1.5: Ship & Publish — COMPLETE
- [x] UAT script (44 tests), MCP configured, skill created
- [x] Daemon running with Windows Startup auto-launch
- [x] Watchdog thread + backoff on failures
- [x] GitHub repo pushed (junkyard-rules/contextpulse, private)

### Phase 2.0: Visual Memory System — COMPLETE (2026-03-21)
- [x] Per-monitor capture (replaces stitched all-monitors image)
- [x] capture_single_monitor(index), get_monitor_count()
- [x] get_screenshot(mode="monitor", monitor_index=N)
- [x] Buffer filename format: {timestamp}_m{monitor_index}.jpg
- [x] Per-monitor change detection
- [x] Buffer retention: 3 min → 30 min
- [x] JPEG quality: 85 → 75 for buffer frames
- [x] Event-driven capture (window focus, idle, monitor cross)
- [x] Activity database (SQLite + FTS5, app_name + window_title tracking)
- [x] Background OCR pipeline (queue-based, non-blocking)
- [x] Smart storage modes (smart/visual/both/text)
- [x] App-level "always both" override (thinkorswim.exe)
- [x] 3 new MCP tools (activity_summary, search_history, get_context_at)
- [x] get_foreground_process_name() via Win32 API
- [x] Automated benchmark script (auto_benchmark.py)
- [x] 118 tests passing (up from 73)
- [ ] Restart daemon with new code
- [ ] Publish to PyPI

### Phase 2.5: Productization — COMPLETE (2026-03-21)
- [x] gui_theme.py: singleton tkinter root, brand colors, dialog factory
- [x] config.py: persistent JSON config at %APPDATA%/ContextPulse with env var fallback
- [x] license.py: Ed25519 license verification with tiers (starter/pro) and expiration
- [x] license_dialog.py: nag dialog for unlicensed/expired Memory users
- [x] first_run.py: welcome dialog with hotkey reference
- [x] settings.py: full settings panel (capture, hotkeys, privacy, license status)
- [x] redact.py: OCR text redaction for secrets (opt-in/out)
- [x] privacy.py: default blocklist for sensitive apps (1Password, KeePass, etc.)
- [x] lambda/license_webhook.py: Gumroad webhook → Ed25519 key → SES email
- [x] app.py: settings/license wired into tray menu, first-run check
- [x] MCP server: input validation, path traversal protection, rate limiting
- [x] 153 tests passing (118 screen + 35 core)
- [ ] Deploy Lambda infrastructure (Lambda, DynamoDB, SES, SSM)
- [ ] Create Gumroad product listings for Memory tiers
- [ ] End-to-end daemon testing (first-run, settings, blocklist, license)
- [ ] Add redact_ocr_text boolean to config defaults (opt-in/out toggle)

### Phase 3: TradingCoPilot Integration — PLANNED
- [ ] Integrate ContextPulse Sight into TradingCoPilot
- [ ] Auto-capture thinkorswim charts with OCR metadata
- [ ] Correlate trade timestamps with chart screenshots
- [ ] Visual pattern analysis + numerical indicator data

### Phase 4: Memory Package — PLANNED
- [ ] Port SynapseAI journal pattern
- [ ] Cross-session context persistence

## Performance Budget
| Metric | Target |
|--------|--------|
| CPU | <1% |
| RAM | <20 MB |
| Disk writes | <2 MB/min |
| Startup time | <2s |

## Key Docs
- `docs/ecosystem-roadmap.md` — unified product roadmap with revenue milestones
- `docs/elevator-pitch.md` — ContextPulse ecosystem pitch
- `docs/market-research.md` — competitive landscape and demand validation
- `brand/BRAND.md` — brand guide, product suite naming
