<p align="center">
  <img src="site/contextpulse-logo.svg" alt="ContextPulse" width="80" />
</p>

<h1 align="center">ContextPulse</h1>

<p align="center">
  <strong>Always-on context for AI agents.</strong><br>
  Give your AI assistants eyes, ears, and memory — locally, privately, via MCP.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="AGPL-3.0" /></a>
  <img src="https://img.shields.io/badge/python-3.14+-3776AB.svg" alt="Python 3.14+" />
  <img src="https://img.shields.io/badge/platform-Windows-0078D6.svg" alt="Windows" />
  <img src="https://img.shields.io/badge/MCP-native-orange.svg" alt="MCP Native" />
  <img src="https://img.shields.io/badge/tests-798-brightgreen.svg" alt="798 tests" />
</p>

---

ContextPulse is a desktop daemon that captures your screen, voice, and keyboard/mouse activity in real time, then delivers it to AI agents through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). One process, one tray icon, 23 MCP tools, zero cloud dependency.

```
┌─────────────────────────────────────────────────┐
│              ContextPulse Daemon                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │  Sight   │  │  Voice   │  │  Touch   │      │
│  │ Screen   │  │ Dictate  │  │ Keys +   │      │
│  │ OCR      │  │ Whisper  │  │ Mouse    │      │
│  │ Clipboard│  │ Vocab    │  │ Bursts   │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       └──────────────┼──────────────┘            │
│                      ▼                           │
│              ┌──────────────┐                    │
│              │  EventBus    │                    │
│              │  (Spine)     │                    │
│              └──────┬───────┘                    │
│                     ▼                            │
│              ┌──────────────┐                    │
│              │ activity.db  │                    │
│              │ SQLite+FTS5  │                    │
│              └──────────────┘                    │
└────────────────────┬────────────────────────────┘
                     │ MCP (stdio)
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   Claude Code    Cursor      Any MCP
                              Client
```

## Why ContextPulse?

AI coding assistants are powerful but blind. They can't see your screen, hear your voice notes, or know what you were just doing. ContextPulse bridges this gap:

- **Screen awareness** — your AI knows what app you're in, what's on screen, what you just copied
- **Voice input** — hold a key, speak, release. Transcribed and pasted instantly. Your AI can read the transcription history.
- **Activity patterns** — typing speed, app switching, focus time. Your AI understands your work rhythm.
- **Cross-modal search** — "what was I looking at when I said that?" queries across all modalities *(Pro)*

Everything stays local. No cloud. No telemetry. Your data never leaves your machine.

## Quick Start

```bash
pip install contextpulse-core contextpulse-sight contextpulse-voice contextpulse-touch
```

Add to your Claude Code MCP config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "contextpulse-sight": {
      "command": "python",
      "args": ["-m", "contextpulse_sight.mcp_server"]
    },
    "contextpulse-voice": {
      "command": "python",
      "args": ["-m", "contextpulse_voice.mcp_server"]
    },
    "contextpulse-touch": {
      "command": "python",
      "args": ["-m", "contextpulse_touch.mcp_server"]
    }
  }
}
```

Start the daemon:

```bash
python -m contextpulse_core.daemon
```

That's it. Your AI agent now has 23 tools for reading your screen, voice, and activity.

## MCP Tools

### Sight (10 free tools)

| Tool | What it does |
|------|-------------|
| `get_screenshot` | Capture screen — active monitor, all monitors, or a region |
| `get_recent` | Recent frames from the rolling buffer (with diff filtering) |
| `get_screen_text` | OCR the current screen at native resolution |
| `get_buffer_status` | Daemon health check + buffer stats |
| `get_activity_summary` | App usage breakdown over last N hours |
| `search_history` | Full-text search across window titles + OCR text |
| `get_context_at` | Frame + metadata from N minutes ago |
| `get_clipboard_history` | Recent clipboard entries |
| `search_clipboard` | Search clipboard by text content |
| `get_agent_stats` | Which MCP clients are consuming context, and how often |

### Voice (3 free tools)

| Tool | What it does |
|------|-------------|
| `get_recent_transcriptions` | Recent voice dictation history (raw + cleaned) |
| `get_voice_stats` | Dictation count, duration, accuracy stats |
| `get_vocabulary` | Current word correction entries |

### Touch (3 free tools)

| Tool | What it does |
|------|-------------|
| `get_recent_touch_events` | Typing bursts, clicks, scrolls, drags |
| `get_touch_stats` | Keystroke count, WPM, click/scroll totals |
| `get_correction_history` | Voice-to-typing correction detections |

### Project (5 free tools)

| Tool | What it does |
|------|-------------|
| `identify_project` | Score text against all projects, return best match |
| `get_active_project` | Detect current project from CWD or window title |
| `list_projects` | All indexed projects with overviews |
| `get_project_context` | Full PROJECT_CONTEXT.md for a project |
| `route_to_journal` | Route an insight to the project journal |

### Pro (2 tools — requires license)

| Tool | What it does |
|------|-------------|
| `search_all_events` | Cross-modal full-text search across screen, voice, clipboard, keys |
| `get_event_timeline` | Temporal view of all events across all modalities |

## Pro Tier

ContextPulse is free and open-source. The Pro tier adds cross-modal intelligence for power users:

- **Cross-modal search** — "find when I was looking at that error while dictating notes"
- **Event timeline** — unified view of everything that happened across all input channels
- 7-day free trial included

[Get ContextPulse Pro &rarr;](https://contextpulse.ai)

## Architecture

ContextPulse is a monorepo with 7 packages:

| Package | Purpose |
|---------|---------|
| `contextpulse-core` | Daemon, EventBus (spine), config, licensing, settings |
| `contextpulse-sight` | Screen capture, OCR, clipboard monitoring |
| `contextpulse-voice` | Hold-to-dictate, Whisper transcription, vocabulary |
| `contextpulse-touch` | Keyboard/mouse activity capture, correction detection |
| `contextpulse-project` | Project detection and journal routing |
| `contextpulse-memory` | Cross-session context persistence *(planned)* |
| `contextpulse-agent` | Agent coordination *(planned)* |

All modules emit events to a shared **EventBus** (the "spine"), which writes to a local SQLite database with FTS5 full-text search. MCP servers are read-only processes that query this database.

## Development

```bash
git clone https://github.com/junkyard-rules/contextpulse.git
cd contextpulse
python -m venv .venv
.venv\Scripts\activate
pip install -e packages/core -e packages/screen -e packages/voice -e packages/touch -e packages/project
pytest tests/ -x -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

ContextPulse is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

**What this means:**
- You can use, modify, and distribute ContextPulse freely
- If you modify and deploy it as a service, you must open-source your changes
- Commercial licensing is available for embedding in proprietary products

For commercial licensing inquiries: david@jerardventures.com

## Patent Notice

ContextPulse's unified multi-modal context delivery system is patent pending.

---

<p align="center">
  Built by <a href="https://jerardventures.com">Jerard Ventures LLC</a>
</p>
