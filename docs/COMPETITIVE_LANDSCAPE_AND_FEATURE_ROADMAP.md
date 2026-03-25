# ContextPulse — Competitive Landscape & Prioritized Feature Roadmap

**March 2026 | Jerard Ventures LLC | Confidential**

---

## Part 1: Competitive Landscape (Updated March 2026)

### Competitor Summary Table

| Competitor | Category | Platform | Pricing | Status |
|-----------|----------|----------|---------|--------|
| **Limitless (ex-Rewind)** | Screen recording + AI search | Mac (was) | Was $20/mo + $99 pendant | Acquired by Meta Dec 2025; desktop product shut down |
| **Screenpipe** | Open-source screen + audio memory | Win/Mac/Linux | $400 lifetime | Active; pivoting toward desktop automation SDK |
| **Microsoft Recall** | OS-level screen indexing | Windows (Copilot+ PCs only) | Free (requires $800-1500 NPU hardware) | GA April 2025; excludes ~85-90% of dev machines |
| **Pieces for Developers** | Context-aware code copilot + LTM | Win/Mac/Linux | Free (individual); Teams contact sales | Active; strong MCP + LTM-2 integration |
| **Granola** | AI meeting notepad | Mac (Win beta) | Free (25 meetings) / $18/mo / $14/user/mo | Active; $250M valuation; MCP in Business tier |
| **Wispr Flow** | Voice dictation | Mac/Win/iOS | Free (2K words/wk) / $15/mo | Active; cloud-dependent, no offline |
| **Talon Voice** | Hands-free voice coding | Win/Mac/Linux | Free (Patreon $25/mo for beta) | Active; niche accessibility/RSI community |
| **Screen Studio** | Screen recording for demos | Mac only | $29/mo or $108/yr | Active; moved to subscription Sept 2025 |
| **Recall.ai** | Meeting recording API | API (no end-user app) | $0.50/hr recording + $0.15/hr transcription | Active; B2B API play, not consumer |
| **Otter.ai** | Meeting transcription | Web/Win/Mac | Free / $8.33/mo / $20/user/mo | Active; $100M ARR; MCP server live |
| **Bee (Amazon)** | Wearable ambient AI | Wearable | $49.99 device | Acquired by Amazon July 2025; wearable-only |

---

### Detailed Competitor Analysis

#### 1. Limitless (formerly Rewind.ai) — DEAD

**What they did well:** Natural language search over screen history; wearable pendant for in-person conversations; polished Mac UX.

**What ContextPulse does that they didn't:** MCP-native agent interface, pre-storage data redaction, <1% CPU overhead, Windows-first, $29 entry vs $339 Y1 cost, no cloud account required, keyboard/mouse capture, clipboard monitoring.

**Strategic takeaway:** Meta acquired for talent/architecture at ~$2M ARR, validating the category. Desktop product killed Dec 19, 2025. The market leader vacancy is ContextPulse's opportunity.

#### 2. Screenpipe — PRIMARY COMPETITOR

**What they do well:** Cross-platform (Win/Mac/Linux); open-source core (MIT, 17,200+ stars); pipe store ecosystem; continuous video recording gives complete visual history; pivoting to "Terminator" desktop automation SDK.

**What ContextPulse does that they don't:** <1% CPU (vs 5-15%); pre-storage OCR redaction; per-monitor independent capture; content-adaptive storage (120MB/hr vs 2-5GB/hr); clipboard monitoring; per-agent usage tracking; token cost estimation; multi-agent shared context; $29 vs $400 entry.

**Pricing:** $400 one-time lifetime license. No subscription = limited recurring revenue.

**Key vulnerability:** Their pivot toward desktop automation (Terminator SDK) means they're becoming a different product. ContextPulse remains focused on passive context delivery.

#### 3. Microsoft Recall — GATED THREAT

**What they do well:** Deep OS integration; NPU-accelerated local processing; encrypted VBS enclave; massive distribution potential.

**What ContextPulse does that they don't:** Runs on ANY PC (no NPU/40 TOPS requirement); MCP API; pre-storage redaction; per-monitor capture; developer focus; cross-platform roadmap; keyboard/mouse/clipboard capture.

**Key vulnerability:** Hardware-gated to Copilot+ PCs ($800-1500+). ~85-90% of developer machines cannot run it. No MCP API. Microsoft pulling back on forced AI features in Windows 11 (March 2026). Enterprise hardware refresh cycles are 3-5 years, keeping this gate up through 2027-2028.

#### 4. Pieces for Developers — CLOSEST ARCHITECTURAL COMPETITOR

**What they do well:** LTM-2 captures OS-level work activity over 9-month rolling window; MCP server with Claude Cowork integration (Jan 2026); free individual tier; cross-platform; local-first encrypted storage; snippet management; IDE integrations (VS Code, JetBrains, Obsidian).

**What ContextPulse does that they don't:** Visual screen capture with OCR; per-monitor independent capture; pre-storage redaction of sensitive data; voice dictation; keyboard/mouse capture with correction detection; clipboard monitoring with search; content-adaptive storage; token cost estimation; privacy blocklist for windows.

**Key insight:** Pieces is the most architecturally similar competitor. Their LTM-2 is essentially a memory layer that captures browser/IDE/terminal activity. However, they capture metadata and code snippets -- not visual screen content, voice, or input device signals. ContextPulse captures the raw sensory layer (what you see, say, type); Pieces captures the semantic layer (what tools you used, what code you touched). These are complementary more than competitive, but Pieces' MCP+LTM momentum is a signal to accelerate ContextPulse's Memory module.

#### 5. Granola — MEETING-ONLY, NOT DIRECT COMPETITOR

**What they do well:** Invisible meeting capture (no bot joining calls); local audio recording; Recipes system (29 templates); MCP in Business tier; Outlook calendar integration; file attachments for meeting context; $250M valuation at $67M raised.

**What ContextPulse does that they don't:** Always-on capture outside meetings; screen OCR; keyboard/mouse capture; clipboard monitoring; developer-focused MCP tooling; pre-storage redaction; $29 vs $18/mo.

**Key insight:** Granola's $250M valuation at undisclosed ARR validates premium multiples for "ambient AI context" products. Their MCP move confirms context-to-agent delivery is the right direction. Not a direct competitor but a strong market comp.

#### 6. Wispr Flow — VOICE NICHE

**What they do well:** 97.2% transcription accuracy; Command Mode for voice editing; 100+ language support with auto-detection; works in any app; polished Mac/Win/iOS UX.

**What ContextPulse does that they don't:** Screen capture + OCR; clipboard monitoring; keyboard/mouse capture; MCP integration; privacy blocklist; local processing (Wispr is cloud-only, no offline); pre-storage redaction; screen-aware vocabulary biasing (planned).

**Key vulnerability:** Cloud-dependent -- no offline mode at all. $15/mo subscription vs ContextPulse's planned one-time pricing for voice. Wispr's accuracy advantage is real but ContextPulse's screen-aware vocabulary biasing (planned) could close the gap for developer use cases.

**Feature inspiration:** Wispr's Command Mode (voice editing commands) and auto-language detection are strong UX patterns to learn from.

#### 7. Talon Voice — ACCESSIBILITY NICHE

**What they do well:** Complete hands-free computer control; eye tracking integration; noise recognition; deep customization via Python scripts; strong RSI/disability community.

**What ContextPulse does that they don't:** Screen capture + OCR; clipboard monitoring; MCP integration; always-on context capture; GUI settings panel; commercial support.

**Key insight:** Talon's community proves demand for accessibility-focused input tools among developers. ContextPulse's AT positioning (USPTO Class 10) could capture this audience once voice + accessibility features mature. Talon is free/Patreon-supported -- not a commercial threat but a community to partner with.

#### 8. Screen Studio — RECORDING NICHE, NOT COMPETITOR

**What they do well:** Beautiful auto-enhanced recordings; cursor smoothing; auto-zoom on clicks; local transcription for subtitles.

**Not a direct competitor:** Screen Studio is for producing demo videos, not for AI context capture. However, their auto-zoom and cursor enhancement UX patterns are relevant for ContextPulse's planned video clip export feature.

#### 9. Recall.ai — B2B API, NOT CONSUMER

**What they do well:** Meeting bot API across all major platforms (Zoom, Meet, Teams, Webex, Slack Huddles); $0.50/hr pricing; Desktop Recording SDK for local capture; real-time transcripts within 10 seconds.

**Not a direct competitor:** Recall.ai is a B2B API for companies building meeting intelligence products. Not a consumer/developer tool. However, their Desktop Recording SDK concept (local call capture without a bot) is a pattern ContextPulse could adopt for meeting transcription.

#### 10. Additional Players

**Otter.ai:** $100M ARR, 35M+ users, MCP server live. Meeting-only. Strong in enterprise (HIPAA, SOC 2). Not competing in always-on desktop context.

**Bee (Amazon):** Wearable ambient AI wristband ($49.99). Always-listening, processes conversations in real-time, deletes audio. Acquired by Amazon July 2025. Wearable-only -- validates ambient context capture demand but completely different form factor.

---

### Competitive Gaps: What Nobody Does Well

These gaps represent the highest-value feature opportunities:

| Gap | Who's Closest | ContextPulse Advantage |
|-----|--------------|----------------------|
| **Cross-modal context** (screen + voice + keyboard + mouse in one tool) | Nobody | Only product building all four modalities |
| **Pre-storage data redaction** | Nobody | 10+ pattern categories; compliance differentiator |
| **MCP-native always-on context** | Pieces (MCP + LTM, but no screen/voice) | Full sensory capture + MCP-native from day one |
| **Developer-first voice dictation** (screen-aware, code-aware) | Wispr (general-purpose) | Screen-aware vocabulary biasing planned |
| **Keyboard/mouse analytics for AI agents** | Nobody | Typing fatigue detection, correction cascading |
| **Sub-1% CPU always-on capture** | Nobody comes close | Architectural advantage; competitors cannot match without full rewrite |
| **Local meeting transcription without a bot** | Granola (audio only, cloud processing) | Local Whisper, no cloud, MCP delivery |
| **Accessibility via MCP** (AT use case) | Nobody | USPTO Class 10 filing; unique regulatory positioning |

---

## Part 2: Prioritized Feature Roadmap

### Scoring Methodology

**Value Score (1-10)** = User Impact (1-5) x Feasibility (1-2)
- User Impact: How much does a developer user love/need/pay-for this?
- Feasibility: 1 = standard engineering; 2 = leverage existing architecture

**Effort:** S = days to a week | M = 1-3 weeks | L = 1-2 months | XL = 2+ months

**Revenue tier:** Free = drives adoption | Starter = included in $29 Pro | Pro = justifies upgrade | Subscription = Team/Enterprise tier

---

### Tier 1: Ship Now (Q2 2026) — Love + Pay + Tell

These features make the existing product stickier, more valuable, and more shareable.

| # | Feature | Description | Value | Effort | Inspired By | Revenue |
|---|---------|-------------|-------|--------|-------------|---------|
| 1 | **Screen Narration (Local Vision)** | Run lightweight local vision model (moondream2/LLaVA) to generate one-sentence descriptions per frame. Agents search natural language without processing images. | **10** | L | Pieces LTM-2 concept | Pro |
| 2 | **Voice Module MVP** | Hold-to-dictate with local Whisper, paste into any app. Port from Voiceasy. Adds second modality. | **10** | L | Wispr Flow | Pro |
| 3 | **Contextual Annotations** | Hotkey to attach voice/text note to current screen frame. Agents find annotated frames via search. "This is the bug." | **9** | M | Nobody (unique) | Starter |
| 4 | **OCR Confidence per App** | Track OCR accuracy by window class, build per-app profiles. Auto-adjust preprocessing for dark themes, small fonts. | **9** | M | Pieces per-app awareness | Starter |
| 5 | **Capture Frequency Auto-Tuning** | Learn per-app change rates, adjust capture interval dynamically. Terminal changes fast, PDF viewer rarely. | **8** | M | Screenpipe event-driven | Starter |
| 6 | **Video Clip Export** | Stitch rolling buffer frames into short MP4/GIF clips for bug reports. One hotkey = 30-second replay clip. | **8** | M | Screen Studio | Pro |
| 7 | **Multi-User Awareness** | Auto-pause when screensharing (detect Zoom/Teams/Meet window). Prevent capturing confidential meetings. | **8** | S | Granola meeting detection | Free |

### Tier 2: Build Next (Q3 2026) — Deepen Moat + Expand TAM

| # | Feature | Description | Value | Effort | Inspired By | Revenue |
|---|---------|-------------|-------|--------|-------------|---------|
| 8 | **Screen-Aware Voice Vocabulary** | Bias Whisper transcription based on OCR text on screen. If IDE shows Python, "def" not "deaf." | **10** | L | Nobody (unique, cross-modal) | Pro |
| 9 | **Memory Module MVP** | Persistent key-value memory for agents across sessions. MCP tools: memory_store, recall, search, forget. SQLite-backed. | **10** | L | Pieces LTM-2 | Pro |
| 10 | **macOS Support** | Cross-platform capture using mss (already supports macOS). Unlocks 30-40% of developer market. | **9** | XL | Screenpipe, Wispr Flow | Starter |
| 11 | **Project-Aware Capture** | Auto-detect active project from window title, IDE, git repo. Tag all captures with project context. | **8** | M | Pieces project awareness | Starter |
| 12 | **Capture Webhooks / Event Stream** | Emit events on app switch, idle detection, dramatic screen change. Other tools subscribe. Platform play. | **8** | M | Nobody (platform architecture) | Pro |
| 13 | **Privacy Auto-Detection** | Learn which apps user manually pauses for, auto-add to blocklist. Reduces friction, builds trust. | **7** | M | Nobody (unique) | Starter |
| 14 | **Meeting Transcription Mode** | Continuous voice recording during calls with speaker diarization. Local Whisper. No bot joining. | **7** | L | Granola, Otter.ai | Pro |

### Tier 3: Differentiate (Q4 2026 - Q1 2027) — Enterprise + Subscription Value

| # | Feature | Description | Value | Effort | Inspired By | Revenue |
|---|---------|-------------|-------|--------|-------------|---------|
| 15 | **Session Summaries** | Auto-generate end-of-day/session summary from screen activity + decisions + voice notes. "What did I work on today?" | **9** | L | Pieces LTM-2, Otter.ai | Pro |
| 16 | **Frame Relevance Scoring** | Track when agents use vs ignore frames. Prioritize high-value captures, reduce noise. ML-lite feedback loop. | **8** | L | Nobody (unique) | Pro |
| 17 | **Code-Aware OCR** | Detect programming language from screen content, apply language-specific OCR postprocessing. Improve accuracy for code. | **8** | M | Pieces code awareness | Pro |
| 18 | **Smart Storage Evolution** | Learn optimal storage mode per app (terminal = text-only, Figma = visual). Reduces disk usage further. | **7** | M | Nobody (unique) | Starter |
| 19 | **Screenshot Diffing** | Visual diff between consecutive frames. Highlight what changed. Agents detect UI regressions. | **7** | M | Screen Studio visual polish | Pro |
| 20 | **Cross-Machine Sync** | Sync activity metadata (not images) between machines. Desktop + laptop context unified. | **7** | XL | Pieces cross-device | Subscription |
| 21 | **Correction Cascading to Memory** | User voice corrections become permanent vocabulary. Keyboard corrections feed back to voice model. Cross-modal learning. | **9** | L | Nobody (patented concept) | Pro |
| 22 | **Bug Report Generator** | One command: screenshot + error OCR + clipboard + voice annotation + related prior errors = complete bug report. | **8** | L | Nobody (cross-modal) | Pro |

### Tier 4: Visionary (Q2-Q4 2027) — Moat Deepening

| # | Feature | Description | Value | Effort | Inspired By | Revenue |
|---|---------|-------------|-------|--------|-------------|---------|
| 23 | **Typing Fatigue Detection** | Physiological signal from keyboard timing speed decay regression. Alert when developer needs a break. | **8** | L | Talon Voice accessibility | Pro |
| 24 | **Agent Coordination Protocol** | Agents announce themselves, share work state. Detect conflicts (two agents editing same file). Session handoff. | **8** | XL | Nobody (unique) | Subscription |
| 25 | **Linux Support** | Wayland + X11 capture. Completes cross-platform story. | **7** | XL | Screenpipe | Starter |
| 26 | **Workflow Prediction** | Learn app-switching patterns, pre-load context for the next app before user switches. | **7** | XL | Pieces LTM-2 patterns | Subscription |
| 27 | **Pointer Tremor Detection** | AI-native motor impairment assessment from pointer data. Maps to ALS/Parkinson's early indicators. | **7** | XL | Nobody (patent candidate) | Subscription |
| 28 | **Voice-to-Code** | Dictate code with syntax awareness. "Define function calculate total taking items as parameter" becomes `def calculate_total(items):` | **7** | XL | Talon Voice, Wispr Flow | Pro |
| 29 | **IDE Extension** | VS Code/Cursor extension that feeds ContextPulse context directly into the editor sidebar. | **6** | L | Pieces IDE integrations | Pro |
| 30 | **Team Memory** | Shared memory across team members. Onboard new developers with accumulated project context. Requires cloud sync. | **6** | XL | Pieces Teams tier | Subscription |

---

## Part 3: Strategic Feature Priorities

### What Makes Developers LOVE the Product (Daily Delight)

1. **Screen Narration** -- agents understand what's on screen without image tokens. Feels magical.
2. **Voice Module** -- dictate thoughts, annotations, commit messages. 4x faster than typing.
3. **Contextual Annotations** -- "bookmark" a screen moment with one hotkey. Agents find it later.
4. **Session Summaries** -- "what did I work on today?" answered automatically. Standup prep in seconds.
5. **Bug Report Generator** -- one command produces a complete, context-rich bug report.

### What Makes Developers PAY (Clear ROI)

1. **Memory Module** -- agents remember context across sessions. Eliminates repetitive context-setting. Saves 15-30 minutes/day.
2. **Screen-Aware Voice Vocabulary** -- dramatically improves dictation accuracy for code. Unique capability.
3. **Video Clip Export** -- replaces manual screenshotting for bug reports. Time savings compound.
4. **Meeting Transcription** -- replaces $8-20/mo Otter/Granola subscription with local, private alternative.
5. **macOS Support** -- unlocks the platform where 30-40% of developers work.

### What Makes Developers TELL OTHERS (Word-of-Mouth Triggers)

1. **<1% CPU** -- "it literally runs and I forget it's there" is a shareable claim.
2. **Pre-storage redaction** -- "it redacts API keys before they ever hit disk" is a trust-building differentiator.
3. **Cross-modal learning** -- "my voice dictation got better because it reads my screen" is a wow moment.
4. **$29 one-time** -- "I paid $29 once and it replaced $15/mo Wispr + $18/mo Granola" is compelling.
5. **MCP-native** -- "Claude Code just knows what I'm looking at" shared in developer communities.

---

## Part 4: Build-Order Recommendation

### Phase A: Voice + Annotations (April-May 2026)
Ship Voice MVP + Contextual Annotations + Multi-User Awareness. This adds the second modality and makes existing screen capture dramatically more useful. Launch on Product Hunt with "the AI context tool that sees and hears."

### Phase B: Intelligence Layer (June-July 2026)
Ship Screen Narration + OCR Confidence + Capture Auto-Tuning + Video Clip Export. This makes the product smarter and more efficient over time. The screen narration feature alone is a major differentiator that no competitor offers.

### Phase C: Memory + Cross-Platform (August-October 2026)
Ship Memory Module + macOS Support + Project-Aware Capture. Memory is the second revenue product. macOS unlocks massive TAM. Together they enable the "ContextPulse remembers everything, everywhere" narrative.

### Phase D: Cross-Modal + Enterprise (Q4 2026 - Q1 2027)
Ship Screen-Aware Voice + Meeting Transcription + Session Summaries + Correction Cascading. This is where the cross-modal flywheel kicks in. Each modality improves the others. Enterprise features (session summaries, meeting transcription) justify Team tier pricing.

### Phase E: Moat Deepening (Q2-Q4 2027)
Ship Agent Coordination + Typing Fatigue + Linux + Workflow Prediction. These features build switching costs and deepen the competitive moat. The accessibility features (fatigue detection, tremor detection) open the AT market channel.

---

## Part 5: Revenue Impact Projections

| Phase | Key Features | Expected Impact on Pro Conversions | Subscription Enabler |
|-------|-------------|-----------------------------------|---------------------|
| A | Voice + Annotations | +30% conversion (second modality) | No |
| B | Screen Narration + Clips | +20% conversion (developer delight) | No |
| C | Memory + macOS | +50% TAM expansion (platform + product) | Memory as add-on |
| D | Cross-Modal + Meetings | +25% conversion (replaces paid tools) | Team tier justification |
| E | Agent Coord + Accessibility | Enterprise channel opener | Enterprise tier justification |

---

*Sources consulted for this research:*
- [Limitless/Rewind acquisition](https://techcrunch.com/2025/12/05/meta-acquires-ai-device-startup-limitless/)
- [Screenpipe GitHub](https://github.com/screenpipe/screenpipe) and [blog](https://screenpi.pe/blog/screenpipe-vs-limitless-2026)
- [Limitless pricing](https://help.limitless.ai/en/articles/9129649-pricing-plans)
- [Recall.ai pricing](https://www.recall.ai/blog/new-recall-ai-pricing-for-2026)
- [Granola Series B](https://techcrunch.com/2025/05/14/ai-note-taking-app-granola-raises-43m-at-250m-valuation-launches-collaborative-features/)
- [Wispr Flow pricing](https://wisprflow.ai/pricing)
- [Pieces MCP + LTM](https://pieces.app/blog/mcp-memory)
- [Talon Voice](https://talonvoice.com/)
- [Otter.ai pricing](https://otter.ai/pricing)
- [Screen Studio pricing](https://www.saasworthy.com/product/screen-studio/pricing)
- [Bee/Amazon acquisition](https://techcrunch.com/2025/07/22/amazon-acquires-bee-the-ai-wearable-that-records-everything-you-say/)
- [Microsoft Recall status](https://support.microsoft.com/en-us/windows/retrace-your-steps-with-recall-aa03f8a0-a78b-4b3e-b0a1-2eb8ac48701c)
- [Developer AI adoption stats](https://www.index.dev/blog/developer-productivity-statistics-with-ai-tools)

*Last updated: March 24, 2026*
