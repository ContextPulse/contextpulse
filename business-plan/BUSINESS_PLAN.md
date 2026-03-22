# ContextPulse — Comprehensive Business Plan

**Prepared:** 2026-03-21
**Owner:** David Jerard, Founder — Jerard Ventures LLC
**Classification:** Confidential — NDA required for distribution

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Company Overview](#2-company-overview)
3. [Problem & Solution](#3-problem--solution)
4. [Product Architecture & Roadmap](#4-product-architecture--roadmap)
5. [Intellectual Property Strategy](#5-intellectual-property-strategy)
6. [Market Analysis](#6-market-analysis)
7. [Competitive Analysis](#7-competitive-analysis)
8. [Business Model & Monetization](#8-business-model--monetization)
9. [Go-To-Market Strategy](#9-go-to-market-strategy)
10. [Marketing Strategy](#10-marketing-strategy)
11. [Sales Strategy](#11-sales-strategy)
12. [Financial Projections](#12-financial-projections)
13. [Accessibility as Strategic Advantage](#13-accessibility-as-strategic-advantage)
14. [Exit Strategy](#14-exit-strategy)
15. [Risk Analysis](#15-risk-analysis)
16. [Action Plan: Next 90 Days](#16-action-plan-next-90-days)

---

## 1. Executive Summary

### Vision
ContextPulse is the context layer for artificial intelligence — invisible infrastructure that gives AI agents continuous awareness of what users see, say, type, and do, enabling a new class of AI-native productivity tools.

### Mission
Eliminate the context tax on human-AI collaboration: the daily friction of re-explaining, re-screenshotting, and re-establishing context that costs knowledge workers 2+ hours per day.

### The Problem
AI coding assistants and productivity agents are powerful but contextually blind. Every conversation starts from zero. Developers manually take 10–20 screenshots per day, re-type context explanations, and switch between AI tools that share nothing. This friction compounds: the more AI tools a user runs simultaneously, the worse the problem.

### The Solution
ContextPulse is a quad-modal context capture platform — screen (Sight), voice (Voice), keyboard (Keys), and pointer (Flow) — delivered to any AI agent through a standardized MCP protocol interface. An on-device cross-modal learning engine continuously improves accuracy and personalizes context delivery over time. All processing is local; no data leaves the machine.

### Current State (2026-03-21)
- **ContextPulse Sight:** Production-ready. 145 passing tests, 10 MCP tools, 4 domains secured (contextpulse.ai primary), Ed25519 licensing + Lambda/Gumroad/SES infrastructure built.
- **Voiceasy (future ContextPulse Voice):** v1.0 shipping on Gumroad, local Whisper, 28 passing tests.
- **IP:** Provisional patent drafted (ready to file, $65 micro entity), trademark application ready for Classes 9, 10, and 42 ($1,050), 13 trade secret categories documented.

### Market Opportunity
- **Primary TAM:** AI productivity tools — $5.2B (2024) → $200B by 2034 (CAGR ~44%)
- **Secondary TAM:** Assistive technology — $26.8B global, growing 7.5%/year; $3.5B+ U.S. federal Section 508 procurement annually
- **Beachhead SAM:** ~2.5M developers using MCP-compatible AI coding assistants
- **3-year SOM:** 25,000 paying users → $3.5M ARR

### Funding Roadmap
- **Now–Q3 2026:** Bootstrap (already self-funded); launch Sight Pro + Voice
- **Q4 2026–Q1 2027:** $500K–$1.5M seed round — +1 engineer, macOS port, enterprise pilot
- **Q2–Q4 2027:** SBIR Phase I ($275K non-dilutive) for accessibility platform
- **2028–2029:** Series A ($3–8M) or strategic acquisition at $15M–$60M

---

## 2. Company Overview

### Entity
**Jerard Ventures LLC** — Limited Liability Company, Colorado, Boulder
Founder: David Jerard | david@jerardventures.com | contextpulse.ai

### Founder Profile
David Jerard is a technical founder managing a portfolio of AI-adjacent ventures. Relevant credentials:
- Built ContextPulse Sight from zero to 145 passing tests + production deployment in 90 days
- Shipped Voiceasy v1.0 (local Whisper voice capture) to Gumroad independently
- Constructed OpenClaw: a 50+ skill agent infrastructure running across Claude Code and Gemini CLI — the living proof-of-concept for multi-agent context sharing
- Filed provisional patent specifications and trademark applications independently
- Deployed AWS Lambda/DynamoDB/SES license key infrastructure
- **Dogfood proof:** ContextPulse Sight runs in David's daily AI development workflow, making him the product's most demanding user

### Current Asset Inventory

| Asset | Status |
|-------|--------|
| ContextPulse Sight (screen capture MCP, 10 tools) | Production-ready, 145 tests |
| ContextPulse Core (licensing, settings, GUI theme) | Production-ready, 35 tests |
| Voiceasy v1.0 (future ContextPulse Voice) | Live on Gumroad |
| Provisional patent specification | Drafted — file at USPTO Patent Center ($65) |
| Trademark application Classes 9, 10, 42 | Drafted — file at USPTO TEAS ($1,050) |
| 13 trade secret categories | Documented under NDA framework |
| 4 domains (contextpulse.ai/.dev/.io, context-pulse.com) | Registered on Cloudflare |
| Lambda webhook (Gumroad → Ed25519 key → SES) | Built, pending deployment |
| Ed25519 licensing + nag dialog | Built, wired into tray menu |
| Landing page | Live at contextpulse.pages.dev |
| Logo system (full mark + simplified) | Designed, in brand/ |

### Team Plan

| Phase | Headcount | Roles |
|-------|-----------|-------|
| Bootstrap (2026) | 1 (founder) | All of the above |
| Seed (Q4 2026) | 2 | +1 Python/Windows engineer |
| Year 2 | 4 | +1 macOS engineer, +1 sales/growth |
| Year 3 | 8 | +2 engineers, +1 enterprise sales, +1 support |

---

## 3. Problem & Solution

### The Context Tax

Knowledge workers using AI tools pay a daily context tax: time spent re-establishing context that AI agents lost between sessions, never had, or can't observe.

**Quantified pain:**
- Professionals lose ~2 hours/day to context switching and re-explanation (Microsoft WorkLab, 2024)
- Developers manually provide AI tools with 10–20 screenshots per day
- Each screenshot + crop + paste + explanation costs 30–90 seconds of flow state
- 10 developers × 20 screenshots × 60 seconds = **3.3 person-hours lost daily** just on visual context
- "Every conversation starts from zero" is the #1 AI productivity complaint across Reddit, OpenAI forums, and developer blogs (confirmed in demand research)

**The five specific failures:**

1. **Session blindness:** AI doesn't know what's on screen, what was worked on yesterday, or what decisions were made
2. **Single-modal limitation:** AI accepts images but doesn't continuously observe the visual environment
3. **No cross-tool memory:** Switching from Claude Code to Cursor means re-explaining everything; no shared context layer exists
4. **Manual capture overhead:** User must interrupt workflow to take screenshots, breaking flow state
5. **No temporal context:** "What was on screen when that error appeared?" is unanswerable

### Why Existing Solutions Fail

| Solution | What It Does | Fatal Flaw |
|----------|-------------|-----------|
| Manual screenshots | User captures and pastes | Flow state interruption; no history; no search |
| Screenpipe | Continuous video+audio recording | 5–15% CPU, 200–500MB RAM, $400 entry price |
| Microsoft Recall | OS-level screen indexing | Windows Copilot+ hardware only; no agent API; privacy backlash; disabled by default |
| Limitless (Rewind) | Wearable + cloud memory | Enterprise pricing; cloud-dependent; requires hardware; no MCP |
| On-demand MCP screenshot tools (5+ exist) | Single-shot screen capture | No daemon; no history; no OCR; no temporal context |
| ChatGPT/Claude native memory | Per-chat personalization | Single-agent, no visual context, no cross-tool sharing |

### ContextPulse's Answer

**Quad-modal capture at <1% CPU.** Screen, voice, keyboard, and pointer — captured always-on, stored locally, delivered via MCP.

**Cross-modal learning.** The system observes when AI suggestions are accepted vs. corrected, learns from keyboard corrections to voice transcriptions, and builds a personalized context model that improves over time.

**On-device privacy.** All processing local. No cloud sync, no telemetry, no accounts required for free tier. MCP transport is stdio (local process communication).

**MCP-native architecture.** Not an app — infrastructure. Any MCP-compatible AI tool (Claude, Cursor, Gemini, VS Code) gets always-on context without integration work.

---

## 4. Product Architecture & Roadmap

### Current State: ContextPulse Sight (Phase 3.0 Complete, 2026-03-21)

**10 MCP tools shipped:**

| Tool | Capability |
|------|------------|
| `get_screenshot` | Capture active/all monitors/region on demand |
| `get_recent` | Rolling buffer frames with diff filtering |
| `get_screen_text` | Full-resolution OCR |
| `get_buffer_status` | Daemon health + per-frame token cost estimates |
| `get_activity_summary` | App usage distribution over N hours |
| `search_history` | FTS5 full-text search across window titles + OCR text |
| `get_context_at` | Time-travel: retrieve frame + metadata from N minutes ago |
| `get_clipboard_history` | Recent clipboard entries with deduplication |
| `search_clipboard` | Full-text search of clipboard history |
| `get_agent_stats` | Per-MCP-client tool call tracking |

**Architecture:**
```
[Screen] → mss (3ms/frame, DPI-aware, multi-monitor)
         → Per-monitor change detection (1.5% diff threshold → 40–60% frame reduction)
         → Event-driven capture (window focus, idle, monitor cross)
         → Smart storage (dual-threshold classifier: 100 char / 70% confidence)
         → Pre-storage OCR redaction (10+ sensitive pattern categories)
         → SQLite activity DB + FTS5 search
         → 30-min rolling buffer (10 frames/monitor)
         → MCP stdio server ─→ Claude / Cursor / Gemini / VS Code
```

**Performance:** <1% CPU at all times, <20MB RAM, <2MB/min disk, <2s startup.

### Platform Roadmap

```
2026 Q1-Q2       2026 Q3-Q4       2027 Q1-Q2       2027 Q3-Q4       2028+
──────────────   ──────────────   ──────────────   ──────────────   ──────────
Spine Contract   Voice MVP        Keys MVP         Flow MVP         Contacts
Sight 1.0        Memory MVP       Keys Pro         Heart            Signals
Sight Pro        macOS beta       Linux beta       Learning Engine  Enterprise
PyPI launch      Agent alpha                       Cloud sync       Platform
```

#### Phase 1: Sight Launch (Q2 2026)
- Deploy Lambda (DynamoDB + SES)
- Create Gumroad listings
- Publish to PyPI
- Product Hunt + Show HN launch

#### Phase 2: Voice Integration (Q3 2026)
- Port Voiceasy as ContextPulse Voice
- Shared activity database (voice + screen temporal alignment)
- Screen-aware vocabulary biasing (OCR text → Whisper vocabulary bias)
- Cross-modal: voice corrections detected from keyboard input

#### Phase 3: Memory Package (Q3–Q4 2026)
- Cross-session persistent memory for agents
- Multi-agent shared context (Claude + Cursor + Gemini simultaneously)
- MCP tools: memory_store, memory_recall, memory_search, memory_forget
- Confidence scoring + temporal decay

#### Phase 4: Keys + Flow (Q1–Q2 2027)
- Keyboard input capture with sensitive field detection via accessibility APIs
- Mouse/pointer interaction capture (hover dwell, scroll patterns, click targets)
- Typing fatigue detection via speed decay regression (novel: no competitor has this)
- Pointer tremor detection (novel: medical device adjacent)
- Quad-modal temporal alignment in unified SQLite DB

#### Phase 5: Cross-Modal Learning Engine (Q3 2027)
- Voice-to-keyboard correction pair detection
- Keyboard-to-voice vocabulary transfer protocol
- Screen-as-ground-truth OCR validation
- Pointer-attention-weighted context scoring
- Cognitive load estimation (multi-signal)
- **This is the deepest moat.** Compounds over time; cannot be replicated without the same four-year head start.

#### Phase 6: Heart — Values & Mission Layer (Q4 2027)
- Structured user profile: mission statement, goals, values, passions, priorities
- Weighting function for all other modules — Heart tells the system what matters
- Context filtering: surface what aligns with user's stated priorities, suppress noise
- Goal tracking: connect daily activity (from Sight, Voice, Keys) to declared objectives
- Life-domain balancing: work, family, health, creative — with user-defined boundaries
- **MCP tools:** `get_priorities`, `check_goal_alignment`, `get_life_balance`
- **Why foundational:** Without Heart, the spine stores data. With Heart, the spine makes *judgments*. This is the compass for the entire ecosystem.
- **Effort:** Low (~400 LOC). Structured profile + goal tracker + weighting API. High impact relative to build cost.

#### Phase 7: Contacts — Personal Context CRM (Q1 2028)
- People database: who they are, relationship context, communication preferences
- Interaction history: auto-populated from email, calendar, mentions across modalities
- Entity extraction: when Sight sees a name on screen or Voice hears one, link to contact record
- Relationship graph: who connects to whom, frequency, recency, sentiment
- Follow-up tracking: pending conversations, last interaction, communication cadence
- **MCP tools:** `search_contacts`, `get_person_context`, `get_interaction_history`, `get_follow_ups`
- **Why valuable:** Every AI assistant loses people context between sessions. Contacts makes "email Sarah about the proposal" work without disambiguation — the system knows which Sarah, your last conversation, and the tone you use with her.
- **Effort:** Medium (~1,200 LOC). PersonEntity extractor + SQLite contact store + MCP tools.

#### Phase 8: Signals — External Intelligence Antenna (Q2 2028+)
- External event monitoring: news, market data, dependency updates, competitor moves
- Integration adapters: email (Gmail/Outlook), Slack, RSS, GitHub notifications, AWS alerts
- Signal-to-noise filtering: Heart-weighted relevance scoring (only surface what matters to this user)
- Proactive alerts: "A competitor just launched a similar feature" or "Your AWS bill spiked 40%"
- Project-aware filtering: signals are tagged to relevant projects automatically
- **MCP tools:** `get_signals`, `search_external_context`, `get_alerts`, `configure_signal_sources`
- **Why ambitious but viable:** The spine architecture already supports it — external events are just another ModalityModule emitting ContextEvents. The hard part is signal filtering, which Heart solves.
- **Effort:** High (~2,500 LOC). Integration adapters + filtering engine + alert system.

### Platform Architecture (Full Vision)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         ContextPulse Platform                            │
│                                                                          │
│  CAPTURE LAYER (Modalities)                                              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐              │
│  │ Sight  │ │ Voice  │ │ Keys   │ │ Flow   │ │ Signals  │              │
│  │(screen)│ │(audio) │ │(keybd) │ │(mouse) │ │(external)│              │
│  └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └────┬─────┘              │
│      └──────────┴──────────┴──────────┴────────────┘                     │
│                             │                                            │
│  SPINE (Event Bus + Unified Storage)                                     │
│  ┌──────────────────────────▼───────────────────────────────────────┐    │
│  │         Unified Activity Database (SQLite + FTS5)                │    │
│  │   ContextEvent format · Temporal alignment · Redaction           │    │
│  └──────────────────────────┬───────────────────────────────────────┘    │
│                             │                                            │
│  INTELLIGENCE LAYER                                                      │
│  ┌──────────────────────────▼───────────────────────────────────────┐    │
│  │  ┌─────────────────┐  ┌──────────┐  ┌────────────────────────┐  │    │
│  │  │ Learning Engine │  │  Heart   │  │      Contacts          │  │    │
│  │  │ Corrections,    │  │ Values,  │  │  People, interactions, │  │    │
│  │  │ vocabulary,     │  │ goals,   │  │  relationship graph,   │  │    │
│  │  │ cognitive load  │  │ weights  │  │  follow-up tracking    │  │    │
│  │  └─────────────────┘  └──────────┘  └────────────────────────┘  │    │
│  └──────────────────────────┬───────────────────────────────────────┘    │
│                             │                                            │
│  DELIVERY LAYER                                                          │
│  ┌──────────────────────────▼───────────────────────────────────────┐    │
│  │              MCP Protocol Interface (stdio)                      │    │
│  └──┬──────────┬──────────┬──────────┬──────────────────────────────┘    │
│     │          │          │          │                                    │
│  Claude    Cursor    Gemini    VS Code / Any MCP Client                  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Platform Support Timeline
- **Now:** Windows 10/11
- **Q3 2026:** macOS beta (mss and pynput already cross-platform; ~2 months of packaging + tray work; see GTM Month 5)
- **Q4 2026:** macOS stable release
- **Q1–Q2 2027:** Linux beta (Wayland + X11; post-seed headcount)

---

## 5. Intellectual Property Strategy

### Provisional Patent

**Title:** System and Method for Adaptive Multi-Modal Context Delivery with Cross-Modal Continuous Learning Including Screen, Audio, Keyboard, and Pointer Input Capture to Artificial Intelligence Agents via Standardized Protocol Interface

**Status:** Specification drafted, ready for USPTO Patent Center filing
**Cost:** $65 (micro entity: solo inventor, <4 prior filings, income < $225K threshold)
**Priority deadline:** 12 months from filing to convert to non-provisional (no extensions)

**8 Core Claim Areas:**

| Claim | Description | Post-Alice Strength |
|-------|-------------|---------------------|
| 1 | Per-monitor independent capture with resolution-adaptive downscaling | Strong — specific technical steps |
| 2 | Content-adaptive storage classification (dual threshold) | Strong — specific measurable parameters |
| 3 | Hybrid timer + event replacement scheduling | Very Strong — novel architectural approach |
| 4 | Differential change detection (per-monitor pixel comparison) | Strong — specific algorithm with thresholds |
| 5 | Pre-storage redaction pipeline (10+ pattern categories) | Very Strong — data privacy method |
| 6 | MCP-native temporal context delivery with token cost estimation | Strong — specific protocol implementation |
| 7 | Cross-modal continuous learning (correction detection, vocabulary transfer) | Very Strong — novel cross-modal method |
| 8 | Accessibility platform via modular AI context + standardized protocol | Strong — specific market positioning method |

**Patent prosecution strategy:**
1. File provisional (Q2 2026) — $65
2. Convert to non-provisional utility (within 12 months) — $800 micro entity
3. File continuation covering accessibility claims separately (Year 2)
4. File PCT application if acquisition conversations are active (Year 3) — buys 30 months before national phase
5. License cross-modal learning + accessibility claims to AT vendors (Year 3+)

### Trademark Portfolio

| Mark | Status | Classes | Cost | Strategic Value |
|------|--------|---------|------|----------------|
| CONTEXTPULSE | Ready to file | 9, 10, 42 | $1,050 | Core brand; Class 10 establishes AT market position |
| CONTEXTPULSE SIGHT | Planned | 9 | $350 | Protects screen capture module |
| CONTEXTVOICE | Planned | 9 | $350 | Protects voice module |
| CONTEXTPULSE MEMORY | Planned | 9, 42 | $700 | Protects memory + SaaS |
| CONTEXTPULSE KEYS | Planned | 9, 10 | $700 | Class 10 = AT market |
| CONTEXTPULSE FLOW | Planned | 9, 10 | $700 | Pointer capture + AT |
| CONTEXTPULSE HEART | Planned | 9, 42 | $700 | Values/mission layer + SaaS |
| CONTEXTPULSE CONTACTS | Planned | 9, 42 | $700 | Personal CRM + SaaS |
| CONTEXTPULSE SIGNALS | Planned | 9, 42 | $700 | External intelligence + SaaS |
| CONTEXTPULSE CLOUD | Planned | 42 | $350 | Cloud service |

**Mark strength:** "ContextPulse" is a suggestive mark — no prior registrations found in TESS, no commercial products using this name, domains secured.

**Class 10 is the strategic moat:** No competitor in the AI context capture space has filed trademarks covering assistive devices. Filing now establishes rights before any well-funded competitor enters this vertical.

### Trade Secret Inventory (13 Categories)

Protected as trade secrets under the Defend Trade Secrets Act (18 U.S.C. § 1836). All accessible only under signed NDA:

| Category | What's Protected | Business Value |
|----------|-----------------|---------------|
| 4.1 | Per-monitor adaptive capture pipeline | Solves multi-monitor illegibility no competitor has solved |
| 4.2 | Content-adaptive storage classification | 100-char min + 70% confidence threshold — empirically optimized |
| 4.3 | Hybrid timer + event replacement architecture | Constant-rate resource use regardless of event density |
| 4.4 | Differential change detection algorithm | 1.5% normalized diff threshold — eliminates 40–60% of redundant frames |
| 4.5 | Pre-storage redaction pipeline | 10+ regex pattern categories; compliance differentiator |
| 4.6 | MCP context delivery interface | Token estimation formulas; context selection algorithms |
| 4.8 | Continuous audio context capture | VAD thresholds, buffer architecture, diarization pipeline |
| 4.9 | Accessibility platform architecture | Modular AT delivery via general-purpose context protocol |
| 4.10 | Token cost estimation method | Model-specific calibration data (Claude tile formula, etc.) |
| 4.11 | Keyboard input context capture | Fatigue detection regression, burst/pause heuristics |
| 4.12 | Mouse/pointer interaction capture | Tremor detection frequency parameters, efficiency formulas |
| 4.13 | Cross-modal learning engine | Correction pair detection, vocabulary transfer protocol |

### Copyright
Register ContextPulse source code at copyright.gov ($65) before launch — required for statutory damages eligibility in infringement cases.

### Freedom to Operate
- Microsoft: broad OS-level patents — mitigated by operating at application layer, not OS level
- Google: audio processing patents — mitigated by on-device Whisper, no cloud
- Screenpipe: MIT license (no enforcement risk)

---

## 5b. Open-Source Strategy (Under Evaluation)

### Strategic Rationale

The most successful AI tooling companies are open-core: open-source the engine, sell the experience. This section documents the open-source option for board/investor discussions and founder decision-making.

### The Case For Open Source

1. **Community builds modality modules** — Browser, Slack, Spotify, IDE plugins. Every community-built module makes the ecosystem stickier without engineering cost.
2. **ContextEvent becomes the standard** — if ContextPulse defines how context is captured and shared, the platform owns the protocol layer. Standards are worth more than products.
3. **Trust removes the #1 adoption barrier** — a tool that sees your screen and hears your voice needs verifiable privacy. Open source lets users audit the code.
4. **Talent pipeline** — contributors become candidates. Companies building on the platform become partners.
5. **Speed** — a community iterates 10x faster than a solo founder on modality coverage.

### Proven Revenue Models

| Model | How It Works | Revenue Example |
|-------|-------------|-----------------|
| **Open Core** | Free base, paid pro features (advanced intelligence, Heart, Contacts) | GitLab ($400M ARR), Supabase ($100M+ ARR) |
| **Hosted Service** | Self-host free, pay for cloud sync + cross-device | PostHog ($80M+ ARR), Cal.com |
| **Marketplace** | Take 20-30% cut of premium community modules | WordPress, Shopify |
| **Enterprise** | Free for individuals, paid for teams (SSO, audit, compliance) | Mattermost, Grafana |

**Recommended model:** Open Core + Hosted Service
- Individuals self-host free → builds trust and community
- Pro users pay $10-20/mo for cloud sync, advanced intelligence, Heart, Contacts
- Enterprise pays $50+/seat for team context sharing, compliance, SSO

### What Gets Open-Sourced vs. Stays Proprietary

| Open Source (Community) | Proprietary (Revenue) |
|------------------------|----------------------|
| ContextEvent spec + EventBus | Cross-modal learning engine (correlation, vocabulary) |
| Basic Sight capture module | Sight Pro features (advanced OCR, attention weighting) |
| Basic Voice module | Heart (values/mission weighting) |
| SQLite storage layer | Contacts (people CRM + entity extraction) |
| Plugin/ModalityModule interface | Signals (external intelligence) |
| Privacy + redaction pipeline | Cloud sync (E2E encrypted) |
| MCP server framework | Enterprise features (SSO, audit, compliance) |

### IP Implications

- **Patent strategy shifts** — patent the intelligence/correlation layer (proprietary), not the base capture. Provisional patent claims already cover cross-modal learning (Claims 7-8), which stays proprietary.
- **Trademark still critical** — open-source projects need trademark protection more, not less (Linux, Docker, Firefox all trademarked). File as planned.
- **License choice** — Apache 2.0 or AGPL. Apache maximizes adoption; AGPL prevents cloud competitors from hosting without contributing back. **Recommendation:** AGPL for the base (forces cloud competitors to open-source their modifications) + commercial license for proprietary layers.

### Recommended Timing

1. **Now** — File provisional patent + trademark. These protect you regardless of open-source decision.
2. **Q2 2026** — Ship Sight Pro closed-source. Get paying customers. Validate what people will pay for.
3. **Q3 2026** — After revenue validates demand, open-source the base layer (EventBus, basic Sight, storage).
4. **Q4 2026+** — Build community around the spine spec. Community contributes modules. Pro/enterprise features stay proprietary.

**Key principle:** Don't open-source before you know what's worth charging for. Ship, learn, then open strategically.

### Decision Status

**Status:** Under evaluation. Decision target: Q3 2026 (after Sight Pro revenue data).

---

## 6. Market Analysis

### Total Addressable Market (TAM)

**Market 1 — AI Productivity Tools**
- 2024 size: **$8.8–$15 billion** (global; range reflects different scope definitions across research firms)
- 2030 projection: **$37–68 billion**
- CAGR: ~26–28% (2024–2030)
- Includes AI coding assistants, AI writing tools, AI meeting tools, AI agent orchestration platforms — all require context infrastructure
- Context infrastructure (ContextPulse's category) is an enabling layer for this entire market
- Sources: [Grand View Research AI Productivity Tools Market Report](https://www.grandviewresearch.com/industry-analysis/ai-productivity-tools-market-report); [Market.us AI Productivity Tools](https://market.us/report/ai-productivity-tools-market/)

**Market 2 — Digital Accessibility / Assistive Technology**
- 2024 global size: **$26.8–$33.3 billion** (range across IMARC Group, Market Research Future, Custom Market Insights)
- U.S. market: ~$9 billion
- 2030 projection: $32.25 billion+ (Coherent Market Insights); $65.2 billion by 2034 (Custom Market Insights)
- CAGR: ~8.9% (Custom Market Insights) — driven by aging population, ADA/Section 508 enforcement, remote work
- U.S. federal Section 508 procurement: **$3.5+ billion annually** (mandatory spending for federal agencies)
- SBIR/STTR grants available: up to $2.075M non-dilutive per applicant
- Sources: [IMARC Group Assistive Technology Market](https://www.imarcgroup.com/assistive-technology-market); [Custom Market Insights](https://www.custommarketinsights.com/report/assistive-technology-market/); [Coherent Market Insights](https://www.biospace.com/press-releases/assistive-technology-market-size-to-worth-usd-32-25-billion-by-2030-coherent-market-insights)

**Market 3 — Enterprise Knowledge Management / Workplace Analytics**
- 2024 size: **$15.4 billion**
- CAGR: ~16%
- Enterprise team context sharing, cognitive load analytics, AI governance/audit trails — all ContextPulse capabilities

**Market 4 — AI Developer Tools (Code Tools)**
- 2024 size: **$4.86–$7.37 billion** (AI code tools specifically; broader developer tools market is $24B+)
- 2030 projection: **$24–37 billion** at 25–27% CAGR
- MCP ecosystem: 5,500+ official servers (PulseMCP registry, Oct 2025); 16,000+ total including community; 97 million monthly SDK downloads (Python + TypeScript, Dec 2025); Linux Foundation standard as of Q1 2026
- Sources: [Grand View Research AI Code Tools Market Report](https://www.grandviewresearch.com/industry-analysis/ai-code-tools-market-report); [Mordor Intelligence AI Code Tools](https://www.mordorintelligence.com/industry-reports/artificial-intelligence-code-tools-market); [MCP 1-year anniversary blog](http://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/)

### Serviceable Addressable Market (SAM)

Target: Developers using MCP-compatible AI coding assistants.

- 27 million software developers globally (Stack Overflow 2025)
- 84% use AI tools regularly (Stack Overflow Dev Survey 2025) = 22.7 million; 62% use AI coding assistants daily (GitHub Copilot usage data, 2025)
- Using MCP-compatible tools (Claude Code, Cursor, VS Code Copilot, Gemini CLI): estimated 10–15% = **2.3–3.4 million developers**
- Windows-first platform narrows further to ~65% of that = **1.5–2.2 million TAA (Total Addressable Audience)**

**SAM value:**
- At $29 one-time (Sight Pro only): 2.5M × $29 = **$72.5M one-time opportunity**
- With subscription tier (Memory + Agent at $49–99/month): estimated $15–25/user/year average = **$37–62M ARR potential**

### Serviceable Obtainable Market (SOM)

| Year | Free Users | Paying Users | ARR |
|------|-----------|--------------|-----|
| 2026 | 3,000 | 150 | $15K |
| 2027 | 15,000 | 1,500 | $250K |
| 2028 | 60,000 | 6,000 | $1.2M |
| 2029 | 150,000 | 25,000 | $3.5M |
| 2030 | 350,000 | 80,000 | $8.5M |

### Market Segmentation

| Segment | Profile | Size | Timing | ARPU |
|---------|---------|------|--------|------|
| Developer tools (beachhead) | Developers using MCP AI tools daily | 2.5M | 2026 | $29–99 one-time |
| Prosumer productivity | Power users: writers, researchers, consultants | 10M | 2027 | $15–25/month |
| Enterprise IT | Companies with 50+ AI-tool-using developers | 50K companies | 2027–28 | $25–50/seat/month |
| Accessibility | AT vendors, federal agencies, healthcare IT | $26.8B market | 2028 | Custom/$50–200/user |

### Beachhead: The First 1,000 Users

**Profile:** Developer on Windows, uses Claude Code or Cursor daily, takes 10+ screenshots/day for AI OR has tried Screenpipe and found it too heavy.

**Where they live:**
- r/ClaudeAI (700K+ members)
- r/LocalLLaMA (800K+ members)
- Hacker News (AI + dev tools hit front page regularly)
- Product Hunt (AI tools category)
- MCP directories: mcp.so, glama.ai, smithery.ai, Anthropic's docs

**Why they convert:** Free tier has zero friction (pip install, 30 seconds to run). The "aha moment" is immediate: AI that already knows what's on screen without a screenshot.

### Market Timing: Why Now

1. **MCP has won:** As of Q1 2026, MCP is supported by Claude, Cursor, Gemini, VS Code Copilot, and 50+ enterprise partners (Salesforce, ServiceNow, Workday). Context infrastructure built on MCP now reaches the entire AI ecosystem immediately.

2. **Microsoft Recall is hardware-gated:** Despite GA in April 2025 and late-2025 EU rollout, Recall requires a Copilot+ PC with a Neural Processing Unit — excluding the vast majority of existing developer machines. It has no MCP integration and no developer-facing API. The 90%+ of developers on non-Copilot+ hardware are completely unserved.

3. **Screenpipe priced out developers:** $400 + 5–15% CPU is too expensive and too heavy for developers who just need screen context for coding. The gap is large and unfilled.

4. **AI tool saturation:** 84% of developers use AI tools. The new battleground is context quality. ContextPulse is context infrastructure for that battleground.

5. **No cross-agent shared context exists:** As developers run multiple AI tools simultaneously, the pain compounds. ContextPulse's shared MCP server (all agents use one instance) is uniquely positioned to solve this.

---

## 7. Competitive Analysis

### Competitive Position Map

```
                         HIGH PRICE
                              │
          Limitless           │     Microsoft Recall
          ($19+/seat/mo,      │     (free but
          enterprise)         │     Copilot+ only)
                              │
SINGLE ───────────────────────┼──────────────────── MULTI
MODAL                         │                      MODAL
                              │
          Screenpipe           │   ContextPulse
          ($400 lifetime,      │   (<1% CPU, $29 Pro,
          5–15% CPU)           │   10 MCP tools,
                              │   quad-modal roadmap)
                         LOW PRICE
```

### Competitor Profiles

**Rewind.ai / Limitless (rebranded 2024)**
- Funding: ~$20M raised
- Pricing: Enterprise SaaS ($19+/seat/month), wearable pendant ($99 hardware)
- Key features: Continuous audio + screen recording, AI search, cloud processing
- Weaknesses: Cloud-dependent (data leaves device), expensive, no MCP, hardware required
- Our gap: Local-only, MCP-native, 10× cheaper for developers, no hardware

**Microsoft Recall**
- Funding: Microsoft ($3.2T market cap) — no separate funding
- Pricing: Bundled with Copilot+ PC ($1,200+ hardware requirement)
- Key features: OS-level screen indexing, natural language search, AI timeline
- Weaknesses: Copilot+ hardware only, no AI agent API, privacy backlash, disabled by default
- Our gap: Any Windows PC, MCP-native, privacy-first with pre-storage redaction, cross-platform roadmap

**Screenpipe**
- Funding: $5M seed round (February 2025); 16,000+ GitHub stars
- Pricing: $400 lifetime (Starter), $600 lifetime (Pro+)
- Key features: Continuous video+audio, searchable, pipe plugin ecosystem
- Users: Estimated 2,000–5,000 paying
- Weaknesses: 5–15% CPU, 200–500MB RAM, $400 barrier, no privacy redaction, not developer-focused
- Our gap: <1% CPU, <20MB RAM, 1/14th the price, smart storage (59% savings), pre-storage redaction, MCP-first

**Granola**
- Funding: $43M Series B at $250M valuation (May 2025); earlier $4M seed (2024)
- Pricing: $10–18/month
- Key features: AI meeting notes from ambient audio
- Users: ~100,000+ (estimated; rapid growth post-Series B)
- Weaknesses: Meeting-only (not general), Mac-first, no screen capture, no developer API
- Our gap: Always-on general context (not just meetings), screen + audio + keyboard + pointer, Windows-first

**Otter.ai**
- Funding: $40M+ raised (Series B, 2021)
- Pricing: Free (300 min/mo) / Pro $16.99/mo / Business $30/user/mo / Enterprise custom
- Users: 1M+ (self-reported)
- Weaknesses: Cloud-dependent, meeting-only, no screen capture, no MCP
- Our gap: On-device, full context platform, developer-focused, MCP infrastructure

**On-demand MCP screenshot tools (5+ open-source)**
- Funding: None
- Pricing: Free
- Key features: Single-shot screen capture for AI agents
- Weaknesses: No daemon, no history, no OCR, no search, no temporal context, no diff detection
- Our gap: Always-on continuous capture, 30-min rolling buffer, temporal search, 10 MCP tools vs. their 1–2

### ContextPulse Unique Features (Nothing Else Has These)

1. **Always-on MCP daemon** — The only context tool designed as continuous background infrastructure, not a request-response app
2. **Rolling buffer with temporal search** — "What was on screen 10 minutes ago?" — unanswerable by any competitor
3. **Smart storage modes** — Stores text-only when content is text-heavy; saves 59% disk vs. image-only competitors
4. **Pre-storage redaction** — Sensitive data is removed before writing to disk; competitors store raw text including API keys, passwords
5. **Event-driven capture** — Window focus + idle wake + monitor crossing trigger immediate captures; not just timer-based
6. **Cross-agent awareness** — Tracks which AI agents (Claude, Cursor, Gemini) consume which context, with per-client stats
7. **Token cost estimation** — Per-frame API cost estimates; unique cost-awareness for AI agent consumption
8. **Accessibility architecture** — The only AI context platform designed to serve both developers and disabled users through the same modular MCP interface

### Barriers to Entry for Competitors

| Barrier | Description | Time to Replicate |
|---------|-------------|------------------|
| Architectural | Lightweight always-on capture with smart storage requires ground-up design | 12–18 months |
| Cross-modal learning | Requires all 4 modalities running simultaneously + temporal alignment | 24–36 months |
| Patent claims | Priority date established; specific techniques protected | Cannot be directly copied |
| MCP ecosystem position | First-mover in MCP context infrastructure; directories, integrations, community | 6–12 months to catch up |
| Accessibility IP | Class 10 trademarks + AT market relationships | 18–24 months |

---

## 8. Business Model & Monetization

### Pricing Tiers

```
FREE — ContextPulse Sight Community
├── Always-on daemon (5-sec capture interval)
├── 3 basic MCP tools (screenshot, recent, buffer_status)
├── 10-minute rolling buffer (10 frames)
├── Privacy controls (blocklist, pause hotkey, auto-pause on lock)
└── Community support (GitHub Issues)

SIGHT PRO — $29 one-time
├── All 10 MCP tools (OCR search, time-travel, clipboard, agent stats, activity)
├── Smart storage modes (59% disk savings)
├── 30-minute rolling buffer (10 frames/monitor)
├── Activity database + FTS5 full-text search
├── Multi-agent awareness + per-agent stats
├── Token cost estimation per frame
├── Email support
└── All future Sight updates (perpetual license)

MEMORY — $49/month | $149/lifetime
├── Cross-session persistent memory for AI agents
├── Multi-agent shared context (Claude + Cursor + Gemini simultaneously)
├── MCP tools: memory_store, recall, search, forget
├── Confidence scoring + temporal decay
└── Memory audit UI

PRO BUNDLE (Sight + Voice + Memory) — $99/month | $249/lifetime
├── All Sight Pro features
├── ContextPulse Voice (local Whisper, hold-to-talk, always-on option)
├── Screen-aware vocabulary biasing (cross-modal)
├── Voice-screen temporal correlation
├── Memory package
└── Priority support (1-business-day response)

TEAM — $20/seat/month (5-seat minimum = $100/mo, increasing to $25/seat by 2029)
├── All Pro Bundle features
├── Team shared memory (agents share context across team)
├── Admin console (usage analytics, audit logs)
├── SSO (Okta, Google Workspace)
├── Dedicated Slack channel support
└── 5 onboarding calls included

ENTERPRISE — Custom pricing
├── All Team features
├── On-premise deployment option
├── SOC 2 Type II compliance documentation
├── AI context audit trails (for AI governance / compliance)
├── SLA (99.9% uptime for cloud components)
├── Custom integrations (ServiceNow, Jira, Salesforce)
└── Accessibility compliance package (Section 508)
```

### Revenue Streams

| Stream | Phase | Expected Contribution |
|--------|-------|----------------------|
| One-time purchases (Sight Pro, Memory lifetime) | Now | 80% Year 1 |
| Monthly subscriptions (Pro Bundle, Team) | Year 2 | 60% Year 2 |
| Enterprise contracts | Year 2–3 | 25% Year 3+ |
| Accessibility licensing (AT vendors) | Year 3 | 10% Year 4+ |
| SBIR/STTR grants | Year 2–3 | Non-dilutive capital |

### Unit Economics

| Segment | CAC | LTV | LTV:CAC | Payback Period |
|---------|-----|-----|---------|----------------|
| Sight Pro (one-time, $29) | $8–15 (content/SEO) | $29 | 2–4× | Day 1 |
| Memory (lifetime, $149) | $15–30 | $149 | 5–10× | Day 1 |
| Pro Bundle ($99/mo) | $30–60 | $99×18mo = $1,782 | 30–60× | <1 month |
| Team (5 seats, $150/mo) | $200–500 | $150×24mo = $3,600 | 7–18× | 1–4 months |
| Enterprise (custom ACV) | $2,000–5,000 | $24K–60K/yr | 12–30× | 1–3 months |

**Gross margin:** ~85–90% (software; costs are AWS Lambda + CDN + SES only)

---

## 9. Go-To-Market Strategy

### GTM Philosophy: Distribution Before Monetization

Sight Free is the wedge. Give developers a genuinely useful free product with zero friction. The "aha moment" (AI sees your screen without a screenshot, finds context from 10 minutes ago, searches your clipboard) drives organic sharing and paid upgrade. Community presence reduces CAC compounding over time.

### Phase 1 (Months 1–6): Developer Community

**Goal:** 3,000 free users, 150 paying, $15K revenue

**Priority channels:**

1. **MCP ecosystem directories** — Submit to mcp.so, glama.ai, smithery.ai, Anthropic's MCP docs. Highest-intent discovery for exact ICP. One-time effort, passive ongoing installs.

2. **Product Hunt launch** — Target Top 5 in AI Tools. Prep: hunter outreach (2 weeks), teaser posts, day-of community activation. Launch Tuesday. Expected 500–2,000 signups from a strong placement.

3. **Show HN post** — "Show HN: I built an always-on MCP screen context daemon that gives Claude/Cursor temporal memory of your desktop." HN front page = highest-quality developer traffic.

4. **Reddit seeding** — Authentic posts in r/ClaudeAI, r/LocalLLaMA, r/ChatGPT showing the use case: "How I eliminated 20 manual screenshots a day." No promotional tone; solve the problem for real users.

5. **Open-source free tier on GitHub** — Star count = social proof for developers. Issues = free product research. Contributors = ecosystem.

### Phase 2 (Months 6–12): Prosumer Expansion

**Goal:** 15,000 free users, 1,500 paying, $250K ARR

**New channels:**

1. **Developer newsletters** — TLDR (750K subscribers), The Pragmatic Engineer (150K), Bytes/JavaScript Weekly. Test $500–2,000 sponsorships; track conversions.

2. **YouTube partnerships** — Target developers with 20K–200K subscribers making AI + productivity content. Offer affiliate program (20% of first-year revenue).

3. **Content SEO** — Target: "give claude code visual context," "screen capture mcp server," "ai coding assistant screenshot," "screenpipe alternative." These queries have purchase intent; conversion is high.

4. **Cursor/Anthropic partnerships** — Pursue official listing in Cursor's MCP marketplace and Anthropic's integration directory. These drive installs at zero CAC.

5. **Memory launch as new PH/HN moment** — Separate launch for ContextPulse Memory; cross-sell to existing Sight user base.

### Phase 3 (Year 2): Enterprise Pilots

**Goal:** 10 enterprise pilots, $100K+ ACV total, clear path to $1M ARR

**Motion:** Inbound-led (engineering managers who find ContextPulse via content) → 30-day team pilot → onboarding → expansion.

**Enterprise value props:**
- AI usage audit trails (what context is being fed to AI agents at the org level)
- Cognitive load analytics from Keys + Flow data
- AI governance compliance documentation

### Phase 4 (Year 2–3): Accessibility Market

**Goal:** 3 SBIR applications, 2 AT vendor partnerships, $50K+ accessibility revenue

**Motion:** SBIR grant → academic validation → AT vendor licensing conversations → federal agency pilot.

---

## 10. Marketing Strategy

### Brand Positioning

**Hero statement:** "Your AI remembers what you forget."

**Proof statement:** "Always-on screen, voice, keyboard, and pointer context — delivered to any AI agent via MCP. Under 1% CPU. Your data never leaves your machine."

**Positioning angles (scored, best first):**

| Angle | One-liner | Score |
|-------|-----------|-------|
| Lightweight Champion | "Screenpipe is $400 and 15% CPU. ContextPulse is $29 and 1% CPU." | 23/25 |
| 30-Second Setup | "pip install, run, connect. Your AI sees your screen in 30 seconds." | 21/25 |
| MCP-Native Infrastructure | "Not a screen recorder. The context layer your AI agents are missing." | 21/25 |
| Privacy-First | "Your screen never leaves your machine. No cloud, no accounts, no telemetry." | 20/25 |

### Content Pillars

1. **Technical depth** — How event-driven capture works, why smart storage matters, what MCP protocol enables. Builds trust and SEO authority with developers.

2. **Productivity transformation** — Before/after: "I used to take 20 screenshots a day. Now I take zero." Specific workflow stories.

3. **Privacy narrative** — Counter to Microsoft Recall's controversy. Every local-processing angle reinforces "your data stays yours."

4. **Accessibility** — Frame as the most demanding use case, not charity. "If it works for motor impairment, it's good enough for everyone."

### Distribution Channel ROI Rankings

| Rank | Channel | Expected ROI | Investment Level |
|------|---------|-------------|-----------------|
| 1 | MCP ecosystem directories | Very High | Low (one-time) |
| 2 | Hacker News Show HN | Very High | Low (writing) |
| 3 | r/ClaudeAI, r/LocalLLaMA | High | Low (participation) |
| 4 | Product Hunt | High | Medium (launch prep) |
| 5 | GitHub open-source (free tier) | High | Medium (ongoing) |
| 6 | Developer newsletters | Medium-High | Medium ($500–2K/send) |
| 7 | Content SEO | Medium | Medium-High |
| 8 | YouTube partnerships | Medium | High |
| 9 | Twitter/X developer community | Medium | Low-Medium |
| 10 | Paid search | Low-Medium | High (ongoing spend) |

### PR Strategy

**Narrative:** "The responsible, developer-grade alternative to Microsoft Recall."

**Story angles:**
- "Privacy-first AI context that works on any machine — not just $1,000+ Copilot+ PCs" (hardware access story)
- "The $29 tool that replaces 20 screenshots per day" (transformation story)
- "How a solo founder built the MCP context layer the AI ecosystem was missing" (founder story)

**Target media:** The Register (privacy), TechCrunch (AI tools), DEV.to, The Pragmatic Engineer, Hacker News front page.

---

## 11. Sales Strategy

### Tier 1: Self-Serve (Individual, Sight Pro)

Zero human involvement. Free → install → use → Pro features locked → nag dialog → Gumroad checkout → license email → unlock.

- Payment: Gumroad initially → Stripe direct at scale (save 7% processing fee)
- Support: GitHub Issues (free) + email within 2 business days (paid)

### Tier 2: Team (Inside Sales, Year 2)

- Lead source: Free users sharing with their team + inbound from content
- Process: Email inquiry → 30-min Zoom demo → pilot agreement → onboarding → expansion
- Tools: HubSpot (free CRM), Lemlist (sequences)
- Close time: 2–4 weeks
- Target: 20 team accounts by end of Year 2

### Tier 3: Enterprise (Outbound, Year 2–3)

- Lead generation: LinkedIn outreach to engineering managers at AI-heavy companies (Stripe, Shopify, Figma — companies with large developer teams and strong AI adoption)
- Process: Cold email → discovery call → pilot → procurement → annual contract
- ACV: $12,000–$60,000/year
- Close time: 2–4 months (typical enterprise)

### Partnership / Channel

| Partner | Type | Value |
|---------|------|-------|
| Anthropic (MCP docs) | Listing | Massive organic installs at zero CAC |
| Cursor | Marketplace | High-intent discovery from active AI users |
| Accessibility consultancies | Referral | Federal clients need Section 508 compliant tools |
| IT distributors (CDW, SHI) | Volume licensing | Enterprise channel (Year 3) |

---

## 12. Financial Projections

### Key Assumptions

| Assumption | Value | Rationale |
|-----------|-------|-----------|
| Sight Pro price | $29 one-time | Validated by Voiceasy; 1/14th of Screenpipe |
| Memory price | $49/month or $149/lifetime | Industry norm for AI productivity tools |
| Pro Bundle | $99/month | Developer tools sweet spot |
| Team (5 seats min) | $150/month | Low barrier for small teams |
| Free-to-Pro conversion | 3–5% | Developer tool industry standard with strong free tier |
| Monthly subscription churn | 3% | Better than 5% average — high switching cost once integrated |
| CAC (self-serve) | $8–20 | Content-led growth, minimal paid spend |
| CAC (enterprise) | $2,000–5,000 | Founder-led sales |
| Gross margin | 85–90% | Software; infra costs are AWS Lambda + CDN + SES |

### 5-Year Revenue Model

| Year | Free Users | Paying Users | ARR | Gross Profit | EBITDA |
|------|-----------|--------------|-----|-------------|--------|
| 2026 | 3,000 | 500 | $14.5K | $13.8K | ($26.2K) |
| 2027 | 12,000 | 2,500 | $77.2K | $73.4K | ($62.6K) |
| 2028 | 40,000 | 6,600 | $321.1K | $306.1K | $48.1K |
| 2029 | 100,000 | 27,000 | $848.7K | $803.7K | $385.7K |
| 2030 | 250,000 | 75,000 | $1.56M | $1.46M | $870K |

*Conservative bootstrap scenario (Sight Pro + Team + Enterprise tiers only). Full product vision with Voice + Memory + Pro Bundle adds 2–3× upside. See FINANCIAL_MODEL.md for complete revenue waterfall, tier-by-tier breakdown, and funded scenario.*

**→ See FINANCIAL_MODEL.md for monthly breakdown, revenue mix by tier, and sensitivity analysis**

### Break-Even Analysis

**Bootstrap phase (solo founder, ~$500/month fixed costs):**
- Break-even revenue: ~$6,000/year
- Break-even users: 207 Sight Pro sales = achievable in Month 3–4 post-launch

**Post-seed ($11,300/month burn: 1 engineer + infra + marketing):**
- Break-even ARR: ~$285,000
- Timeline: Q2 2028 (18–24 months after seed)
- See FINANCIAL_MODEL.md for sensitivity analysis at different founder salary levels

### Funding Plan

| Stage | Amount | Timing | Use of Funds |
|-------|--------|--------|-------------|
| Bootstrap | $0 (self-funded) | Now–Q4 2026 | Launch; prove demand |
| SBIR Phase I | $275K non-dilutive | Q2 2027 | Accessibility features |
| Seed round | $500K–$1.5M | Q4 2026–Q1 2027 | +1 engineer, macOS, enterprise |
| SBIR Phase II | $1.8M non-dilutive | Q4 2027 | Scale accessibility platform |
| Series A or exit | $3–8M raised or acquisition | 2028–2029 | Scale to $3M+ ARR or exit |

### Use of $1M Seed

| Use | Amount | % |
|-----|--------|---|
| Engineer (1 FTE × 18 months, $100K/yr) | $150K | 15% |
| macOS/Linux port (contract work) | $50K | 5% |
| Keys + Flow development (contract) | $75K | 7.5% |
| Sales & marketing | $200K | 20% |
| AWS infrastructure | $50K | 5% |
| Legal (patent prosecution, contracts) | $25K | 2.5% |
| SOC 2 Type II audit | $30K | 3% |
| Runway / operating reserve | $420K | 42% |

---

## 13. Accessibility as Strategic Advantage

### The $26.8B Market Hiding in Plain Sight

ContextPulse's multi-modal architecture maps perfectly onto the four major disability categories:

| Disability | Module | Capability |
|------------|--------|------------|
| Visual impairment | Sight | AI-powered screen narration that understands context, not just reads text |
| Speech impairment | Voice | Real-time transcription + rephrasing + captioning, fully local |
| Motor impairment | Keys + Flow | Typing fatigue detection, pointer tremor detection, predictive input |
| Cognitive impairment | Memory | Activity recall, context summarization, cross-session working memory augmentation |
| All combined | Learning Engine | Personalizes to each user's specific impairment patterns over time |

**What makes this different from traditional AT:**

Traditional assistive tech (JAWS, Dragon, Windows Magnifier):
- Single-purpose: one tool, one disability
- Static: doesn't learn or adapt
- No AI understanding: reads UI labels, doesn't comprehend context
- Siloed: each tool is independent

ContextPulse accessibility:
- **Context-aware:** Sight understands what a screen means, not just what it says
- **Adaptive:** The learning engine personalizes to the specific user's patterns
- **Interoperable:** MCP means any AI assistant becomes an accessibility tool
- **Compounding:** The longer it runs, the better it gets for that specific user

### Pointer Tremor Detection — A Novel Medical-Adjacent Capability

The Flow module's pointer efficiency analysis and click-miss-retry detection constitutes a first-of-its-kind AI-native motor assessment system:

- **Non-invasive longitudinal tracking:** Continuous pointer data over months generates a motor control baseline with natural variation — enabling early detection of changes associated with early-stage Parkinson's, MS, or ALS
- **Not a medical device** (important legal distinction): ContextPulse generates data; clinicians interpret it. This keeps us out of FDA Class II territory.
- **WCAG 2.1 AA compliance roadmap:** Sight + Voice target full WCAG 2.1 AA conformance by Q2 2027; required for federal procurement and VPAT completion. Keyboard navigation and screen reader compatibility (NVDA/JAWS) built in from first release.
- **Workplace ADA applications:** Employers with accommodation obligations can use tremor + fatigue data to objectively justify ergonomic interventions
- **Gaming/esports:** Motor compensation and aim assist for players with motor differences

### SBIR/STTR Grant Strategy

| Program | Agency | Topic | Amount | Timeline |
|---------|--------|-------|--------|---------|
| SBIR Phase I | NIDILRR | AI-powered AAC (Augmentative and Alternative Communication) — Voice module | $275K | Q3 2027 submission |
| SBIR Phase I | NIH/NEI | AI screen interpretation for visually impaired — Sight module | $275K | Q4 2027 submission |
| SBIR Phase II | NIDILRR | Full accessibility platform | $1.8M | Q2 2028 |
| DoD SBIR | DARPA/VA | TBI cognitive aids — Memory module | $275K | Q1 2028 |

**Preparation:** NIDILRR offers free pre-application consultations. Schedule one in Q4 2026 to understand solicitation alignment before spending 6 months on a proposal.

### Section 508 Enterprise Pathway

Federal agencies are legally required to purchase Section 508-compliant software. An enterprise ContextPulse package with documented WCAG 2.1 AA compliance and FedRAMP-aligned security posture qualifies for direct procurement without competitive bidding under the $250K simplified acquisition threshold.

**Department of Veterans Affairs (VA)** is the primary federal target: the VA is the largest U.S. AT procurer, serving 400,000+ veterans with TBI, motor impairments, and PTSD. The VA's fiscal year procurement window runs October–September; enterprise pilots should be initiated by Q1 2028 to appear in FY2028 procurement cycles. Contact: VA's Office of Information and Technology (OIT) vendor portal plus direct outreach to VA National Center for Cognitive Behavioral Therapy (PTSD cognitive aids).

**Primary target:** Department of Veterans Affairs (VA) — largest U.S. AT procurer, serves veterans with TBI and motor impairments.

### Partnership Opportunities

| Partner | Opportunity |
|---------|-------------|
| Freedom Scientific (JAWS) | License AI context layer to enhance JAWS with contextual understanding |
| NV Access (NVDA) | Open-source Sight as NVDA plugin — goodwill, reach, credibility |
| Microsoft Accessibility | MCP integration with Seeing AI; co-marketing for Recall alternative |
| Apple Accessibility | macOS port + VoiceOver integration; AT grant potential |
| Tobii (eye tracking) | Eye tracking as 5th input modality for cross-modal learning |

---

## 14. Exit Strategy

### Primary Exit: Strategic Acquisition (2028–2030)

Target exit valuation: **$15M–$60M** at 15–25× ARR (strategic acquisition premium; developer tools category benchmark: Loom ~20× ARR, GitHub ~30× ARR). Conservative 15× applied to ContextPulse base case. See ACQUISITION_TARGETS.md for full acquirer-by-acquirer analysis.

**What drives the acquisition premium (above pure ARR multiple):**
1. Cross-modal learning engine — 2–3 years ahead of any competitor; acquirer saves that development time
2. IP portfolio — utility patent + trademark coverage across AT market prevents IP disputes
3. MCP ecosystem position — First-mover brand recognition in the AI context infrastructure market
4. User base quality — Developer users are sticky and hard to acquire organically
5. Accessibility market access — Opens a government procurement channel acquirers want but can't easily enter
6. Provisional patent on accessibility AT claims — Blocks competitors from the specific technical approach

### Acquisition Target Ranking

**Tier 1 — Highest Likelihood**

| Acquirer | Strategic Fit | Why They Buy | Valuation Range |
|----------|--------------|--------------|----------------|
| Anthropic | Building Claude's developer ecosystem; ContextPulse is native Claude infrastructure | Makes context capture a built-in Claude feature; eliminates a competitive gap | $15M–$50M |
| Cursor (Anysphere) | Raised $100M Series B (2025) at $2.5B valuation; competing hard with Copilot | Built-in screen context gives Cursor a capability GitHub Copilot doesn't have | $20M–$60M |
| Microsoft | Recall failed; needs privacy-first alternative; AT capabilities align with Seeing AI | Acquires the developer trust Recall lost; Accessibility team gets a compounding AT platform | $30M–$100M |

**Tier 2 — Possible**

| Acquirer | Strategic Fit | Valuation Range |
|----------|--------------|----------------|
| Notion | "AI workspace" vision; context layer feeds Notion's knowledge management | $15M–$40M |
| Salesforce | Einstein AI needs developer workflow context | $25M–$75M |
| Apple | macOS + VoiceOver integration; AT as core Apple value | $20M–$80M |

**Tier 3 — Secondary Options**

| Acquirer | Scenario |
|----------|---------|
| ServiceNow / Atlassian | Enterprise workflow context + AI governance audit trail |
| PE rollup (developer tools) | 5–8× ARR if no strategic buyer by Year 5 |
| Licensing deal | License cross-modal learning + AT claims to 2–3 AT vendors without full acquisition |

### Exit Timeline

| Year | State | Exit Readiness |
|------|-------|---------------|
| 2026 | Sight + Voice launched, 3K users | Too early; build first |
| 2027 | $250K ARR, patent filed, Keys in beta | Early conversations OK; not optimal |
| 2028 | $1.2M ARR, enterprise pilots, SBIR funded | Serious acquisition conversations begin |
| 2029 | $3.5M ARR, cross-modal learning shipped | Primary acquisition window |
| 2030 | $8.5M ARR, 80K paying users | Strong negotiating position; or Series A for IPO path |

---

## 15. Risk Analysis

### Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Windows API changes break capture | Medium | High | mss abstracts Win32; monitor upstream; test on each Windows update |
| macOS port harder than expected | High | Medium | Not on critical path; Windows alone is 3 years of runway |
| OCR accuracy poor on some apps | Medium | Medium | App-specific OCR profiles + image-only fallback already built |
| Cross-modal learning doesn't improve accuracy measurably | Medium | High | Phase carefully: ship 4 modalities first, A/B test learning in controlled conditions |
| SQLite performance at scale | Low | Medium | FTS5 handles millions of rows; quarterly benchmarks; SQLite upgrade path documented |

### Market Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Microsoft Recall adds MCP + expands beyond Copilot+ hardware | Low-Medium | High | Recall requires NPU silicon — a hardware mandate Microsoft cannot lift overnight. ContextPulse lead: multi-platform (macOS/Linux), cross-modal roadmap, and accessibility IP Recall cannot match. |
| Anthropic builds screen context into Claude natively | Medium | Very High | Become the recommended MCP server first; acquisition is the ideal outcome of this scenario |
| Screenpipe drops CPU usage + adds MCP as primary interface | Medium | High | Lightweight architecture is structural; accelerate cross-modal learning as the insurmountable moat |
| MCP protocol replaced by different standard | Low | Medium | Transport layer abstracted; porting to new protocol is feasible |

### Regulatory Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| GDPR enforcement on screen capture tools | Medium | High | On-device processing is best-possible GDPR posture; no data leaves device; document prominently |
| CCPA expansion covers keyboard/pointer capture | Low | Medium | Consent-first design; clear disclosure in first-run wizard; opt-in defaults |
| EU AI Act high-risk classification for AT use case | Low | High | Monitor implementation; disability AI may get favorable treatment; delay EU AT launch until clear |

### Execution Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Solo founder bandwidth (5 hrs/week vs. portfolio) | High | Medium | If traction confirmed by Month 6, increase to 20+ hrs/week from other ventures |
| Key person risk (David only engineer) | High | Medium | Document architecture; open-source free tier creates contributor pool |
| Delayed launch (perfect vs. shipped) | High | Low | Set hard launch date; ship even if imperfect; iterate in public |
| Enterprise sales cycle too long for solo founder | Medium | Low | Self-serve covers burn; enterprise is upside, not required for survival |

### IP Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Provisional patent rejected on § 101 grounds | Medium | Low | Rejection doesn't kill business; trade secrets + first-mover still hold |
| Prior art discovered before non-provisional conversion | Low | Medium | Commission prior art search ($500–2K) before non-provisional filing |
| Trademark opposition | Low | High | No conflicts found; monitor TESS monthly for 12 months post-filing |

---

## 16. Action Plan: Next 90 Days

### Weeks 1–2 (March 22 – April 4): IP & Infrastructure — Owner: David Jerard

- [ ] **File trademark** (Classes 9, 10, 42) via USPTO TEAS Standard — $1,050. Do this first; no upside to waiting.
- [ ] **File provisional patent** via USPTO Patent Center — $65. Every day of delay is a day of priority date risk.
- [ ] **Register copyright** at copyright.gov — $65. Required for statutory damages eligibility.
- [ ] **Deploy Lambda infrastructure** — DynamoDB + Lambda + SES. Required for Gumroad → license key flow. Blocking Gumroad launch.

### Weeks 3–4 (April 5–18): Product Launch Prep — Owner: David Jerard

- [ ] **Create Gumroad listings** — Sight Free + Sight Pro ($29). Use "Lightweight Champion" positioning angle.
- [ ] **Publish to PyPI** — `pip install contextpulse-sight` public wheel. Repo stays private.
- [ ] **End-to-end daemon testing** — 30 manual scenarios: first-run, settings, blocklist, license dialog, license email.
- [ ] **Update landing page** — New comparison table (vs Screenpipe, vs manual), "Install in 30 seconds" section, before/after transformation story.
- [ ] **Submit to MCP directories** — mcp.so, glama.ai, smithery.ai. Passive ongoing installs; one-time effort.

### Weeks 5–6 (April 19 – May 2): Product Hunt Launch — Owner: David Jerard

- [ ] **Hunter outreach** — Identify 5 PH hunters with 1,000+ followers; introduce product; ask for hunting.
- [ ] **Teaser posts** — r/ClaudeAI, r/LocalLLaMA in the week before PH. "Coming soon" without the link; build anticipation.
- [ ] **Launch Tuesday** — PH + Show HN simultaneously. Coordinate Discord/Reddit for same-day comments.
- [ ] **Monitor for 48 hours** — Reply to every PH comment within 1 hour on day 1. This drives the PH algorithm.

### Weeks 7–10 (May 3–31): Voice Port — Owner: David Jerard

- [ ] **Port Voiceasy as ContextPulse Voice** — Integrate into ContextPulse monorepo. Shared activity DB, shared MCP server.
- [ ] **Screen-aware vocabulary biasing** — OCR text from Sight → Whisper vocabulary bias (Python visible → bias "def" not "deaf"). Cross-modal proof-of-concept.
- [ ] **30-session stability test** — Voice + Sight running together for 30 days. No crashes = ready to launch.
- [ ] **Plan Voice launch** — Separate PH + HN moment for Voice; cross-sell to Sight user base.

### Weeks 11–12 (June 1–14): Enterprise Prep + Memory Design — Owner: David Jerard

- [ ] **SOC 2 gap assessment** — Document current state vs. SOC 2 Type II requirements. Not filing yet; understand the gap.
- [ ] **Design Memory package** — MCP tools spec, SQLite schema, multi-agent namespace design. Write spec before code.
- [ ] **Recruit 3 design partners** — 3 developers for Memory beta. Offer free lifetime access in exchange for weekly feedback calls.
- [ ] **NIDILRR pre-application consultation** — Schedule free 15-minute call to assess SBIR solicitation alignment.

### 90-Day KPIs

| Metric | Target |
|--------|--------|
| Trademark filed | ✓ by April 4 |
| Patent filed | ✓ by April 4 |
| Lambda deployed | ✓ by April 4 |
| Gumroad listings live | ✓ by April 18 |
| PyPI published | ✓ by April 18 |
| MCP directory listings | 5+ by April 18 |
| Product Hunt position | Top 5 AI Tools |
| Free users by June 14 | 500+ |
| Paying users by June 14 | 25+ |
| Revenue by June 14 | $750+ |

---

*ContextPulse is a Jerard Ventures LLC product. All financial projections are estimates based on market research and comparable company benchmarks. This document is confidential and intended for internal planning and investor discussions only under NDA.*

*Last updated: 2026-03-21*
