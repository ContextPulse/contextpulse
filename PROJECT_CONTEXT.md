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

## Product Suite

| Package | Description | Status |
|---------|-------------|--------|
| **contextpulse-screen** | Always-on screen capture + MCP server | Phase 1 MVP in progress |
| **contextpulse-core** | Shared config, utilities | Scaffolded |
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
- **Config:** python-dotenv

## Architecture
```
ContextPulse/
├── pyproject.toml              # Workspace root
├── packages/
│   ├── core/                   # Shared utilities
│   │   └── src/contextpulse_core/
│   ├── screen/                 # Screen capture daemon + MCP
│   │   └── src/contextpulse_screen/
│   │       ├── app.py          # Main daemon: tray + hotkeys + auto-capture
│   │       ├── capture.py      # mss wrapper: monitor detection, region crop
│   │       ├── buffer.py       # Rolling buffer with change detection
│   │       ├── classifier.py   # OCR-based text/image classification
│   │       ├── mcp_server.py   # MCP stdio server
│   │       ├── config.py       # Env var loading
│   │       └── icon.py         # System tray icon generation
│   ├── memory/                 # Cross-session memory (future)
│   │   └── src/contextpulse_memory/
│   ├── agent/                  # Agent coordination (future)
│   │   └── src/contextpulse_agent/
│   └── project/                # Project context (future)
│       └── src/contextpulse_project/
├── docs/
│   ├── NAMING.md               # Brand naming research (80+ candidates evaluated)
│   └── DOMAINS.md              # Domain registrations, pricing, DNS strategy
└── tests/                      # Per-package test suites
```

## Screen Capture Modes

| Mode | Hotkey | What It Captures | File Output |
|------|--------|-----------------|-------------|
| Quick shot | Ctrl+Shift+S | Active monitor (where cursor is) | screen_latest.png |
| All monitors | Ctrl+Shift+A | Stitched panorama of both displays | screen_all.png |
| Cursor region | Ctrl+Shift+Z | 800x600 crop centered on cursor | screen_region.png |
| Auto-capture | Timer (default 5s) | Active monitor | Rolling buffer only |

All files written to `C:\Users\david\screenshots\`.

## MCP Server Tools

| Tool | Description |
|------|-------------|
| get_screenshot(mode) | Capture current screen (active/all/region) |
| get_recent(count, seconds) | Recent frames from rolling buffer |
| get_screen_text() | OCR current screen at full resolution |
| get_buffer_status() | Check daemon/buffer health |

## Domain Strategy
- **Primary:** contextpulse.ai — REGISTERED ($80/yr, Cloudflare)
- **Backup:** contextpulse.dev ($12/yr), contextpulse.io ($34/yr) — REGISTERED (Cloudflare)
- **Bonus:** context-pulse.com ($10/yr) — REGISTERED (Cloudflare)
- **contextpulse.com:** GoDaddy squatter, negotiate later
- **Registrar:** Cloudflare (Account 520086e741f5447328d166067320183b)
- Full naming research: `docs/NAMING.md`, domain details: `docs/DOMAINS.md`

## Origin
- Screen capture component evolved from **ScreenContext** (this repo, renamed)
- Memory/agent components draw from **SynapseAI** concepts (productized AI assistant setup)
- SynapseAI's journal pattern, session protocol, and orchestration map to contextpulse-memory and contextpulse-agent

## Build Phases (Screen Package)

### Phase 1: Core Capture + Hotkeys (MVP) — IN PROGRESS
- [x] Project scaffold
- [x] capture.py — mss wrapper with monitor detection, cursor tracking, region crop
- [x] app.py — pynput hotkeys + pystray tray icon
- [x] Three hotkey modes: quick shot, all monitors, cursor region
- [x] Image downscale to 1280x720 + JPEG 85%
- [x] Auto-capture with rolling buffer + change detection
- [x] OCR classifier for text-heavy screens
- [x] MCP server with 4 tools
- [x] Code review + 6 critical bug fixes + 7 warnings resolved
- [x] Unit test suite (50 tests, all passing, 0.36s)
- [x] Domain registration (contextpulse.ai, .dev, .io, context-pulse.com)
- [x] Python 3.14 venv created
- [ ] End-to-end test: daemon + MCP in same session
- [ ] Privacy controls (blocklist, auto-pause on lock)

### Phase 2: Memory Package — PLANNED
- [ ] Port SynapseAI journal pattern
- [ ] Cross-session context persistence
- [ ] MCP tools for memory read/write

### Phase 3: Agent Package — PLANNED
- [ ] Session protocol extraction from SynapseAI
- [ ] Agent coordination primitives
- [ ] Multi-agent context sharing

## Performance Budget
| Metric | Target |
|--------|--------|
| CPU | <1% |
| RAM | <20 MB |
| Disk writes | <2 MB/min |
| Startup time | <2s |
