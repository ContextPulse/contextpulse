# ContextPulse Marketing Assets

Ready-to-post copy for launch channels. Follows brand voice (brand/voice.md): direct, technical, confident, no hype.

---

## 1. Show HN

**Title:** Show HN: ContextPulse Sight -- Always-on screen capture for AI coding assistants (free, MCP-native)

**Body:**

I built ContextPulse Sight because I got tired of screenshotting my desktop every time Claude Code or Cursor needed visual context.

It's a Python daemon that runs in your system tray, captures your screen every 5 seconds, and serves it to any MCP-compatible AI agent. Your AI can see what you see without you lifting a finger.

What it does:

- 7 MCP tools: get_screenshot, get_screen_text (OCR), search_history (FTS5), get_context_at, get_recent, get_activity_summary, get_buffer_status
- <1% CPU, <20MB RAM, <3ms per capture
- 4 storage modes (smart/visual/both/text) -- smart mode saves 59% disk by dropping images when OCR text is sufficient
- Privacy: window blocklist, auto-pause on lock screen, no cloud, no telemetry
- Activity database: searchable history of what apps/windows were used

I use it daily with Claude Code and Gemini CLI running side by side. Both can see my screen without me doing anything.

Tech: Python 3.10+, mss for capture, rapidocr-onnxruntime for OCR, SQLite + FTS5 for search, pystray for tray. 118 tests passing.

Install: `pip install contextpulse-sight`

GitHub: https://github.com/junkyard-rules/contextpulse

Free and open source. Windows-first (the underserved platform), cross-platform planned.

---

## 2. Reddit Posts

### 2a. r/ClaudeAI — MCP integration angle

**Title:** I built an MCP server that gives Claude Code always-on screen awareness

**Body:**

Tired of screenshotting my desktop for Claude Code, so I built ContextPulse Sight.

It's a background daemon that auto-captures your screen and serves it via MCP. Claude Code can call `get_screenshot()`, `get_screen_text()` (OCR), or `search_history("error in terminal")` without you doing anything.

Setup is 30 seconds:

```
pip install contextpulse-sight
contextpulse-sight  # runs in system tray
claude mcp add contextpulse-sight
```

Under 1% CPU, under 20MB RAM, 7 MCP tools, 118 tests. Privacy controls built in -- window blocklist skips banking apps, auto-pauses on lock screen. Everything stays local.

I run it alongside Gemini CLI too. Both agents share visual context through the same MCP server.

GitHub: https://github.com/junkyard-rules/contextpulse

Free, open source. Feedback welcome.

### 2b. r/LocalLLaMA — Local-first angle

**Title:** Built a local-first screen capture MCP server for AI agents -- no cloud, no API keys

**Body:**

ContextPulse Sight is a daemon that captures your screen continuously and exposes it via MCP tools. Any MCP-compatible agent (Claude Code, Cursor, or your own local LLM setup) can call `get_screenshot()`, `get_screen_text()`, or `search_history()` to understand what's on your screen.

Key specs: under 1% CPU, under 20MB RAM, under 3ms per capture. 4 storage modes including smart mode that drops images when OCR text is sufficient (saves 59% disk). SQLite + FTS5 for full-text search over window titles and OCR text.

Everything runs on your machine. No cloud dependency, no API keys, no accounts. Privacy controls include window blocklist and auto-pause on lock.

Python 3.10+, 118 tests, Windows-first. Cross-platform planned.

`pip install contextpulse-sight`

GitHub: https://github.com/junkyard-rules/contextpulse

### 2c. r/SideProject — Build story angle

**Title:** I replaced my Snip Tool workflow with an always-on screen capture daemon for AI agents

**Body:**

I use Claude Code and Gemini CLI to build software across 9 projects. Every conversation started with me manually screenshotting whatever I was looking at. Multiply that by 20+ sessions a day and it adds up.

So I built ContextPulse Sight: a Python daemon that sits in my system tray, captures my screen every 5 seconds, and makes everything available to my AI agents via MCP. Now when I ask Claude "what's on my screen?" it just knows.

The result: 7 MCP tools, activity search across window titles, smart storage that saves 59% disk, privacy blocklist for sensitive apps. Under 1% CPU.

Free, open source, 118 tests. Built it in 2 weeks.

GitHub: https://github.com/junkyard-rules/contextpulse
Site: https://contextpulse.ai

---

## 3. Twitter/X Thread

**Tweet 1 (hook):**
Your AI coding assistant can refactor your entire codebase.

It can't see what's on your screen.

I fixed that. Here's ContextPulse Sight:

**Tweet 2 (problem):**
Every AI conversation starts blind. You screenshot, paste, re-explain. 20 times a day. That's not "AI-assisted" -- that's you being the bottleneck.

**Tweet 3 (solution):**
ContextPulse Sight runs in your system tray. Captures your screen every 5 seconds. Serves it to any MCP-compatible agent.

Claude Code, Cursor, Gemini CLI -- they can all call get_screenshot() or get_screen_text() without you lifting a finger.

**Tweet 4 (specs):**
The specs:
- Under 1% CPU
- Under 20MB RAM
- Under 3ms per capture
- 7 MCP tools
- 118 tests
- 4 storage modes
- Built-in OCR + FTS5 search

**Tweet 5 (privacy):**
Everything stays on your machine. No cloud. No API keys. No accounts. No telemetry.

Window blocklist skips banking apps. Auto-pauses when you lock your screen.

**Tweet 6 (CTA):**
Free and open source. Install in 30 seconds:

pip install contextpulse-sight

GitHub: https://github.com/junkyard-rules/contextpulse
Site: https://contextpulse.ai

---

## 4. Product Hunt Listing

**Tagline (60 chars):**
Always-on screen capture for AI coding assistants

**Description (260 chars):**
ContextPulse Sight captures your desktop continuously and serves it to any MCP-compatible AI agent. Your AI sees what you see -- locally, privately, instantly. 7 MCP tools, built-in OCR, activity search. Free and open source.

**First Comment (maker story):**
Hey Product Hunt -- I'm David, and I built ContextPulse because I was screenshotting my desktop 20 times a day for my AI coding assistants.

I run Claude Code and Gemini CLI side by side across 9 projects. Every conversation started with me manually screenshotting whatever I was looking at. It broke my flow constantly.

ContextPulse Sight sits in your system tray, captures your screen every 5 seconds with change detection, and makes everything available via MCP. Your AI can call get_screenshot(), get_screen_text(), or search_history() without you doing anything.

It uses under 1% CPU and under 20MB RAM. Everything stays local -- no cloud, no API keys, no telemetry. Window blocklist keeps banking apps private. Auto-pauses when you lock your screen.

Free and open source. Feedback welcome -- especially from developers who use AI tools daily.

**Tags:** Developer Tools, Artificial Intelligence, Open Source, Privacy, MCP

---

## 5. Gumroad Tags

contextpulse, screen-capture, mcp, ai-agents, developer-tools, privacy, open-source, coding-assistant, context, visual-context, local-first, claude-code, cursor, python

---

## 6. Key Positioning Angles

| Angle | Best For | Lead With |
|-------|----------|-----------|
| "Your AI is blind" | Developer communities | Pain point → solution |
| "Under 1% CPU" | Performance-conscious devs | Specs → comparison to Screenpipe |
| "No cloud, no API keys" | Privacy-focused audiences | Trust → local-first architecture |
| "I replaced Snip Tool" | General dev audience | Personal story → workflow improvement |
| "7 MCP tools" | MCP ecosystem audiences | Technical capability → integration ease |
