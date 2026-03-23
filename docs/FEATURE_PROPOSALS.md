# ContextPulse Sight — Feature Proposals

Generated 2026-03-21 from competitive research and market gap analysis.

## Context

ContextPulse Sight is the only always-on MCP screen capture daemon. Competitors are either on-demand screenshot tools (5+ exist, all single-shot) or heavyweight recorders (Screenpipe, $400, 5-15% CPU). Our existing unique features: rolling buffer, activity DB with FTS5, smart storage (4 modes), event-driven capture, time-travel context, <1% CPU.

These proposals build on that foundation to widen the moat.

---

## Tier 1: High Impact, Buildable Now

### 1. Clipboard Context Capture
**What:** Capture clipboard contents (text, URLs, file paths) alongside screenshots. When a developer copies an error message, stack trace, or URL, store it in the activity database tagged with timestamp and active window.

**Why:** The clipboard is often more informative than the screen. A copied stack trace is searchable text; the screen showing it is a 1,200-token image. No MCP tool captures clipboard context.

**MCP tools:** `get_clipboard_history(count=N)`, `search_clipboard(query)` or integrate into existing `search_history()`.

**Complexity estimate:** Low-medium. Win32 clipboard monitoring via `pyperclip` or `win32clipboard`. Need to filter noise (password managers, rapid copy-paste loops).

---

### 2. MCP Config Generator
**What:** A CLI command `contextpulse-sight --setup claude-code` that auto-generates the MCP JSON config and writes it to the correct settings file for that client.

**Why:** MCP setup is the #1 friction point. Every MCP tool requires manually editing JSON config. Automating this for Claude Code, Cursor, and Gemini CLI would make ContextPulse the easiest MCP server to install — a real onboarding differentiator.

**Supported targets:** `claude-code` (writes to `~/.claude.json` or project `.mcp.json`), `cursor` (writes to `.cursor/mcp.json`), `gemini` (writes to `~/.gemini/settings.json`).

**Complexity estimate:** Low. Read template, detect install paths, write JSON. The hard part is knowing each client's config format (research needed).

---

### 3. Multi-Agent Awareness
**What:** Track which MCP client is connected and what tools they've called. Expose via `get_agent_stats()` tool: "Claude Code: 12 screenshot requests today, last active 3 min ago. Cursor: 0 requests."

**Why:** Tells the user (and agents themselves) which tools are actually consuming context. Feeds directly into the Agent product later. No competitor tracks MCP client usage.

**Implementation:** Log each MCP tool call with client identifier (from MCP session metadata if available, or connection fingerprint). Store in activity DB.

**Complexity estimate:** Low. MCP protocol may expose client info in handshake; if not, track by connection/session ID.

---

### 4. Diff-Aware Capture
**What:** Instead of binary change detection (changed/not), compute a visual diff score (0-100%) between consecutive frames. Tag each frame with diff magnitude. Expose via `get_recent(min_diff=50)` — "only show me frames where the screen changed significantly."

**Why:** Saves token costs. "Screen changed 85%" = likely switched apps (worth processing). "Screen changed 3%" = cursor moved (skip it). Agents can make cost-conscious decisions about which frames to request.

**Implementation:** Pixel-level comparison is already done for change detection. Extend to return a percentage. Could use structural similarity (SSIM) for better perceptual scoring.

**Complexity estimate:** Low. The infrastructure exists; this is adding a numeric score to existing boolean logic.

---

## Tier 2: Strong Differentiators, Medium Effort

### 5. Contextual Annotations
**What:** Let the user tag captures with a note via hotkey (e.g., Ctrl+Shift+N opens a small input, types "debugging auth bug", attaches to current frame). Annotations stored in activity DB, searchable via `search_history()`.

**Why:** User-annotated visual context is unique. When an agent searches for "auth bug," annotated frames surface first. Bridges the gap between raw captures and meaningful context.

**Implementation:** New hotkey handler, small tkinter input dialog (reuse gui_theme from core), store annotation in activity DB linked to nearest frame timestamp.

**Complexity estimate:** Medium. UI dialog + DB schema extension + search integration.

---

### 6. Project-Aware Capture
**What:** Detect which project/repo the user is working in based on: (a) active window title (IDE shows project name), (b) active terminal's cwd, (c) git repo root. Tag each capture with project name. Expose via `get_project_context(project="StockTrader")`.

**Why:** Developers work across multiple projects. "Show me what I was looking at in the StockTrader project" is far more useful than "show me what I was looking at 10 minutes ago." No competitor does project-level context filtering.

**Implementation:** Parse window titles for known IDE patterns (VS Code: "filename - ProjectName", PyCharm: "ProjectName - filename"). Fall back to querying active terminal's cwd. Store project tag in activity DB.

**Complexity estimate:** Medium. Window title parsing is heuristic; different IDEs use different patterns. Needs a pattern registry.

---

### 7. Token Cost Estimation
**What:** Each capture in the buffer includes an estimated Claude API token cost: "This frame = ~1,200 tokens as image, ~45 tokens as OCR text." Expose via `get_buffer_status()` or per-frame metadata.

**Why:** Helps agents make cost-conscious decisions. If OCR text is 45 tokens and the image is 1,200 tokens, an agent should prefer `get_screen_text()` unless it genuinely needs visual layout. No tool provides this.

**Implementation:** Image tokens estimated from dimensions (Claude's formula: ceil(width/768) * ceil(height/768) * 258). OCR tokens estimated from character count / 4. Both are fast calculations on existing data.

**Complexity estimate:** Low. Pure math on existing data. No new capture or storage needed.

---

### 8. Capture Event Stream / Webhooks
**What:** Emit events when interesting things happen: app focus changed, idle detected, screen dramatically changed (diff >50%), annotation added. Other tools can subscribe via local webhook or event file.

**Why:** Makes ContextPulse a platform. Other MCP servers, automation scripts, or the future Agent package can react to context changes in real-time. "When user switches to thinkorswim, start capturing at higher frequency."

**Implementation:** Event bus (simple pub/sub or write to a JSONL event file). Webhook: POST to localhost URL on each event. Could also expose as an MCP resource/subscription if MCP supports it.

**Complexity estimate:** Medium. Event bus architecture + webhook HTTP client + event filtering.

---

## Tier 3: Visionary, Larger Effort

### 9. Screen Narration (Local Vision Model)
**What:** Periodically run a lightweight local vision model (moondream2, ~1.8B params, runs on CPU) to generate a one-sentence description of what's on screen. Store narrations in activity DB. Agents search natural language: "find when I was reviewing the pull request."

**Why:** Text narrations are 10-20 tokens vs 1,200 for an image. Agents can understand context history without processing images at all. Screenpipe does this with cloud AI ($$$). Doing it locally and lightweight would be unique.

**Implementation:** Run moondream2 or similar on a timer (every 30-60s, not every 5s). Queue-based to avoid blocking capture. Store narration text in activity DB alongside frame metadata. GPU optional (CPU works, just slower — 2-5s per narration).

**Complexity estimate:** High. Model download (~3.5GB), inference pipeline, CPU/GPU detection, graceful degradation if model isn't installed.

---

### 10. Cross-Machine Activity Sync
**What:** Sync the activity database (text metadata only, not images) between machines. When you switch from desktop to laptop, your AI agent knows what you were doing on the other machine.

**Why:** Multi-device developers lose context on every machine switch. Syncing text metadata (window titles, OCR text, timestamps, annotations) is tiny bandwidth. Images stay local (privacy). No competitor does cross-machine context.

**Implementation:** SQLite WAL + periodic sync to a shared location (Dropbox, OneDrive, or a simple HTTP server). Conflict resolution: append-only event log, no overwrites. Could also use the future Memory package as the sync layer.

**Complexity estimate:** High. Sync is always hard. Conflict resolution, network failures, merge logic. Best deferred until Memory package exists.

---

## Recommended Build Order

1. **Token cost estimation** (#7) — Trivial to build, immediately useful, differentiating
2. **Diff-aware capture** (#4) — Extends existing code, makes buffer smarter
3. **MCP config generator** (#2) — Biggest onboarding impact, low effort
4. **Clipboard capture** (#1) — High-value new context source
5. **Multi-agent awareness** (#3) — Sets up Agent product, low effort
6. **Project-aware capture** (#6) — High value for multi-project devs
7. **Contextual annotations** (#5) — Unique feature, medium effort
8. **Event stream** (#8) — Platform play, medium effort
9. **Screen narration** (#9) — Visionary, needs model management
10. **Cross-machine sync** (#10) — Defer until Memory package
