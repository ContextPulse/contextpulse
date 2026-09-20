<p align="center">
  <img src="logo.png" alt="ContextPulse" width="80" />
</p>

<h1 align="center">ContextPulse</h1>

<p align="center">
  <strong>Local-first ambient context for AI agents.</strong><br>
  Screen capture, voice dictation, clipboard, keyboard/mouse activity. Captured locally, with captured text redacted before it is stored.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="AGPL-3.0" /></a>
  <img src="https://img.shields.io/badge/python-3.12+-3776AB.svg" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-0078D6.svg" alt="Windows | macOS" />
  <img src="https://img.shields.io/badge/MCP-native-orange.svg" alt="MCP Native" />
</p>

---

> **Developer Preview (v0.1-alpha).** ContextPulse is under active development. APIs and configuration may change between releases. [Report issues](https://github.com/ContextPulse/contextpulse/issues).

ContextPulse is a desktop daemon that captures your screen, voice, and keyboard/mouse activity in real time, then delivers it to AI agents through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). One tray-icon daemon does the capture, a companion MCP process serves it to your agent. 37 MCP tools, zero cloud dependency.

Capture, storage and search all run on your machine, and there is no telemetry. ContextPulse sends nothing off the machine until you switch on one of three network features, each off by default:

- Semantic memory search downloads its embedding model from huggingface.co the first time you use it. One file, once, then it runs offline.
- Voice LLM cleanup sends the text of a dictation to Anthropic's API to fix grammar and strip filler words. It needs an API key in `voice_anthropic_api_key` (or `ANTHROPIC_API_KEY`) and `voice_always_use_llm` turned on. The same key lets the vocabulary learner send recent transcript pairs to Anthropic to find words that speech recognition keeps mishearing.
- The fact consolidator, `scripts/probe_consolidator.py`, pipes recent captured events to the Claude CLI to distill them into facts. That prompt carries app names, window titles and the captured text itself. It runs only when you run the script or schedule it.

Captured text is redacted before it is stored and again before any MCP tool returns it. Rows written before 0.1.1 are redacted in place by a one-time sweep on the first daemon or MCP-server start after upgrade; `scripts/purge_clipboard_secrets.py` is there if you want to run it by hand. See [SECURITY.md](SECURITY.md) for what the filter covers and what it does not.

**A note on MCP clients.** Once your agent reads a tool result, what happens to it next is that client's decision, not ours. A local model keeps it on the machine. A cloud-backed client sends it to its provider like any other prompt. That applies to every context tool you give an agent, and ContextPulse cannot see or control the hop.

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
                     │ MCP (streamable-http :8420)
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   Claude Code    Cursor      Any MCP
                              Client
```

## Quick Demo

```
> You: "What am I looking at right now?"

Claude calls get_screenshot → get_screen_text:

  Active window: VS Code, src/auth/middleware.ts
  OCR text: "export async function validateToken(req: Request)..."
  Clipboard: "[REDACTED:BEARER]"

> Claude: "You're editing the auth middleware in VS Code.
>          I can see the validateToken function. You copied a
>          bearer token, but ContextPulse masked it on the way
>          to disk, so all I get is the marker. Want me to help
>          debug the token validation?"
```

```
> You hold Ctrl+Space and say: "refactor this to use the new auth provider"

ContextPulse Voice transcribes → pastes into editor
Claude calls get_recent_transcriptions → get_screen_text:

> Claude: "I can see your dictation and the current file.
>          Here's the refactored middleware using AuthProvider..."
```

```
> You: "What was I working on before lunch?"

Claude calls get_activity_summary(hours=4) → search_history("auth"):

  10:15 AM  VS Code, auth/middleware.ts (45 min)
  11:00 AM  Chrome, JWT.io (12 min)
  11:15 AM  VS Code, auth/tests/test_middleware.py (30 min)
  11:45 AM  Slack, #backend channel (15 min)

> Claude: "You spent the morning on auth middleware: writing
>          the implementation, checking JWT specs, then adding
>          tests. You were in Slack #backend right before lunch."
```

## Why ContextPulse?

AI coding assistants are powerful but blind. They can't see your screen, hear your voice notes, or know what you were just doing. ContextPulse bridges this gap:

- **Local-first.** Capture, storage and search run on your machine. Core features need no account. Pro is an optional paid tier unlocked by a license key that verifies offline. ContextPulse contacts no third-party server until you switch on one of the opt-in network features listed above, two of which send captured text to Anthropic and are off by default.
- **MCP-native from day one.** ContextPulse exposes all context as MCP tools over streamable HTTP, so any standards-compliant MCP client can read it without a custom integration. Ready-made config snippets for Claude Code, Cursor and Continue are in [docs/mcp-configs](docs/mcp-configs/README.md).
- **True multi-modal in a single daemon.** Screen capture, voice dictation and keyboard/mouse input all run in one background process. No stitching multiple tools together.
- **Open source (AGPL-3.0).** Fully auditable, self-hostable, and extensible. No vendor lock-in, no SaaS dependency, no risk of acquisition-driven shutdowns.

### What Makes ContextPulse Different

| Capability | ContextPulse | Typically Available? |
|---|---|---|
| **Screen capture + OCR** | Yes, native resolution | Common |
| **Voice dictation** | Yes, local Whisper | Rare as integrated feature |
| **Keyboard + mouse tracking** | Yes | Rare |
| **Semantic memory** | Yes, three-tier with hybrid search | Rare |
| **All capture in one daemon** | Yes, single background process | No, usually separate tools |
| **MCP-native** | Yes, 37 tools | Emerging |
| **Local capture and storage** | Yes, with three opt-in network features, all off by default | Uncommon |
| **Open source** | AGPL-3.0 | Varies |

### Platform Support

| Platform | Status |
|----------|--------|
| Windows 10+ | Full support |
| macOS 13+ (Apple Silicon and Intel) | Full support |
| Linux | Community contributions welcome -- core abstractions are in place, platform modules need implementation |

## Installation

```bash
git clone https://github.com/ContextPulse/contextpulse
cd contextpulse
# Windows
pip install -e packages/core -e packages/screen -e packages/voice -e packages/touch -e packages/project

# macOS — the [macos] extras are REQUIRED, not optional niceties. They pull in
# pyobjc (clipboard, window, caret, session monitor), rumps (menu bar) and
# mlx-whisper (Apple Silicon transcription). Without them the install succeeds
# and then fails at runtime.
pip install -e "packages/core[macos]" -e "packages/screen[macos]" -e "packages/voice[macos]" \
            -e packages/touch -e packages/project

# Optional: persistent memory + semantic search
pip install -e packages/memory
```

Configure your AI agent and install companion skills:

```bash
contextpulse --setup claude-code   # configures MCP + installs skills
# or: contextpulse --setup gemini  # for Gemini CLI
# or: contextpulse --setup all     # both
```

`contextpulse --setup` writes the authenticated `contextpulse` entry into Claude Code, Cursor and Gemini CLI, creating the access token if this is the first run. Re-run it after regenerating the token.

Start ContextPulse:

```bash
contextpulse       # starts the background daemon
contextpulse-mcp   # starts the MCP server on port 8420
```

That's it. Your AI agent now has tools for reading your screen, voice, activity, and memory.

<details>
<summary>Manual MCP configuration (if not using --setup)</summary>

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "contextpulse": {
      "type": "http",
      "url": "http://127.0.0.1:8420/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN_HERE" }
    }
  }
}
```

The endpoint requires an access token. ContextPulse generates one per install on first use and stores it with user-only permissions at `%APPDATA%\ContextPulse\mcp_token` (macOS: `~/Library/Application Support/ContextPulse/mcp_token`; Linux: `$XDG_CONFIG_HOME/ContextPulse/mcp_token`). Print yours with `contextpulse-mcp --print-config claude-code`, or find it in the tray under **Settings -> MCP Access**. See [docs/mcp-configs](docs/mcp-configs/README.md) for Cursor, Gemini CLI, Claude Desktop and troubleshooting.

The token defends against other local callers: other accounts on the machine, sandboxed applications with loopback access, and other MCP clients and agents. It does not defend against a process already running as you, which can read the token file and the databases directly.
</details>

## MCP Tools

### Sight (11 free tools)

| Tool | What it does |
|------|-------------|
| `get_screenshot` | Capture screen (active monitor, all monitors, or a region) |
| `get_recent` | Recent frames from the rolling buffer (with diff filtering) |
| `get_screen_text` | OCR the current screen at native resolution |
| `get_monitor_summary` | Lightweight text summary of all monitors (low token cost) |
| `get_buffer_status` | Daemon health check + buffer stats |
| `get_activity_summary` | App usage breakdown over last N hours |
| `search_history` | Full-text search across window titles + OCR text |
| `get_context_at` | Frame + metadata from N minutes ago |
| `get_clipboard_history` | Recent clipboard entries |
| `search_clipboard` | Search clipboard by text content |
| `get_agent_stats` | Which MCP clients are consuming context, and how often |

### Voice (7 free tools)

| Tool | What it does |
|------|-------------|
| `get_recent_transcriptions` | Recent voice dictation history (raw + cleaned) |
| `get_voice_stats` | Dictation count, duration, accuracy stats |
| `get_vocabulary` | Current word correction entries |
| `learn_from_session` | Analyze dictation history to auto-learn vocabulary corrections (patterns seen 2+ times) |
| `rebuild_context_vocabulary` | Rebuild vocabulary from `PROJECT_CONTEXT.md` files (project names + domain terms) |
| `consolidate_learning` | Run the full cross-modal vocabulary consolidation pipeline (the core learning loop) |
| `check_corrections` | Detect repeated voice corrections that should become permanent vocabulary |

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

### Recall (2 free tools)

| Tool | What it does |
|------|-------------|
| `facts_about` | Consolidated facts about a project, person, file, tool, or topic |
| `context_at` | What was happening around a given moment in time |

These two read the nightly-distilled fact store, so they return nothing until the consolidator has run at least once.

### Memory (5 free + 2 Pro tools)

Basic memory is **free forever**. No license required.

| Tool | Tier | What it does |
|------|------|-------------|
| `memory_store` | Free | Store a key-value memory with optional tags and TTL |
| `memory_recall` | Free | Retrieve a memory by exact key |
| `memory_list` | Free | List memories, optionally filtered by tag |
| `memory_forget` | Free | Delete a memory by key |
| `memory_stats` | Free | Storage statistics (entry counts, DB sizes, tiers) |
| `memory_search` | Pro | Hybrid/keyword/semantic search across all stored memories |
| `memory_semantic_search` | Pro | Pure vector search using all-MiniLM-L6-v2 embeddings |

Memory uses a 3-tier hot/warm/cold architecture: in-memory LRU cache → SQLite WAL + FTS5 → compressed archive. The optional `pip install contextpulse-memory` package ships these tools.

### Pro (4 tools, requires license or 30-day trial)

| Tool | What it does |
|------|-------------|
| `memory_search` | Hybrid/keyword/semantic search across stored memories |
| `memory_semantic_search` | Pure vector search using sentence embeddings |
| `search_all_events` | Cross-modal full-text search across screen, voice, clipboard, keys |
| `get_event_timeline` | Temporal view of all events across all modalities |

**Free forever:** 33 tools (Sight × 11, Voice × 7, Touch × 3, Project × 5, Memory × 5, Recall × 2)
**Pro:** adds 4 search tools: semantic memory search plus cross-modal event queries
**Trial:** 30-day Pro trial on first use, no credit card required

Additionally, ContextPulse includes several background learning tools (vocabulary consolidation, correction detection) that run automatically to improve transcription quality over time.

## Architecture

ContextPulse is a monorepo with modular packages:

| Package | Purpose |
|---------|---------|
| `contextpulse-core` | Daemon, EventBus (spine), config, licensing, settings |
| `contextpulse-sight` | Screen capture, OCR, clipboard monitoring |
| `contextpulse-voice` | Hold-to-dictate, Whisper transcription, vocabulary |
| `contextpulse-touch` | Keyboard/mouse activity capture, correction detection |
| `contextpulse-project` | Project detection and journal routing |
| `contextpulse-memory` | Persistent key-value memory with semantic search (optional) |

All modules emit events to a shared **EventBus** (the "spine"), which writes to a local SQLite database with FTS5 full-text search. The MCP servers never write to `activity.db`; that pipeline is capture-only. The memory server is the exception, and writes by design to its own `memory.db` through `memory_store` and `memory_forget`.

## Development

```bash
git clone https://github.com/ContextPulse/contextpulse
cd contextpulse
uv venv
.venv\Scripts\activate
uv pip install -e "packages/core[dev]" -e packages/screen -e packages/voice -e packages/touch -e packages/project
pytest packages/ -x -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Canary Health Check

A canary script exercises every exposed MCP tool and reports pass/fail. It runs automatically on a cron/Task Scheduler schedule to catch regressions before users do.

```bash
# Run manually
python scripts/canary_health_check.py

# Verbose (shows each tool as it runs)
python scripts/canary_health_check.py --verbose

# JSON output (for CI or external monitoring)
python scripts/canary_health_check.py --json
```

**What it does:**
- Auto-starts the ContextPulse daemon if it is not already running
- Calls all primary MCP tools with minimal valid arguments
- Prints a human-readable summary with per-server breakdown
- Appends results to `logs/canary_results.json` (last 100 runs retained)
- Exits `0` if all tools pass, `1` if any fail

**Scheduling (Windows Task Scheduler):**

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, repeat every 4 hours
3. Action: Start a program
   - Program: `<path-to-contextpulse>\.venv\Scripts\python.exe`
   - Arguments: `scripts/canary_health_check.py`
   - Start in: `<path-to-contextpulse>`

## License

ContextPulse is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

- You can use, modify, and distribute ContextPulse freely
- If you modify and deploy it as a service, you must open-source your changes
- Commercial licensing available for embedding in proprietary products

For commercial licensing inquiries, visit [contextpulse.ai](https://contextpulse.ai).

## Patent Notice

ContextPulse's unified multi-modal context delivery system is patent pending.

---

<p align="center">
  Built by <a href="https://contextpulse.ai">Jerard Ventures LLC</a>
</p>
