# Landing Page Copy — ContextPulse Sight

Generated via direct-response-copy skill. Awareness level: Problem-Aware to Solution-Aware.
Positioning angle: Lightweight Champion (anti-Screenpipe).
Brand voice: Direct, technical, confident. No superlatives. No exclamation marks.

---

## Hero

**Badge:** Free and open source
**Headline:** Your AI can write code. It can't see your screen.
**Subheadline:** ContextPulse captures your desktop continuously and serves it to any MCP-compatible AI agent. Install in 30 seconds. No cloud, no API keys, no cost.
**Primary CTA:** Get started free -> #install
**Secondary CTA:** View on GitHub -> https://github.com/junkyard-rules/contextpulse
**Install block:** `pip install contextpulse-sight`

*Rationale: Headline names the pain directly. Subheadline covers what, how, and three objection-killers (speed, privacy, price) in one sentence.*

---

## Problem Agitation

**Section label:** THE PROBLEM
**Section title:** Every AI conversation starts blind
**Section subtitle:** Your AI assistant can refactor your codebase but has no idea what's on your screen. You're the bottleneck.

**Card 1: Manual screenshots**
Snip Tool, paste, wait for processing. Every question. Every session. It breaks your flow and costs you minutes each time.

**Card 2: Context amnesia**
New session, blank slate. Developers spend ~5 hours/week re-explaining context their AI should already have.

**Card 3: Agent silos**
Claude Code, Cursor, Gemini CLI, Copilot -- none of them share visual context. Your tools work in isolation.

*Rationale: Three pain points match the three products in the suite (Sight solves #1, Memory solves #2, Agent solves #3). Plants seeds for future products.*

---

## Stats Bar

| Stat | Value | Label |
|------|-------|-------|
| Speed | <3ms | Per capture |
| CPU | <1% | CPU usage |
| MCP | 7 | MCP tools |
| Cloud | 0 | Cloud dependencies |
| Tests | 118 | Tests passing |
| Storage | 4 | Storage modes |

*Rationale: Leads with performance (the differentiator vs Screenpipe). "0 cloud dependencies" reinforces privacy without saying the word.*

---

## Features (Product Suite)

### Sight (Available now)
**Headline:** ContextPulse Sight
**Body:** Runs in your system tray. Captures your screen every 5 seconds with change detection. Serves every frame to any MCP-compatible AI agent via 7 built-in tools.

**Feature-benefit bullets:**
- 4 capture modes -- active monitor, all monitors, cursor region, auto-timer -- so you get the right frame for any situation
- Built-in OCR -- extract text from any screen without sending an image, so your agent gets context even on metered connections
- Activity database with FTS5 search -- find what you were looking at 10 minutes ago, by app name or window title
- Smart storage -- text-only when text-heavy, image when visual -- so your disk doesn't fill up (saves 59%)
- Window blocklist and auto-pause on lock -- so banking apps and passwords never get captured

**Code example:**
```
# In Claude Code, Cursor, or any MCP client:
get_screenshot(mode="active")
get_screen_text()
search_history("error in terminal")
get_context_at(minutes_ago=10)
```

### Memory (Coming soon)
**Headline:** ContextPulse Memory
**Body:** Cross-session persistent memory so your AI agents don't start from zero. Decisions, preferences, and project context that accumulates over time -- shared across all your AI tools.

**Bullets:**
- Shared memory across Claude Code, Cursor, Gemini CLI, and more
- Outcome-based learning -- what worked, what didn't
- SQLite-backed, local-first, portable

### Agent (Coming soon)
**Headline:** ContextPulse Agent
**Body:** Multi-agent coordination so your AI tools work as a team, not isolated silos. Session protocols, handoff patterns, and shared state between any MCP-compatible agent.

**Bullets:**
- Agent-to-agent session handoff
- Shared project state across tools
- Conflict detection when agents edit the same files

---

## Comparison Table

**Section label:** HOW WE COMPARE
**Section title:** Built for developers who use AI agents
**Section subtitle:** Lightweight, MCP-native, and privacy-first. Not a heavyweight screen recorder.

| Feature | ContextPulse | Screenpipe | Manual Screenshots |
|---------|-------------|------------|-------------------|
| Setup time | 30 seconds (pip install) | 5-10 minutes | None |
| CPU usage | <1% | 5-15% | 0% (manual) |
| MCP native | 7 tools | Partial | No |
| Privacy controls | Blocklist + auto-pause + lock | Basic | Full (manual) |
| Activity search | FTS5 over titles + OCR | Yes | No |
| Storage modes | 4 (smart/visual/both/text) | Full recording | N/A |
| RAM usage | <20 MB | 200-500 MB | 0 MB |
| Price | Free, open source | $400 lifetime | Free |

*Rationale: Every row ContextPulse wins or ties on. The table does the persuading -- no need to badmouth Screenpipe explicitly.*

---

## How It Works

**Section title:** Three commands. That's it.

**Step 1: Install**
`pip install contextpulse-sight`
Python 3.10+. No GPU. No cloud account. No API key.

**Step 2: Run**
`contextpulse-sight`
Starts in your system tray. Auto-captures every 5 seconds. Invisible.

**Step 3: Connect**
Add the MCP server to Claude Code, Cursor, or any MCP-compatible tool. Ask your AI what's on screen.

*Rationale: Three steps matches the "30 seconds" claim. Each step addresses a potential objection (dependencies, complexity, compatibility).*

---

## Pricing

**Section title:** Sight is free. Memory is coming.
**Section subtitle:** Start with visual context at no cost. Add persistent memory when you need it.

**Sight (featured):** Free -- Open source, forever
- Always-on screen capture
- 7 MCP tools
- 4 storage modes
- Activity search (FTS5)
- Privacy controls
- OCR text extraction
- CTA: Install now

**Memory Starter:** $29 one-time
- Cross-session persistence
- Single-agent memory
- SQLite-backed, local-first
- MCP tools for read/write/search
- Label: Coming soon

**Memory Pro:** $49 one-time
- Everything in Starter
- Multi-agent shared memory
- Outcome-based learning
- Journal routing and search
- Priority support
- Label: Coming soon

*Rationale: Free tier eliminates price objection entirely. Future tiers plant revenue seeds without blocking adoption.*

---

## Privacy

**Section title:** Your screen stays on your machine
**Section subtitle:** ContextPulse runs 100% locally. No cloud. No accounts. No telemetry. No data ever leaves your computer.

**100% local** -- All captures stay on disk. No network calls. Works offline.
**Window blocklist** -- Skip sensitive apps automatically. Banking, password managers, private browsing.
**Auto-pause on lock** -- Captures stop instantly when you lock your screen or switch users.
**Open source** -- Every line of code is auditable. No hidden data collection.

---

## Final CTA

**Headline:** Stop screenshotting. Start building.
**Subtitle:** Free, open source, and local-first. Your AI agent sees what you see in 30 seconds.
**Primary CTA:** Get started free -> #install
**Secondary CTA:** Star on GitHub -> GitHub URL

---

## Objection Handling (FAQ candidates)

**Q: Does this slow down my machine?**
A: Under 1% CPU and under 20MB RAM. The daemon uses mss for capture (3ms per frame) and only stores frames that changed.

**Q: What about sensitive information on my screen?**
A: Window blocklist lets you skip specific apps (banking, password managers). Auto-pause on lock screen. Everything stays local -- no cloud, no telemetry.

**Q: Does it work with Cursor / Copilot / my tool?**
A: Any MCP-compatible tool. That includes Claude Code, Cursor, Gemini CLI, and any custom agent using the MCP SDK.

**Q: How is this different from Screenpipe?**
A: Screenpipe records everything you see, say, and hear -- full video and audio. ContextPulse captures only what your AI agents need: screen context. That's why it uses <1% CPU vs Screenpipe's 5-15%, and <20MB RAM vs 200-500MB. It's also free.

**Q: Is this Windows-only?**
A: Windows-first, but the capture library (mss) is cross-platform. macOS and Linux support is planned.

---

## Founder Story (for Product Hunt first comment)

I built ContextPulse because I was screenshotting my desktop 20 times a day for my AI coding assistants. I run Claude Code and Gemini CLI side by side across 9 projects. Every conversation started with me manually capturing whatever I was looking at.

ContextPulse Sight sits in your system tray and handles all of that automatically. Your AI asks what's on screen -- it already knows. Zero effort, zero flow interruption.
