# Positioning Analysis — ContextPulse

## Date: 2026-03-21

## Competitive Landscape

| Competitor | Positioning | Price | Strengths | Weaknesses | Gap |
|------------|------------|-------|-----------|------------|-----|
| **Screenpipe** | "AI that remembers everything you see, say, and hear" | $400 lifetime / $600 lifetime+pro | 16K+ GitHub stars, full audio+video, cross-platform, app store ecosystem | Heavy (5-15% CPU, 200-500MB RAM), $400 entry price, broad scope dilutes developer focus | Heavyweight for devs who just need screen context for coding |
| **screenshot-mcp** (BradyDouthit) | "Give your AI coding assistant eyes to see UI changes" | Free (GitHub) | Simple, focused on visual verification | Single-shot only, no auto-capture, no OCR, no history, no activity tracking | No always-on capability, manual trigger only |
| **Screenshot Viewer** (MCP Market) | "Find and display recent screenshots in Claude Code" | Free (skill) | Quick to set up, cross-platform | Reads existing screenshots, doesn't capture them; no auto-capture, no OCR | Depends on user taking screenshots manually |
| **Manual Snip Tool** | Built into Windows/Mac | Free | Zero setup, user controls exactly what to capture | Breaks flow, no AI integration, no history, no search, no automation | Time cost compounds: 20+ manual screenshots/day |
| **Claude ContextPulse Sight MCP** | Built-in screenshot reading via `using-contextpulse-sight` skill | Free (internal) | Already integrated into David's workflow | Not productized, no public distribution | Only works for David right now |

## ICP (Ideal Customer Profile)

```
WHO: Developer using AI coding assistants daily (Claude Code, Cursor, Gemini CLI, Copilot)
SITUATION: Works on 2+ projects, uses multiple AI tools, Windows or cross-platform
TRIGGER: Realizes they're screenshotting 10-20 times/day for their AI, or switches between
         AI tools that don't share context
PAIN: "Every AI conversation starts blind — I'm constantly re-explaining what's on my screen"
DESIRE: AI that can see what they see without manual effort, across all their tools
BUDGET: Paying $0-20/mo for AI tools. Willing to pay $29-49 one-time for meaningful
        productivity gain. Won't pay $400 (Screenpipe) unless they need full recording.
CHANNELS: Hacker News, r/ClaudeAI, r/LocalLLaMA, GitHub, Product Hunt, dev.to, Twitter/X
```

## Transformation

**Before:** Developer opens AI assistant, types question about what's on screen, realizes AI can't see it, opens Snip Tool, captures region, pastes into chat, waits for processing. Repeats 15-20 times daily. Each context switch costs 30-60 seconds of flow state. Across multiple AI tools, none share visual context — switching from Claude Code to Cursor means re-explaining everything.

**After:** Developer asks AI assistant anything about their screen. AI already knows what's there — it captured 5 seconds ago. OCR text is searchable. Activity history is queryable ("what was I looking at 10 minutes ago?"). All AI tools share the same visual context through one MCP server. Zero manual effort, zero flow state interruption.

## Positioning Angles

### 1. The Lightweight Champion (Anti-Screenpipe)
"Screenpipe records everything. ContextPulse captures what matters. Under 1% CPU, under 20MB RAM."
- Positions against heavyweight competitor on performance
- Appeals to devs who want context without system drain

### 2. The 30-Second Setup (Speed Weapon)
"pip install, run, connect. Your AI sees your screen in 30 seconds."
- Positions on time-to-value vs Screenpipe's 5-10 min setup + $400 buy decision
- Appeals to developers who want instant results

### 3. The MCP-Native Developer Tool (Niche Specialist)
"Not a screen recorder. Not a memory app. The context layer your MCP agents are missing."
- Positions as infrastructure for the MCP ecosystem, not a consumer product
- Appeals to developers building with MCP who want composable tools

### 4. The Privacy-First Capture (Privacy/Control)
"Your screen never leaves your machine. No cloud. No API keys. No accounts. No telemetry."
- Positions on data ownership in a world of cloud-dependent tools
- Appeals to security-conscious developers and enterprises

### 5. The 10x Cheaper Alternative (Underdog/Value)
"Screenpipe costs $400. ContextPulse Pro costs $29. Same always-on capture, 1/50th the CPU."
- Positions on price against Screenpipe, on value against manual workflow
- Clear, sharp comparison — free tier for adoption, Pro for power users

### 6. The Composable Context Stack (Simplicity + Vision)
"Screen context today. Shared memory tomorrow. Agent coordination next. Each package works alone. Together, they compound."
- Positions the product suite vision, not just Sight
- Appeals to developers who think long-term about their tool stack

## Scoring

| Angle | Resonance | Differentiation | Defensibility | Clarity | Market Size | Total |
|-------|-----------|----------------|---------------|---------|-------------|-------|
| Lightweight Champion | 4 | 5 | 5 | 5 | 4 | **23** |
| 30-Second Setup | 5 | 3 | 3 | 5 | 5 | **21** |
| MCP-Native Dev Tool | 4 | 5 | 5 | 4 | 3 | **21** |
| Privacy-First | 4 | 3 | 4 | 5 | 4 | **20** |
| 10x Cheaper Alt | 5 | 3 | 3 | 5 | 5 | **21** |
| Composable Stack | 3 | 5 | 5 | 3 | 3 | **19** |

## Winning Angle: The Lightweight Champion

**One-liner:** "Your AI can write code but can't see your screen. ContextPulse fixes that — under 1% CPU, under 20MB RAM, in 30 seconds."

**Why this wins:** Highest total score (23/25). It's the hardest angle for competitors to copy — Screenpipe's architecture is fundamentally heavyweight (continuous video+audio recording). ContextPulse's lightweight design is a structural advantage, not a feature that can be bolted on. The angle resonates immediately with any developer who's tried Screenpipe and found it too heavy, or who hasn't tried it because $400 + 5-15% CPU felt like overkill for "I just want my AI to see my screen."

**Combined pitch (Lightweight + Speed + Free):**
"Screenpipe records everything you see, say, and hear — for $400 and 5-15% of your CPU. ContextPulse captures what your AI agents actually need — starting free, Pro for $29, at under 1% CPU. Install in 30 seconds."

## Supporting Angles (backup)

1. **MCP-Native Dev Tool** — "Not a screen recorder. The context layer your MCP agents are missing." Best for MCP ecosystem channels (r/ClaudeAI, MCP directories).
2. **30-Second Setup** — "pip install, run, connect. Done." Best for Product Hunt and Twitter where attention is scarce.
3. **Privacy-First** — "Your screen never leaves your machine." Best for r/LocalLLaMA and privacy-focused audiences.

## Detailed Competitive Analysis (2026-03-21)

### What actually exists in the MCP screenshot space

**On-demand screenshot MCP tools (5+ exist, all free):**
- BradyDouthit/screenshot-mcp — localhost only, Playwright, captures web dev server pages. No desktop.
- sethbang/mcp-screenshot-server — Puppeteer web + native OS screenshots. On-demand only, no daemon.
- Digital Defiance MCP Screenshot — Most feature-rich: multi-format, PII masking, security policies, multi-monitor. Still on-demand only.
- Various others on MCP Market, FastMCP — all single-shot capture tools.

**Screenpipe ($400 lifetime):**
- Full video + audio recording. Heavyweight (5-15% CPU, 200-500MB RAM).
- "Personal AI memory" scope — broad consumer product, not developer-focused.
- Has MCP integration but it's an add-on, not the core interface.

### ContextPulse's UNIQUE features (no competitor has these)

1. **Always-on daemon** — The only MCP tool that runs continuously in the background. Every other MCP screenshot tool requires the AI to explicitly request a capture.
2. **Rolling buffer with change detection** — 30 min of per-monitor frames, stored only when content changes. No other tool tracks temporal context.
3. **Activity database (SQLite + FTS5)** — Searchable history across window titles and OCR text. "What was I looking at when that error happened?" is only answerable with ContextPulse.
4. **Event-driven capture** — Triggers on window focus change, monitor boundary cross, and activity after idle. Not timer-based alone.
5. **Smart storage modes** — 4 modes (smart/visual/both/text). Text-only when text-heavy saves 59% disk. No other tool has storage intelligence.
6. **get_context_at(minutes_ago=N)** — Time-travel tool. No competitor can answer "what was on screen 10 minutes ago?"
7. **search_history(query)** — Full-text search across all captured context. Only Screenpipe has comparable search, at 10-50x resource cost.
8. **<1% CPU at always-on** — The on-demand tools use 0% when idle (they're not running), but they also provide 0% value when idle. ContextPulse is always capturing at <1%.

### The positioning reframe

The comparison isn't "ContextPulse vs other screenshot MCP tools." Those are point-in-time capture tools. ContextPulse is a **continuous context service** — closer to Screenpipe's category but at 1/50th the resource cost and focused specifically on what MCP agents need.

## Next Steps
- [x] Feed winning angle into `direct-response-copy` skill
- [x] Create lead magnet (AI Context Cost Calculator)
- [ ] Update `brand/BRAND.md` with positioning
- [ ] Apply refined differentiators to landing page comparison table
- [ ] Update MARKETING_ASSETS.md with differentiator-focused copy
