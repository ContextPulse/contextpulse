# ContextPulse Launch Posts

---

## 1. Hacker News (Show HN)

**Title:** Show HN: ContextPulse -- Open-source daemon that gives AI agents eyes, ears, and memory via MCP

**Body:**

I've been building ContextPulse for the past several months as a solo project. It's a Windows desktop daemon that captures your screen, voice, and keyboard/mouse activity, then exposes all of that context to AI agents through MCP (Model Context Protocol) tools. The idea is simple: your AI coding assistant shouldn't have to ask "what's on your screen?" -- it should already know.

The architecture is a single-process daemon with an EventBus spine backed by SQLite + FTS5. Three capture modules (Sight, Voice, Touch) feed events into a shared database, and separate MCP server processes expose 23 read-only tools over stdio transport. Screen capture runs on a 5-second timer plus event-driven triggers, OCR happens in a background worker, voice uses faster-whisper locally (no cloud calls), and the whole thing idles at ~0.5% CPU / 15 MB RAM (80 MB with the Whisper model loaded). Everything runs locally -- zero cloud dependency, zero telemetry. The daemon auto-starts on login and sits in your system tray.

It's AGPL-3.0 licensed. The free tier includes all 21 core MCP tools -- screen capture, OCR, clipboard history, voice transcription, typing analytics, project detection, and more. The Pro tier ($10-15/mo) adds two cross-modal search tools that let agents query across screen + voice + clipboard + keyboard events simultaneously. 798 tests passing, patent pending on the cross-modal event architecture. Built with Python 3.14.

Repo: [REPO_URL]
Landing page: [LANDING_PAGE_URL]

---

## 2. Reddit r/LocalLLaMA

**Title:** ContextPulse: open-source daemon that gives your local AI setup persistent screen/voice/keyboard context via MCP (zero cloud, runs entirely on your machine)

**Body:**

Hey all -- I built something I think this community will appreciate given the local-first ethos here.

ContextPulse is a desktop daemon that continuously captures what's on your screen (with OCR), your voice (via faster-whisper running locally), and your keyboard/mouse activity, then makes all of that available to AI agents through MCP tools. No cloud. No telemetry. Everything stays on your machine in a local SQLite database.

**Why this matters for local LLM setups:** If you're running Claude Code, Cursor, or any MCP-compatible agent, ContextPulse gives it persistent awareness of your desktop. Your agent can look at what's on screen, search through your recent clipboard history, check what you were working on 20 minutes ago, or see voice dictation transcripts. It's like giving your AI assistant a photographic memory of your workstation.

**Key specs:**
- 23 MCP tools across Sight (screen/OCR/clipboard), Voice (whisper transcription), Touch (typing/mouse), and Project (auto-routing)
- Idles at 0.5% CPU, 15 MB RAM (80 MB with Whisper model)
- SQLite + FTS5 for full-text search across all modalities
- Voice transcription uses faster-whisper base model -- runs entirely local, no API keys needed
- Python 3.14, auto-starts on login, Windows only for now
- AGPL-3.0, 798 tests passing

**Free vs Pro:** 21 of 23 tools are completely free. Pro ($10-15/mo) unlocks cross-modal search (query across screen + voice + clipboard simultaneously) and event timeline. The free tier alone is fully functional for most use cases.

Built by one person. Patent pending on the cross-modal architecture.

Repo: [REPO_URL] | Landing page: [LANDING_PAGE_URL]

Happy to answer questions about the architecture or MCP integration.

---

## 3. Reddit r/ClaudeAI

**Title:** I built an open-source daemon that gives Claude Code eyes, ears, and memory -- 23 MCP tools for screen, voice, and keyboard context

**Body:**

If you use Claude Code, you've probably wished it could just *see* what you're looking at. ContextPulse fixes that.

It's a background daemon that captures your screen (with OCR), voice dictation (local Whisper, no cloud), and keyboard/mouse activity, then exposes everything to Claude Code through MCP tools. Once it's running, Claude can:

- **See your screen:** `get_screenshot`, `get_screen_text` (OCR), `get_recent` (rolling buffer of recent frames)
- **Search your history:** "What was on my screen 10 minutes ago?" -- `get_context_at`, `search_history`
- **Read your clipboard:** `get_clipboard_history`, `search_clipboard`
- **Know what you dictated:** `get_recent_transcriptions`, `get_voice_stats`
- **Track your activity:** `get_activity_summary` shows which apps you've been using
- **Detect your project:** `get_active_project` auto-identifies what project you're in based on CWD and window titles, then `route_to_journal` logs insights to the right place

Total of 23 MCP tools, 21 of which are free. It runs entirely locally -- no data leaves your machine.

**Setup is straightforward:** install, add the MCP servers to your Claude config, and Claude Code immediately has access to all 23 tools. The daemon auto-starts on login and uses <1% CPU at idle.

AGPL-3.0. 798 tests. Built solo. Patent pending.

Repo: [REPO_URL] | Landing page: [LANDING_PAGE_URL]

Would love to hear how people end up using it. The project detection + journal routing has been my most-used feature -- Claude automatically tags observations to the right project.

---

## 4. MCP Discord / Community

**Title:** ContextPulse -- 23 MCP tools for screen, voice, and keyboard context (open-source)

**Body:**

Releasing ContextPulse, an open-source desktop daemon that exposes screen, voice, and keyboard/mouse context to MCP clients.

**What it provides:**
- **Sight (12 tools):** screen capture, OCR, clipboard history, activity summary, FTS search across window titles and OCR text, rolling screenshot buffer
- **Voice (3 tools):** transcription history via local faster-whisper, voice stats, vocabulary corrections
- **Touch (3 tools):** typing burst analytics, mouse events, correction detection
- **Project (5 tools):** auto-detect active project from CWD/window title, route journal entries, retrieve project context

**Architecture:** Single daemon process with EventBus spine (SQLite + FTS5). MCP servers run as separate stdio processes -- read-only queries against the shared database. Each module (Sight, Voice, Touch) is a separate MCP server entry in your config.

**Install:**
1. Install ContextPulse (Windows, Python 3.14)
2. Add MCP server entries to your client config:
```json
{
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
```
3. Your MCP client now has access to 23 tools

**License:** AGPL-3.0. 21 tools free, 2 Pro-gated (cross-modal search, event timeline).

Repo: [REPO_URL]

---

## 5. Twitter/X Thread

**Tweet 1 (Hook):**
I just open-sourced ContextPulse -- a desktop daemon that gives AI agents eyes, ears, and memory.

23 MCP tools. Screen capture + OCR + voice + keyboard context. Runs entirely local. Zero cloud.

Here's what it does and why I built it:

[DEMO_GIF_URL]

**Tweet 2 (The problem):**
AI coding assistants are powerful but blind. They can't see your screen, don't know what you copied 5 minutes ago, and have no idea you just dictated a note.

ContextPulse runs in the background and feeds all of that context to your AI agent through MCP tools.

**Tweet 3 (What it captures):**
Three capture modules, one daemon:

- Sight: screen capture every 5s + OCR + clipboard monitoring
- Voice: hold-to-dictate with local Whisper (no cloud)
- Touch: typing bursts, mouse clicks, correction detection

All events go into a local SQLite DB with full-text search.

**Tweet 4 (The numbers):**
Built solo over several months:

- 23 MCP tools (21 free, 2 Pro)
- 798 tests passing
- 0.5% CPU at idle, 15 MB RAM
- Zero cloud dependency
- AGPL-3.0
- Patent pending

**Tweet 5 (Use cases):**
What can your AI agent do with ContextPulse?

- "What was on my screen 10 min ago?"
- Search clipboard history by keyword
- See which apps you've been using all day
- Auto-detect which project you're in
- Get voice transcription context without asking

**Tweet 6 (Free vs Pro):**
Pricing philosophy: the core is free forever.

21 tools -- screen capture, OCR, clipboard, voice, typing analytics, project detection -- all free under AGPL-3.0.

Pro ($10-15/mo) adds cross-modal search: query across screen + voice + clipboard + keyboard simultaneously.

**Tweet 7 (CTA):**
Try it out:

Repo: [REPO_URL]
Landing page: [LANDING_PAGE_URL]

Works with Claude Code, Cursor, and any MCP-compatible client. Windows only for now, cross-platform planned.

Star the repo if this is useful. Feedback welcome.
