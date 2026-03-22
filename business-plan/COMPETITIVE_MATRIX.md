# ContextPulse — Competitive Matrix

**March 2026 | Jerard Ventures LLC | Confidential**

---

## Competitor Landscape Overview (Current as of March 2026)

### Status Summary

| Competitor | Status | Funding | Key Fact |
|------------|--------|---------|----------|
| **Microsoft Recall** | Shipping (Copilot+ PCs only) | Bundled with Windows | Requires 40+ TOPS NPU, 16GB RAM — excludes ~85–90% of developer machines |
| **Screenpipe** | Active, open-source | Founders Inc. (Oct 2024) + Embedding VC + Top Harvest Capital; no large VC round | 17,200+ GitHub stars; $400 lifetime desktop app; video-first + new "Terminator" computer-use SDK layer |
| **Limitless AI** (formerly Rewind) | **ACQUIRED by Meta (Dec 2025)** | $33M raised; peaked at ~$350M valuation | Desktop screen capture shut down Dec 19, 2025; pendant discontinued; team absorbed by Meta |
| **Granola AI** | Active, growing | $67M total ($43M Series B at $250M valuation, May 2025) | Meeting notes only (no screen capture); audio-first; macOS; 10% WoW user growth; **MCP integration now in Business tier** |
| **Otter.ai** | Active, enterprise push | ~$70M raised | $100M ARR (Dec 2025); 35M+ registered users; HIPAA compliance (Jul 2025); **MCP Server now live**; meeting transcription + AI Agents |
| **On-demand MCP tools** | Fragmented, many | N/A (individual OSS projects) | Single-shot; no history; no always-on daemon |

Sources: [TechCrunch Meta/Limitless acquisition](https://techcrunch.com/2025/12/05/meta-acquires-ai-device-startup-limitless/) | [Granola Series B](https://techcrunch.com/2025/05/14/ai-note-taking-app-granola-raises-43m-at-250m-valuation-launches-collaborative-features/) | [Otter.ai $100M ARR](https://otter.ai/blog/otter-ai-caps-transformational-2025-with-100m-arr-milestone-industry-first-ai-meeting-agents-and-global-enterprise-expansion) | [Screenpipe GitHub](https://github.com/mediar-ai/screenpipe)

**Strategic implication:** The market leader (Limitless/Rewind) was acquired and its desktop product was killed in December 2025. This eliminates the most feature-rich competitor while validating the category. The remaining landscape has no dominant cross-modal, on-device, MCP-native player.

---

## Detailed Competitor Profiles

### Microsoft Recall
**Sources:** [Microsoft Recall support docs](https://support.microsoft.com/en-us/windows/retrace-your-steps-with-recall-aa03f8a0-a78b-4b3e-b0a1-2eb8ac48701c) | [Pureinfotech hardware requirements](https://pureinfotech.com/windows-recall-hardware-requirements/) | [Wikipedia: Windows Recall](https://en.wikipedia.org/wiki/Windows_Recall)

- **What it does:** Continuous screenshot indexing with AI semantic search. Runs on Windows 11 Copilot+ PCs.
- **Hardware gate:** Requires NPU with 40+ TOPS (Qualcomm Snapdragon X, AMD Ryzen AI 300, Intel Core Ultra 200V). Minimum 16GB RAM, 256GB storage. Windows Hello enrollment required.
- **Availability:** General availability April 2025 (U.S., UK, Canada, Australia, NZ); **EU/EEA rollout completed late 2025** (August Patch Tuesday target), with region-specific privacy controls including encrypted snapshot export codes.
- **Pricing:** Free — bundled with hardware costing $800–$1,500+ (Copilot+ PC premium).
- **Privacy posture:** Opt-in, local-only, encrypted, requires PIN/biometric to access. Users worldwide can now reset Recall and delete all stored data. EU users get additional export/reset controls.
- **Microsoft Copilot pullback (March 2026):** Microsoft canceled several planned Copilot integrations in Windows 11, halted forced Copilot app installation, and publicly acknowledged Windows 11 "went off track." AI features are being made more optional. This signals internal pressure on AI-feature rollout velocity. ([gHacks, March 2026](https://www.ghacks.net/2026/03/16/microsoft-cancels-several-planned-copilot-integrations-in-windows-11/))
- **Microsoft MCP support (separate from Recall):** Native MCP support reached general availability in Copilot Studio (March 2026) — but this is enterprise Copilot Studio, not Windows Recall. Recall itself still has no MCP API. ([Microsoft Copilot Studio MCP GA](https://www.microsoft.com/en-us/microsoft-copilot/blog/copilot-studio/model-context-protocol-mcp-is-now-generally-available-in-microsoft-copilot-studio/))
- **Key limitations:** No external API, no MCP integration in Recall, Windows-only, hardware-gated, no developer focus, no pre-storage redaction, no per-monitor capture.
- **Installed base reality:** Analysts estimate 10–15% of PC refresh cycles in 2025 qualified as Copilot+. Enterprise hardware refresh cycles are 3–5 years. The vast majority of developers cannot run Recall through at least 2027.

### Screenpipe
**Sources:** [GitHub repo — screenpipe/screenpipe](https://github.com/screenpipe/screenpipe) *(repo migrated from mediar-ai/screenpipe)* | [Screenpipe website](https://screenpi.pe/)

- **What it does:** Continuous screen + audio recording daemon with AI search. Open-source core (MIT), paid desktop app.
- **Architecture:** Video recording (continuous frame capture) — the reason for 5–15% CPU. Not a bug; it is the core design.
- **Platform:** Windows, macOS, Linux (cross-platform advantage over MS Recall).
- **Pricing:** Core engine free (MIT); desktop app $400 one-time lifetime. Third-party "pipes" (plugins) in developer store with Stripe integration.
- **Funding:** Backed by Founders, Inc. (Oct 2024) + Embedding VC + Top Harvest Capital; no large disclosed VC round; ran $12K developer hackathon (February 2025). Enterprise testers reportedly include Microsoft, Intel, Oracle, GitHub, and Alibaba Cloud.
- **Traction:** **17,200+ GitHub stars**, 1,300+ forks; trended #1 on GitHub November 2024. Repo recently migrated to `screenpipe/screenpipe`.
- **MCP integration:** Partial (live in `modelcontextprotocol/servers` registry). Not the primary interface.
- **Pivot indicator (March 2025):** Launched "Screenpipe Terminator" — described as "Playwright but for your desktop," a computer-use SDK built on OS APIs (claiming 100x faster than vision-based alternatives). This is a strategic pivot from capture daemon → desktop automation SDK. Signals they are moving up the stack, away from pure context delivery toward automation.
- **Pipe store:** Launched app store with $1,000 bounty per approved plugin PR + Stripe passive income for plugin authors.
- **Key limitations:** 5–15% CPU (architectural — cannot be fixed without full rewrite); 200–500MB RAM; ~2–5GB/hr video storage; no pre-storage redaction; no per-monitor independent capture; no token cost estimation; no accessibility positioning; $400 entry excludes casual adoption.
- **Business model weakness:** One-time $400 with no subscription = limited recurring revenue, no enterprise ceiling. Cannot scale to enterprise without fundamentally changing their go-to-market.
- **Strategic gap:** Their pivot toward desktop automation means they are becoming a different product category. ContextPulse remains focused on passive context delivery for AI agents — a cleaner, more focused position.

### Limitless AI (Formerly Rewind AI) — ACQUIRED AND SHUT DOWN
**Sources:** [TechCrunch](https://techcrunch.com/2025/12/05/meta-acquires-ai-device-startup-limitless/) | [9to5Mac](https://9to5mac.com/2025/12/05/rewind-limitless-meta-acquisition/) | [CNBC](https://www.cnbc.com/2025/12/05/meta-limitless-ai-wearable.html) | [Sacra analysis](https://sacra.com/research/why-meta-bought-limitless/)

- **Status:** Acquired by Meta, December 5, 2025. Desktop screen/audio capture **shut down December 19, 2025.** Pendant hardware discontinued. Up to 50 employees absorbed into Meta AI.
- **Pre-acquisition funding:** $33M+ raised; Series A at ~$350M valuation (January 2023, NEA led); investors included Sam Altman, First Round Capital, a16z.
- **ARR at acquisition:** ~$2M (analyst estimate; not publicly confirmed).
- **Exit terms:** Undisclosed. Peak valuation ~$350M; exit may have been flat-to-down per Sacra. Characterized as strategic acqui-hire for talent and AI architecture — not a revenue multiple story.
- **What Meta acquired:** Ambient AI team + wearable context architecture. The gap left: **desktop context** (screen, keyboard, pointer) — exactly ContextPulse's domain.
- **Strategic lesson:** Major platforms pay strategic prices for context-capture infrastructure well before revenue scale. But Limitless's cloud dependency and hardware requirement limited its reach and likely suppressed its exit price. ContextPulse's on-device, software-only, developer-first architecture is a stronger strategic asset.

### Granola AI
**Sources:** [TechCrunch Series B](https://techcrunch.com/2025/05/14/ai-note-taking-app-granola-raises-43m-at-250m-valuation-launches-collaborative-features/) | [Sifted Series B](https://sifted.eu/articles/note-taking-app-granola-raise) | [Granola pricing](https://www.granola.ai/pricing)

- **What it does:** AI-powered meeting notes with no visible bot. Audio-only during meetings; no always-on background daemon outside meetings.
- **Platform:** macOS primary (Windows beta).
- **Funding:** $4.25M seed + $20M Series A (Lightspeed, Oct 2024) + $43M Series B (NFDG: Nat Friedman + Daniel Gross, May 2025) = **$67M total at $250M valuation**.
- **Notable Series B angels:** Guillermo Rauch (Vercel), Amjad Masad (Replit), Tobias Lütke (Shopify), Karri Saarinen (Linear).
- **Growth:** 10% week-over-week user growth; primarily word-of-mouth among VCs and founders.
- **Pricing (2025–2026):** Free (25 lifetime meetings) → Individual $18/mo → **Business $14/user/mo** (unlimited notes, Attio/Notion/Slack/HubSpot integrations, **MCP integration**) → Enterprise $35+/user/mo (SSO, AI training opt-out, API access).
- **MCP integration (Business tier, 2025):** Granola added MCP support in their Business tier, positioning meeting notes as an agent context source. This is a meaningful competitive move — they now deliver context to AI agents, not just to human note-readers.
- **Key limitations:** Meeting-only scope. No screen context outside meetings. No always-on daemon. Cloud-dependent. No privacy-first positioning. MCP is a feature, not the architecture.
- **Competitive relationship:** Not a direct competitor for ContextPulse's developer segment. Granola's $250M valuation at undisclosed ARR demonstrates the "ambient AI context" category commands premium multiples — strong comp for ContextPulse fundraising. Their MCP move validates that context-to-agent delivery is the right product direction.

### Otter.ai
**Sources:** [Otter.ai $100M ARR announcement](https://otter.ai/blog/otter-ai-caps-transformational-2025-with-100m-arr-milestone-industry-first-ai-meeting-agents-and-global-enterprise-expansion) | [Sacra Otter profile](https://sacra.com/c/otter/)

- **What it does:** AI meeting transcription, real-time notes, post-meeting action items. AI Meeting Agents (Sales Agent, SDR Agent, Meeting Agent) launched March 2025. SDR Agent conducts live product demos autonomously.
- **Revenue:** **$100M ARR** (announced Q4 2025); $81M ARR end-2024; ~56% YoY growth.
- **Users:** 35M+ registered worldwide.
- **Team efficiency:** ~200 employees; >$500K revenue per employee.
- **Funding:** ~$70M raised ($50M Series B 2021); investors: Draper Associates, Horizons Ventures, GGV Capital, NTT DOCOMO Ventures.
- **Pricing (2026):** Free (limited) → **Pro $16.99/mo ($8.33 billed annually)** → **Business $30/user/mo ($20 billed annually)** → Enterprise custom.
- **Compliance:** SOC 2 Type II certified + **HIPAA compliant (July 2025)** — now targeting healthcare and regulated industries.
- **MCP Server:** **Live as of 2025** — connects Otter transcription data to external AI agents including Claude. This is the first meeting-context tool to support MCP delivery, though it remains meeting-only.
- **Key limitations:** Meeting transcription only. No always-on screen context, no keyboard/pointer capture, no developer focus. Cloud-dependent.
- **Competitive relationship:** Demonstrates audio-based context tools reach $100M ARR. Otter's MCP server is a proof point that meeting-context tools understand the MCP opportunity — but they cannot capture what users do *between* meetings. ContextPulse fills that gap.

---

## Feature Comparison Matrix

Legend: ✓ Yes | ✗ No | ~ Partial/Limited | † Conditional | N/A Not Applicable

| Feature | ContextPulse | Screenpipe | MS Recall | Limitless (†shut down) | Granola | Otter.ai | On-Demand MCP |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **CAPTURE MODALITIES** | | | | | | | |
| Screen capture (always-on) | ✓ | ✓ | ✓† | ✗ (shut down Dec 2025) | ✗ | ✗ | ✗ |
| Audio/voice capture | ~ (roadmap Q3 2026) | ✓ | ✓ | ✗ (shut down) | ✓ | ✓ | ✗ |
| Keyboard input capture | ~ (roadmap Q1 2027) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Mouse/pointer capture | ~ (roadmap Q2 2027) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Clipboard monitoring | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Cross-modal temporal alignment | ✓ (screen + clipboard today) | ~ (screen + audio only) | ~ | ✗ | ✗ | ✗ | ✗ |
| Wearable/pendant context | ✗ (not planned) | ✗ | ✗ | ✗ (shut down) | ✗ | ✗ | ✗ |
| **PERFORMANCE** | | | | | | | |
| Always-on CPU usage | **<1%** | 5–15% | 2–5% (NPU req.) | N/A | ~2% (meeting only) | N/A | ~0% (idle) |
| RAM footprint | **<20MB** | 200–500MB | ~100MB | N/A | ~50MB | N/A | ~0% |
| Startup to first capture | **<30 sec** | 5–10 min | Built-in (OS-level) | N/A | 5 min | Instant | 2–5 min |
| Per-monitor independent capture | ✓ | ✗ (composite) | ✗ (composite) | N/A | N/A | N/A | ✗ |
| Change detection (skips static) | ✓ (1.5% diff = 40–60% frame reduction) | ~ (some dedup) | Unknown | N/A | N/A | N/A | ✗ |
| Smart storage (text vs image adaptive) | ✓ (**59% storage savings**) | ✗ (all video) | ✗ | N/A | N/A | N/A | ✗ |
| 30-min rolling buffer | ✓ | ✓ (video, much larger) | ✓ | N/A | ✗ | ✗ | ✗ |
| Estimated disk usage per hour | **~120MB/hr** | 2–5GB/hr (video) | Unknown | N/A | Meeting only | Meeting only | 0 |
| **PRIVACY & SECURITY** | | | | | | | |
| All processing on-device | ✓ | ✓ | ~† (NPU req. + BitLocker) | ✗ (cloud) | ✗ (cloud) | ✗ (cloud) | ✓ |
| Pre-storage data redaction | ✓ **(10+ categories: API keys, JWTs, CC#, SSN, PEM, connection strings, bearer tokens)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Window title blocklist (enforced) | ✓ (default patterns, configurable) | ✓ | ~ | N/A | ✗ | N/A | ✗ |
| Auto-pause on session lock | ✓ (Win32 WTS_SESSION_LOCK) | ✓ | ✓ | N/A | N/A | N/A | ✗ |
| No cloud account required | ✓ | ✓ | ✓ (but needs Win Hello) | ✗ | ✗ | ✗ | ✓ |
| Zero telemetry | ✓ | ~ (some telemetry reported) | ✗ (Windows sends usage data) | ✗ | ✗ | ✗ | ✓ |
| GDPR/EU data residency | ✓ (all data local) | ✓ | ~ (EEA rollout delayed) | ✗ | ✗ | ✗ | ✓ |
| Works in regulated industries (finance, healthcare, legal) | ✓ | ✓ | ~ (Copilot+ only) | ✗ | ✗ | ✗ | ✓ |
| **AI AGENT INTERFACE** | | | | | | | |
| MCP protocol (primary interface) | ✓ **(10 tools, MCP-native)** | ~ (3 tools, add-on module) | ✗ | ✗ | ✗ | ✗ | ✓ (1–3 tools, stateless) |
| Temporal context query (N minutes ago) | ✓ `get_context_at` | ~ (timeline browsing, UI only) | ✓ (natural language, UI only) | N/A | ✗ | ✗ | ✗ |
| Full-text search (programmatic API) | ✓ (FTS5 SQLite via MCP) | ✓ (local UI only) | ✓ (local UI only) | N/A | ✗ | ✓ (Otter UI only) | ✗ |
| Per-agent usage tracking | ✓ `get_agent_stats` | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| Token cost estimation per frame | ✓ (Claude tile formula built-in) | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| Multi-agent shared context (all AI tools simultaneously) | ✓ (one daemon, all MCP clients) | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| Clipboard context delivery to agents | ✓ (`get_clipboard_history`, `search_clipboard`) | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| App usage analytics for agents | ✓ `get_activity_summary` | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| Auto-configure Claude Code MCP | ✓ (setup.py) | ~ (manual) | ✗ | N/A | ✗ | ✗ | ~ |
| Auto-configure Cursor MCP | ✓ (setup.py) | ~ (manual) | ✗ | N/A | ✗ | ✗ | ~ |
| Auto-configure Gemini CLI MCP | ✓ (setup.py) | ✗ | ✗ | N/A | ✗ | ✗ | ✗ |
| **PLATFORM & PRICING** | | | | | | | |
| Windows | ✓ | ✓ | ✓ (Copilot+ only) | ✗ (shut down) | ~ (beta) | ✓ | Varies |
| macOS | ~ (Q3 2026 roadmap) | ✓ | ✗ | ✗ (shut down) | ✓ | ✓ | Varies |
| Linux | ~ (2027 roadmap) | ✓ | ✗ | ✗ | ✗ | ✗ | Varies |
| Required hardware | **Any PC** | Any PC | Copilot+ PC (40+ TOPS NPU, 16GB RAM, $800–1,500+) | N/A | Any | Any | Any |
| Free tier with genuine functionality | ✓ (3 MCP tools, no time limit) | ~ (OSS core free; $400 for desktop app) | ✓† (only if Copilot+ hardware) | N/A | ✗ (25 lifetime meetings max) | ✓ (limited minutes/month) | ✓ |
| Entry paid price | **$29 one-time** | $400 one-time (lifetime) | Free (software only) + $800–1,500 hardware | N/A | $18/month | $8–10/month | Free |
| Team/subscription pricing | $20/seat/month (Q1 2027) | None (no subscription product) | Free (bundled, no SKU) | N/A | $14/user/month | $20/month | N/A |
| Enterprise pricing | $50K+/year (2028 roadmap) | None | Bundled (no separate enterprise product) | N/A | $35+/user/month | Custom | N/A |
| **ACCESSIBILITY** | | | | | | | |
| Designed for AT use (trademark Class 10 filed) | ✓ (USPTO Class 10 application ready) | ✗ | ~ (separate Seeing AI product; no Class 10 intent) | ✗ | ✗ | ~ (live captioning only) | ✗ |
| Motor impairment detection (pointer tremor, typing fatigue) | ~ (Flow + Keys — roadmap Q1–Q2 2027) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Cognitive accessibility (recall, summarization, context) | ~ (Memory module — roadmap Q4 2026) | ✗ | ✓ (AI timeline, limited) | ✗ | ✗ | ✗ | ✗ |
| Section 508 / ADA compliance positioning | ✓ (proactive strategy; VPAT planned 2027) | ✗ | ~ (enterprise, hardware-gated) | ✗ | ✗ | ~ (captioning only) | ✗ |
| Federal government procurement angle | ✓ (GSA Schedule roadmap 2028) | ✗ | ~ (limited; hardware gate is barrier for federal) | ✗ | ✗ | ✗ | ✗ |
| **LEARNING & PERSONALIZATION** | | | | | | | |
| Cross-modal learning engine | ~ **(Q3 2027 — first in class; no competitor building this)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Vocabulary personalization over time | ~ (via learning engine) | ✗ | ✗ | ✗ | ✗ | ~ (accent adaptation) | ✗ |
| Switching cost accumulation | ✓ (historical context + personal vocabulary models grow over time) | ~ (data local but no personalization model) | ✓ (history local; no personalization) | N/A | ~ (notes history) | ~ (transcripts) | ✗ |

---

## Aggregate Scoring (1–5 per dimension)

| Dimension | ContextPulse | Screenpipe | MS Recall | Granola | Otter.ai | On-Demand MCP |
|-----------|:-----------:|:----------:|:---------:|:-------:|:--------:|:-------------:|
| Resource efficiency | **5** | 2 | 3† | 4 (meeting only) | N/A | 5* |
| Privacy & security | **5** | 4 | 2 | 1 | 1 | 4 |
| AI agent interface (MCP) | **5** | 2 | 1 | 1 | 1 | 3 |
| Time to value / setup | **5** | 2 | 3† | 3 | 4 | 3 |
| Price accessibility | **5** | 2 | 3† | 2 | 3 | 5 |
| Current feature breadth | **4** | 4 | 3 | 2 | 2 | 1 |
| Roadmap feature breadth | **5** | 3 | 3 | 2 | 2 | 1 |
| Platform support | 3 | **5** | 1 | 3 | 4 | 4 |
| Accessibility strategy | **5** | 1 | 3 | 1 | 2 | 1 |
| Learning & personalization | 3 | 1 | 2 | 1 | 2 | 1 |
| **TOTAL** | **45/50** | **26/50** | **24/50** | **20/50** | **20/50** | **28/50** |

\* On-demand tools: ~0% CPU when idle, but also 0% useful when idle — pure request-response, no history.
† Microsoft Recall: scores well ONLY with Copilot+ hardware (40+ TOPS NPU, 16GB RAM, $800–$1,500 PC). Without qualifying hardware, score drops to 1 on all hardware-dependent dimensions. Approximately 85–90% of current developer machines do not qualify.

**Limitless excluded from 2026 scoring** — desktop product shut down December 19, 2025; no longer an active competitor in this space.

---

## Pricing Comparison (March 2026)

| Product | Free | Entry Paid | Team/Monthly | Enterprise | Hardware Cost | Total Cost of Ownership (Y1) |
|---------|------|-----------|--------------|------------|---------------|-------------------------------|
| **ContextPulse** | 3 MCP tools (unlimited) | **$29 one-time** | $20/seat/mo (2027) | $50K+/yr (2028) | None | **$29** |
| Screenpipe | OSS core (MIT) | $400 lifetime | None | None | None | **$400** |
| MS Recall | N/A | Free (software) | N/A | N/A | **$800–1,500 new PC** | **$800–1,500** |
| Granola | 25 meetings lifetime | $18/month | $14/user/mo | $35+/user/mo | None | **$216/yr** |
| Otter.ai | Limited minutes | $16.99/mo ($8.33 annual) | $30/user/mo ($20 annual) | Custom | None | **$100–204/yr** |
| On-demand MCP | Free (OSS) | Free | N/A | N/A | None | **$0** |

**Key insight:** ContextPulse's $29 one-time entry is the lowest barrier in the paid space while offering the most developer-centric feature set. Screenpipe's $400 one-time creates a 14x higher conversion barrier. MS Recall's "free" is misleading — it requires $800–$1,500 in new hardware.

---

## ContextPulse Unique Features (No Active Competitor Has These — March 2026)

| Feature | Why Unique | Competitive Defensibility | IP Status |
|---------|-----------|--------------------------|-----------|
| Always-on daemon at **<1% CPU** | Structural advantage: competitors are either 5–15% CPU (Screenpipe) or not running (on-demand) | **Very High** — Screenpipe cannot match without ground-up rewrite; video recording is their architecture | Trade Secrets #3, #4 |
| **Pre-storage OCR redaction** (10+ pattern categories: API keys, JWTs, CC#, SSN, PEM, connection strings, bearer tokens) | Competitors store raw screen text; ContextPulse masks sensitive data before it ever hits disk | **Very High** — compliance differentiator for regulated industries; no competitor even mentions this capability | Trade Secret #5 |
| **Per-monitor independent capture** | Each monitor processed at native resolution; solves multi-monitor illegibility | **High** — Screenpipe composites all monitors; Recall does not expose per-monitor API | Trade Secret #1 |
| **Content-adaptive storage** (59% savings) | Dual-threshold OCR classifier: text-only storage when confident (100 chars, 70% confidence); image retained only when visual-dominant | **High** — Screenpipe stores everything as video (2–5GB/hr vs ~120MB/hr for ContextPulse) | Trade Secret #2 |
| **Per-agent MCP usage tracking** (`get_agent_stats`) | Tracks which AI clients consume which context, call volumes, and tool patterns | **Medium** — novel in multi-agent awareness; first-mover advantage | Trade Secret #6 |
| **Token cost estimation per frame** | Model-specific formulas (Claude: `ceil(w/768) × ceil(h/768) × 258`); enables cost-aware context delivery decisions | **Medium-High** — no competitor tracks token economics at the frame level | Trade Secret #9; provisional patent candidate |
| **Clipboard history + search via MCP** (`get_clipboard_history`, `search_clipboard`) | Error messages, stack traces, URLs, code snippets captured and searchable without screenshot | **Medium** — simple but nobody ships it; high utility in developer workflow | Trade Secret #6 |
| **Multi-agent shared context** | One daemon, multiple simultaneous MCP clients; Claude Code + Cursor + Gemini all get the same context | **High** — on-demand tools are stateless; no competitor has multi-agent coordination | Trade Secret #6 |
| **Cross-modal learning engine** (Q3 2027 roadmap) | First system where screen, voice, keyboard, and pointer modalities improve each other — voice corrections → keyboard vocabulary; OCR validates voice transcriptions | **Very High** — 2–3 years to replicate even with same architecture; requires all four modalities first | Trade Secret #12; Provisional Patent Claims 17–22 |
| **Accessibility via general-purpose MCP** (AT architecture) | Repurposes developer MCP protocol for assistive technology delivery — no AT tool uses this approach | **Very High** — AT market doesn't think this way; creates dual developer + government channel | Trade Secret #8; USPTO Class 10 trademark |
| **Typing fatigue detection** (Q1 2027 roadmap) | Physiological signal from keyboard timing speed decay regression — novel; no competitor attempts this | **High** — medical-adjacent; first-in-class; strong provisional patent position | Trade Secret #10; Provisional Patent Claims 23–26 |
| **Pointer tremor detection** (Q2 2027 roadmap) | AI-native motor impairment assessment from desktop pointer data — maps to ALS, Parkinson's early indicators | **Very High** — medical-adjacent research novelty; no competitor exists in this space | Trade Secret #11; Provisional Patent Claims 27–30 |

---

## Why Competitors Cannot Replicate ContextPulse's Core Advantages

### Screenpipe cannot achieve <1% CPU
Screenpipe's architecture continuously records full video frames — foundational to their product. Their AI search works because they have complete video history. Dropping to <1% CPU would require eliminating video recording entirely: (1) losing their core differentiator, (2) rebuilding the entire capture and storage stack from scratch, (3) delivering a product that breaks their existing $400-paying customers' expectations. The CPU overhead is not a bug; it is an unavoidable consequence of their chosen architecture. ContextPulse solved this by building change-detection and content-adaptive storage from day one.

### Microsoft Recall cannot become cross-platform or MCP-accessible
Recall is OS-level infrastructure bundled with Microsoft's Copilot+ PC hardware refresh strategy. Shipping Recall to all Windows PCs would undermine the commercial incentive for OEM partners to produce Copilot+ hardware. Adding an MCP API would re-ignite the privacy controversy that delayed Recall a full year. Additionally, Recall will never run on macOS or Linux — ContextPulse's cross-platform roadmap (macOS Q3 2026, Linux 2027) creates a 100M+ device addressable market Recall will never reach.

### Limitless has exited the market
Meta acquired Limitless and shut down the desktop screen/audio capture product December 19, 2025. The team is now building ambient AI features inside Meta AI. Any competitive concern from Limitless's direction is resolved. More importantly: Meta acquired context-capture infrastructure at $2M ARR, validating ContextPulse's strategic value.

### Granola and Otter cannot expand to general desktop context
Both products are architected around audio transcription in meeting contexts. Expanding to always-on screen capture + keyboard + pointer would mean building an entirely new product that competes with their own core value proposition. Neither has MCP integration nor developer infrastructure focus. Their funding (Granola $67M, Otter $70M) went into meeting-focused AI — not the architecture needed for general desktop context capture.

### On-demand MCP tools cannot become always-on
These tools are stateless — single screenshot on demand, no daemon, no history, no buffer. Adding always-on capture means building: a background daemon, change detection, rolling buffer, OCR pipeline, SQLite database, redaction layer, thread watchdog, event detection, and per-monitor handling. Developers who attempt this face the same 90-day development curve ContextPulse already completed — plus the harder architectural challenges that took additional months. First-mover advantage compounds: ContextPulse's history will be 6–12 months deep before any equivalent tool ships.

---

## Competitive Dynamics: Why the Window Is Now (Q1–Q2 2026)

1. **Market leader vacancy:** Limitless's acquisition and shutdown (Dec 2025) creates an open field in the desktop-context category with no established winner.
2. **MCP ecosystem inflection:** 97M monthly SDK downloads (December 2025); 5,500+ official servers on PulseMCP; Linux Foundation standardization in progress. Developer workflows are converging on MCP — ContextPulse is MCP-native from day one. ([MCP 1-year anniversary blog](http://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/))
3. **Recall hardware gate will persist:** Despite April 2025 general availability, Recall remains inaccessible to ~85–90% of developers due to Copilot+ requirements. Enterprise hardware refresh cycles are 3–5 years. This window stays open through at least 2027–2028.
4. **No MCP-native context product with subscription revenue exists:** Screenpipe has no subscription model. Granola and Otter are meeting-only. ContextPulse is positioned to be the first MCP-native context platform with recurring enterprise contracts.
5. **Accessibility market untouched by AI-native tools:** The $26.8B global assistive technology market ([IMARC Group, 2024](https://www.imarcgroup.com/assistive-technology-market)) has no AI-native, MCP-based competitor. ContextPulse's USPTO Class 10 trademark filing is the only AI context tool filing in this class.
6. **AI developer tools growing at 25–27% CAGR:** $4.86B (2023) → $26B (2030) ([Grand View Research](https://www.grandviewresearch.com/industry-analysis/ai-code-tools-market-report)). The market is early and growing fast.

---

## 2x2 Competitive Position

```
                              MULTI-MODAL (screen + voice + keyboard + pointer)
                                           ▲
                                           │
                                           │      ★ ContextPulse
                                           │        (screen + clipboard today;
                                           │         voice/keys/pointer roadmap)
                                           │
ON-DEVICE ─────────────────────────────────┼───────────────────── CLOUD-DEPENDENT
(privacy-first; no account required)       │                       (data leaves machine)
                                    Screenpipe        Granola (meeting audio only)
                                    MS Recall         Otter.ai (meeting audio only)
                                    (screen only;
                                     no MCP API)
                                           │
                                           ▼
                                     SINGLE-MODAL (screen only, or meeting audio only)
```

**ContextPulse occupies the upper-left quadrant alone.** No active competitor is building toward multi-modal + on-device + MCP-native. The closest player (Limitless) was acquired and shut down. The field is open.

---

*Last updated: March 2026 | Sources verified against public records | Competitive landscape reflects publicly available information as of the update date.*