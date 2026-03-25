# ContextPulse Feature Roadmap

*Generated 2026-03-24 from competitive analysis and user experience research*

## Competitive Landscape

| Product | Focus | Pricing | Platform | Key Differentiator |
|---------|-------|---------|----------|-------------------|
| **Limitless** (ex-Rewind) | Meeting transcription + $99 pendant | $20/mo + $99 hardware | Mac, pendant | Cloud processing, wearable |
| **Screenpipe** | Open-source screen+audio recording | Free (MIT) | Win/Mac/Linux | Open source, REST API, 16K GitHub stars |
| **Wispr Flow** | Voice dictation | $12-15/mo | Mac/Win/iPhone | Command Mode, 100+ languages, Whisper Mode |
| **Pieces for Developers** | Dev context copilot | Free / $19/mo Pro | Win/Mac/Linux | LTM-2 (9-month rolling memory), snippet management |
| **Granola** | AI meeting notes | Freemium | Mac/Win | Meeting-focused, auto-enriched notes |

### Where ContextPulse Wins
- **Unified modalities** — only product combining screen + voice + touch in one process
- **MCP-native** — 23 tools designed for AI agents (Screenpipe has MCP too, but fewer tools)
- **Cross-modal search** — search across what you saw, said, AND typed simultaneously
- **Correction detection** — Touch module detects voice dictation errors and auto-learns vocabulary
- **Privacy-first** — all local, no cloud, blocklist, auto-pause on lock, OCR redaction
- **Developer-first** — built for coding workflows, not meetings

### Where Competitors Win
- **Screenpipe:** Open source (community trust), REST API (broader integrations), Linux support, audio capture
- **Wispr Flow:** Command Mode ("make this formal"), 100+ languages, quiet Whisper Mode, polished UX
- **Pieces:** 9-month rolling memory, snippet management, IDE plugins (VS Code, JetBrains)
- **Limitless:** Wearable pendant (captures offline conversations), cloud processing (no local CPU)

---

## Ranked Feature Roadmap

### Tier 1: High Value, Ship Next (Score 8-10)

| # | Feature | Description | Value | Effort | Inspired By | Tier |
|---|---------|-------------|-------|--------|-------------|------|
| 1 | **Voice Command Mode** | Natural language commands while dictating: "delete last sentence", "make this a list", "translate to Spanish" | 10 | M | Wispr Flow | Pro |
| 2 | **Cross-Session Memory** | Persistent context that survives restarts — "what was I working on yesterday?", "find that error from 3 hours ago" | 10 | L | Pieces LTM-2 | Pro |
| 3 | **System Audio Capture** | Record system audio + mic for meeting transcription alongside screen | 9 | M | Screenpipe, Limitless | Pro |
| 4 | **Onboarding Polish** | First-run wizard: test mic, test hotkeys, show privacy controls, offer model download | 9 | S | Wispr Flow | Free |
| 5 | **Activity Timeline UI** | Visual timeline of what happened (apps used, dictations, screenshots) — browsable HTML dashboard | 9 | M | Limitless, Screenpipe | Free |

### Tier 2: Strong Value, Build Soon (Score 6-8)

| # | Feature | Description | Value | Effort | Inspired By | Tier |
|---|---------|-------------|-------|--------|-------------|------|
| 6 | **Multi-Language Voice** | Auto-detect and transcribe 100+ languages (Whisper already supports this, just needs UI/config) | 8 | S | Wispr Flow | Free |
| 7 | **REST API** | localhost HTTP API so any tool (not just MCP) can query context | 8 | M | Screenpipe | Pro |
| 8 | **IDE Plugins** | VS Code and JetBrains extensions showing context sidebar (recent captures, voice history) | 8 | L | Pieces | Pro |
| 9 | **Smart Snippets** | Auto-save code blocks from OCR'd screens with language detection and tags | 7 | M | Pieces Drive | Pro |
| 10 | **Quiet/Whisper Mode** | Low-volume dictation mode for open offices — auto-boost sensitivity | 7 | S | Wispr Flow | Free |
| 11 | **Tray Status Overlay** | Floating mini-widget showing recording status, last dictation, module health | 7 | S | — | Free |
| 12 | **macOS Port** | Mac version (replace Win32 APIs with macOS equivalents) | 7 | L | Wispr Flow, Screenpipe | Free |

### Tier 3: Nice to Have, Future (Score 4-6)

| # | Feature | Description | Value | Effort | Inspired By | Tier |
|---|---------|-------------|-------|--------|-------------|------|
| 13 | **Screen Narration** | Local vision model generates natural-language summaries of what's on screen | 6 | M | — | Pro |
| 14 | **Auto-Update** | Silent background updates with rollback | 6 | M | — | Free |
| 15 | **Attention Scoring** | ML model scores each event's importance (typing burst in IDE > idle scroll in Twitter) | 6 | L | Pieces LTM-2 | Pro |
| 16 | **Custom Hotkey Builder** | GUI to remap all hotkeys with conflict detection | 5 | S | — | Free |
| 17 | **Export/Sync** | Export activity data to JSON/CSV, optional encrypted cloud sync (metadata only) | 5 | M | — | Pro |
| 18 | **Linux Support** | Linux version (X11/Wayland screen capture, PulseAudio) | 5 | L | Screenpipe | Free |
| 19 | **Meeting Mode** | Auto-detect video calls, switch to continuous transcription + summary | 5 | M | Granola, Limitless | Pro |
| 20 | **Agent Coordination** | Shared context layer between Claude Code, Gemini CLI, Cursor — each sees what the others did | 5 | L | — | Pro |

---

## Revenue Strategy

| Tier | Price | What's Included |
|------|-------|-----------------|
| **Free** | $0 | Sight (screen capture, OCR, clipboard), Voice (basic dictation), Touch (typing/mouse capture), 10 free MCP tools |
| **Starter** | $8/mo | Cross-modal search, REST API, multi-language voice, 7-day memory |
| **Pro** | $19/mo | Unlimited memory, Command Mode, audio capture, IDE plugins, attention scoring, screen narration |

**Comparable pricing:** Wispr Flow $12-15/mo, Pieces Pro $19/mo, Limitless $20/mo

---

## Recommended Build Order (Q2 2026)

1. **Onboarding Polish** (S) — first impressions matter, needed before any launch
2. **Voice Command Mode** (M) — the #1 feature that would make users switch from Wispr Flow
3. **Activity Timeline UI** (M) — makes the product tangible ("look what it captured")
4. **Multi-Language Voice** (S) — quick win, Whisper already supports it
5. **Cross-Session Memory** (L) — the killer feature that justifies Pro pricing
6. **System Audio Capture** (M) — unlocks meeting use case
7. **REST API** (M) — opens integrations beyond MCP

---

## Sources
- [Screenpipe vs Limitless 2026](https://screenpi.pe/blog/screenpipe-vs-limitless-2026)
- [Wispr Flow Pricing](https://wisprflow.ai/pricing)
- [Pieces for Developers Review](https://aichief.com/ai-productivity-tools/pieces-for-developers/)
- [Limitless Pricing & Plans](https://help.limitless.ai/en/articles/9129649-pricing-plans)
- [Screenpipe GitHub](https://github.com/mediar-ai/screenpipe)
