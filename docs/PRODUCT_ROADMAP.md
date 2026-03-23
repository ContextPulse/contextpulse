# ContextPulse — Product Roadmap & Feature Brainstorm

*Comprehensive feature inventory across all four products. Organized by product, then by phase.*

---

## ContextPulse Sight

### Shipped (Phase 1-3, March 2026)
- [x] Always-on daemon with system tray
- [x] Auto-capture every 5s with change detection
- [x] Per-monitor capture + rolling buffer (30 min)
- [x] 10 MCP tools (screenshot, recent, OCR, search, time-travel, clipboard, agent stats, buffer status, activity summary, context-at)
- [x] Built-in OCR with smart storage (4 modes, 59% disk savings)
- [x] Activity database with FTS5 search
- [x] Privacy: window blocklist, auto-pause on lock, OCR redaction
- [x] Clipboard monitoring with history and search
- [x] Event-driven capture (window focus, idle detection, monitor cross)
- [x] Multi-agent awareness (tracks which agents use context)
- [x] Token cost estimation per frame
- [x] Diff-aware capture
- [x] MCP config generator (--setup claude-code/cursor/gemini)
- [x] Settings panel, first-run wizard, hotkey reference
- [x] Ed25519 license verification (free/pro tiers)
- [x] File logging + full watchdog for all daemon threads
- [x] 145 tests passing

### Next (Phase 4)
- [ ] **OCR confidence tracking per app** — measure accuracy by window class, build per-app profiles
- [ ] **Capture frequency auto-tuning** — learn per-app change rates, adjust interval dynamically
- [ ] **Privacy auto-detection** — learn which apps trigger manual pauses, auto-add to blocklist
- [ ] **Frame relevance scoring** — track when agents use vs ignore frames, prioritize high-value captures
- [ ] **Smart storage evolution** — learn optimal mode per app (terminal→text, design→visual)
- [ ] **OCR preprocessing per app** — auto-adjust contrast/zoom/crop for dark themes, small fonts
- [ ] **Monitor-specific capture profiles** — different settings per monitor (ultrawide, portrait)
- [ ] **Annotation layer** — user can tag frames with notes ("this is the bug", "show this to Bob")
- [ ] **Screenshot diffing** — visual diff between frames, highlight what changed
- [ ] **Code-aware OCR** — detect programming language from screen, apply language-specific OCR models
- [ ] **Multi-user awareness** — pause when screensharing, detect Zoom/Teams/Meet

### Future
- [ ] **Video clip export** — stitch buffer frames into short clips for bug reports
- [ ] **Screen recording mode** — continuous recording for demos (vs default screenshot mode)
- [ ] **Remote capture** — capture from headless servers or VMs via agent
- [ ] **macOS support** — cross-platform capture (mss already supports it)
- [ ] **Linux support** — Wayland + X11
- [ ] **Browser extension** — capture specific browser tabs, not just full screen
- [ ] **IDE integration** — VS Code/Cursor extension that feeds context directly

---

## ContextPulse Memory

### MVP (Phase 1)
- [ ] **Persistent key-value memory** — agents store/retrieve facts across sessions
- [ ] **MCP tools** — memory_store, memory_recall, memory_search, memory_forget
- [ ] **SQLite-backed** — local-first, portable, no cloud dependency
- [ ] **Multi-agent read/write** — Claude Code, Cursor, Gemini CLI all share the same memory
- [ ] **Namespaced memory** — per-project, per-user, global scopes
- [ ] **Confidence scoring** — memories have confidence levels, decay over time if not reinforced
- [ ] **Memory types** — facts, preferences, corrections, decisions, observations

### Phase 2: Learning Engine
- [ ] **Correction cascading** — user corrections to agent suggestions become permanent memory, not per-session
- [ ] **Context pre-delivery** — learn what agents need at session start, pre-package it
- [ ] **Decision journal** — correlate screen context + agent suggestion + user action → decision history
- [ ] **Error pattern detection** — recurring errors across sessions, with root cause correlation
- [ ] **Preference learning** — code style, tool preferences, communication style — learned from corrections
- [ ] **Anti-patterns** — track what the agent suggested that the user consistently rejects → stop suggesting
- [ ] **Session summaries** — auto-generate end-of-session summary from activity + decisions

### Phase 3: Intelligence
- [ ] **Outcome tracking** — did the agent's suggestion work? Track follow-up errors, reverts, re-edits
- [ ] **Codebase knowledge graph** — relationships between files, modules, people, decisions
- [ ] **Temporal context** — "what was true about this project last Tuesday" vs "what's true now"
- [ ] **Memory consolidation** — nightly process that merges, deduplicates, and strengthens memories (like sleep)
- [ ] **Conflict resolution** — when two agents write conflicting memories, detect and flag
- [ ] **Memory export/import** — onboard new team members with shared project memory
- [ ] **Forgetting** — intentional decay of stale/outdated memories to keep the knowledge base clean

### Future
- [ ] **Team memory** — shared memory across team members (requires cloud sync)
- [ ] **Memory visualization** — web dashboard showing what the system knows, confidence levels
- [ ] **Memory audit** — user reviews what's been learned, approves/rejects/corrects
- [ ] **Cross-project transfer** — lessons from one project inform another (e.g., "you always use pytest, not unittest")

---

## ContextPulse Voice

### MVP (port from Voiceasy)
- [ ] **Hold-to-dictate** — hotkey triggers recording, release transcribes + pastes
- [ ] **Local Whisper** — no cloud, no API keys, runs on CPU
- [ ] **Fix-last** — re-transcribe with higher accuracy (beam_size=10)
- [ ] **System tray** — invisible background process
- [ ] **Settings panel** — model selection, hotkey config, BYOK API key for cloud Whisper
- [ ] **Always-use-AI toggle** — route through LLM for grammar/formatting

### Phase 2: Self-Improving Transcription
- [ ] **Nightly correction analysis** — review LLM corrections to raw transcriptions (already built in Voiceasy)
- [ ] **Custom vocabulary generation** — auto-build word lists from corrections + codebase terms
- [ ] **Auto-fix rules** — common misheard words get fixed before LLM processing
- [ ] **Screen-aware vocabulary** — bias transcription based on what's on screen (Python → "def" not "deaf")
- [ ] **Project vocabulary** — load project-specific terms (class names, API endpoints, team member names)
- [ ] **Accuracy tracking** — measure correction rate over time, show improvement trends

### Phase 3: Context-Rich Voice
- [ ] **Meeting transcription** — continuous recording mode for calls, with speaker diarization
- [ ] **Voice commands for agents** — "Hey Claude, what was I looking at 10 minutes ago?"
- [ ] **Spoken decision capture** — "I'm going with approach B because..." → auto-logs to Memory
- [ ] **Voice annotations** — speak a note about what's on screen, attached to the current frame
- [ ] **Multi-language** — Whisper already supports 99 languages, expose language selection
- [ ] **Voice-to-code** — dictate code with syntax awareness ("define function calculate total taking items as parameter")

### Future
- [ ] **Real-time transcription** — streaming output as you speak (not just on release)
- [ ] **Voice cloning/TTS** — agent reads responses aloud in a natural voice
- [ ] **Ambient listening** — optional always-on mode that captures spoken context without hotkey
- [ ] **Audio context for agents** — "what did I say 5 minutes ago?" like time-travel but for voice

---

## ContextPulse Agent

### Concept (needs validation)
- [ ] **Session protocol** — agents announce themselves, share what they're working on
- [ ] **Conflict detection** — warn when two agents are editing the same file
- [ ] **Session handoff** — Claude Code finishes, Cursor picks up where it left off with full context
- [ ] **Shared project state** — one source of truth for "what's in progress" across all agents
- [ ] **Task routing** — route work to the best agent based on effectiveness scoring
- [ ] **Workflow orchestration** — define multi-step workflows that span multiple agents

### Phase 2: Prediction
- [ ] **Workflow prediction** — learn app-switching patterns, pre-load context for the next app
- [ ] **Agent effectiveness scoring** — track acceptance rates per agent, per task type
- [ ] **Proactive context** — agent pushes relevant context before user asks
- [ ] **Schedule-aware** — "it's Monday morning, you usually review PRs first" → pre-load PR queue

### Future
- [ ] **Agent marketplace** — third-party agents that plug into the ContextPulse context layer
- [ ] **Non-technical agent setup** — YAML-based agent definitions for non-developers
- [ ] **Agent templates** — pre-built workflows (code review, bug triage, daily standup prep)
- [ ] **Supervisor agent** — meta-agent that monitors other agents and intervenes when they go off track

---

## Cross-Product Features

These require multiple products working together:

| Feature | Products | Description |
|---------|----------|-------------|
| **Screen + Voice correlation** | Sight + Voice | "User said 'this is broken' while looking at the dashboard" → auto bug report |
| **Visual memory** | Sight + Memory | "Last time you saw this error, you fixed it by editing config.py" |
| **Voice corrections → Memory** | Voice + Memory | Whisper corrections become permanent vocabulary, shared across sessions |
| **Predictive context loading** | All four | Agent predicts what you'll need, Sight captures it, Memory stores it, Voice captures spoken intent |
| **Session reconstruction** | Sight + Memory + Voice | "What happened in yesterday's debugging session?" → full timeline of screens, decisions, and spoken notes |
| **Onboarding acceleration** | Memory + Agent | New team member gets the project's accumulated context — decisions, conventions, pitfalls |
| **Automated standup** | Sight + Memory | "What did I work on yesterday?" answered automatically from screen activity + decisions |
| **Bug report generation** | Sight + Voice + Memory | Screenshot + error OCR + spoken context + prior related errors → complete bug report |

---

## Platform Infrastructure

| Component | Purpose | Status |
|-----------|---------|--------|
| Ed25519 licensing | License key generation + verification | Built |
| Lambda webhook | Gumroad purchase → license key → SES email | Built |
| MCP protocol | Standard interface for all agent communication | Built |
| Settings panel | Unified settings UI across products | Built (Sight) |
| First-run wizard | Onboarding flow per product | Built (Sight) |
| File logging | Persistent crash diagnostics | Built |
| Full-thread watchdog | Auto-restart crashed daemon threads | Built |
| PyPI distribution | Package distribution | Pending |
| Landing page | Product marketing site | Live (contextpulse.pages.dev) |
| Brand system | Logo family, colors, typography, voice | In progress |

---

## Revenue Model

| Product | Free Tier | Paid Tier | Pricing |
|---------|-----------|-----------|---------|
| Sight | Core capture, 3 MCP tools, basic buffer | Pro: all 10 tools, OCR, smart storage, learning | $29 one-time |
| Memory | — | Full memory system | $29-49 one-time |
| Voice | — | Full voice system | TBD |
| Agent | — | Coordination platform | TBD (likely subscription) |
| Bundle | — | All products | Discounted bundle TBD |

---

## Competitive Landscape

| What We Do | Closest Competitor | Our Advantage |
|-----------|-------------------|---------------|
| Screen capture for AI | Screenpipe ($400, 5-15% CPU) | 1/50th CPU, 1/14th price, MCP-native |
| Cross-agent memory | Claude MEMORY.md, ChatGPT memory | Cross-agent, outcome-based, local |
| Voice-to-text for devs | GitHub Copilot Voice, Whisper CLI | Self-improving, screen-aware, local |
| Agent coordination | CrewAI, Lindy.ai | MCP-native, local-first, not a walled garden |
| All four combined | Nobody | No product combines visual + memory + voice + coordination |
