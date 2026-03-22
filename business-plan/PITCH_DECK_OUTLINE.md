# ContextPulse — Investor Pitch Deck Outline
**12 Slides | March 2026 | Jerard Ventures LLC**

---

## Slide 1: Title / Hook

**Headline:** "AI can write your code. It can't see your screen."

**Visual:** Split screen — left: developer staring at complex codebase; right: Claude AI assistant with empty context window saying "I don't know what you're working on."

**Sub-text:** ContextPulse is the persistent context layer for AI agents.

**Bottom:** Logo | contextpulse.ai | david@jerardventures.com | March 2026

**Verbal hook:** "Every AI coding session starts blind. ContextPulse fixes that."

---

## Slide 2: The Problem (Make It Personal)

**Headline:** "Developers spend 2+ hours/day re-explaining context that AI should already know"

**Three pain panels:**
1. **Context amnesia** — "My AI assistant knows my coding style... until I close the terminal. Then we start over." [Quote from target user archetype — validate with user interviews pre-pitch]
2. **Single-modal blindness** — Screenshot tools capture what you see. Voice tools capture what you say. Clipboard tools capture what you copy. None of them talk to each other. None maintain history. Each AI session starts blind.
3. **Privacy risk** — "Microsoft Recall was theoretically great — until it was delayed a full year by a privacy scandal, and when it finally shipped, it required an $800 laptop you probably don't have."

**Exact data points for the slide:**
- **27M** professional developers globally (Evans Data Corporation, 2025)
- **62%** use AI coding assistants daily (GitHub Octoverse 2024); **80%+** will by 2028
- **97M** monthly MCP SDK downloads (December 2025) — the protocol all their AI tools speak
- **0** of those AI tools know what the developer was just looking at
- The market leader (Limitless/Rewind, $33M raised) was **acquired and shut down** December 19, 2025

**Verbal hook for this slide:** "Limitless raised $33 million to solve this problem. Meta bought the company in December. And immediately shut down the product. The market is open — and bigger than ever."

---

## Slide 3: The Solution

**Headline:** "ContextPulse: Always-on context for AI agents — screen, voice, keyboard, pointer"

**Simple diagram:**
```
Your Computer → [ContextPulse] → MCP Protocol → Any AI Agent
(What you see)     On-device      Standard API    Claude, Cursor
(What you say)     No cloud                       Copilot, Gemini
(What you type)    <1% CPU
(How you click)    <20MB RAM
```

**Three differentiators:**
1. **MCP-native** — plugs into any AI agent (Claude Code, Cursor, Copilot) in 30 seconds
2. **On-device only** — no cloud, no privacy compromise, works in EU
3. **Cross-modal learning** — the system gets smarter about you over time

**Demo moment (live or GIF):** "I've been away from this project for 3 days. [runs get_context_at] — there's everything I was working on."

---

## Slide 4: Product Demo

**Headline:** "30 seconds to install. Runs invisibly. Works with every AI tool."

**Format:** 3-step walkthrough with screenshots/GIF

**Step 1 — Install:**
```bash
pip install contextpulse-sight
contextpulse-sight
```
→ System tray icon appears. Capture begins.

**Step 2 — Use any AI agent:**
→ Screenshot shows Claude Code with ContextPulse MCP tools listed
→ Screenshot shows `get_recent` returning last 3 screen captures with OCR text

**Step 3 — Context delivered:**
→ Screenshot of Claude Code with full context: "You were working on the authentication module. Here's the error you saw at 2:14pm..."

**Exact benchmark numbers to include on the slide:**
- **145 tests passing** (0 failing) — production-ready
- **10 MCP tools** vs. Screenpipe's 3 (partial add-on) vs. MS Recall's 0
- **<1% CPU always-on** vs. Screenpipe 5–15% vs. MS Recall 2–5% (NPU required)
- **<20MB RAM** vs. Screenpipe 200–500MB
- **~120MB/hour** disk usage vs. Screenpipe's 2–5GB/hour (video)
- **59% storage savings** from content-adaptive OCR (measured benchmark)
- **0 cloud dependencies** — no account, no API key, works in EU, works in a Faraday cage

---

## Slide 5: Market Opportunity

**Headline:** "A $36B market with no dominant player"

**TAM / SAM / SOM visualization (concentric circles):**

- **TAM: $15B** — AI Productivity Tools, 2024; growing to $37–68B by 2030 at 26–28% CAGR ([Grand View Research](https://www.grandviewresearch.com/industry-analysis/ai-productivity-tools-market-report))
- **SAM: $7.4B** — AI Developer / Code Tools, 2024; growing to $26B by 2030 at 27.1% CAGR ([Grand View Research AI Code Tools](https://www.grandviewresearch.com/industry-analysis/ai-code-tools-market-report))
- **SOM: $50M** — MCP-native context providers for the 16.7M developers actively using AI coding assistants

**Two market expansion paths:**
- **Developer productivity** → 27M professional developers (Evans Data 2025), $200–600/year tool spend
- **Accessibility** → $26.8–$33.3B global assistive technology market (2024), growing to $65.2B by 2034 at ~8.9% CAGR ([IMARC Group](https://www.imarcgroup.com/assistive-technology-market)); $3.5B+ U.S. federal Section 508 procurement annually; no AI-native MCP player

**Why now — exact data points:**
- MCP ecosystem: 100K downloads (Nov 2024) → 8M (Apr 2025) → **97M monthly (Dec 2025)** — 970x growth in 14 months. Linux Foundation standardization in progress. 5,500+ official servers (PulseMCP), 16,000+ total. ([MCP 1-year anniversary blog](http://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/))
- Microsoft Recall shipped April 2025 but remains **hardware-gated** (requires Copilot+ PC: 40+ TOPS NPU, 16GB RAM, $800–$1,500 new PC). **~85–90% of developer machines cannot run it**. Recall has zero MCP integration. Microsoft itself pulled back on broader Copilot integrations in Windows 11 (March 2026 — acknowledged platform "went off track").
- Meta **acquired** Limitless (Dec 2025) — the market leader with $33M raised — for undisclosed price and **immediately shut it down** (Dec 19, 2025). At ~$2M ARR. That's a $2M ARR business being acquired by the world's 4th largest company because the category is strategically critical.
- Otter.ai reached **$100M ARR** (Dec 2025) on meeting audio alone. Granola raised **$67M at $250M valuation** on meeting notes. The "persistent AI context" category is validated at billion-dollar scale — the developer desktop segment is wide open.

---

## Slide 6: Competitive Landscape

**Headline:** "No one is building the full picture"

**2x2 matrix:**
- X-axis: Single-modal ← → Multi-modal
- Y-axis: Cloud-dependent ← → On-device
- **ContextPulse: top-right quadrant (multi-modal + on-device) — alone**
- Microsoft Recall: lower-right (single-modal + on-device, but hardware-gated to ~10–15% of PCs; no MCP)
- Screenpipe: lower-right (screen+audio, 5–15% CPU, no MCP, $400 one-time)
- Otter/Granola: lower-left (meeting audio only + cloud; Granola: $67M raised, $250M valuation; Otter: $100M ARR)
- Limitless: **ACQUIRED by Meta, Dec 2025 — desktop product shut down Dec 19, 2025**

**Comparison table (abbreviated):**

| | ContextPulse | MS Recall | Screenpipe | Granola | Otter.ai |
|---|---|---|---|---|---|
| MCP-native | ✅ 10 tools | ❌ | Partial (3) | ❌ | ❌ |
| CPU overhead | **<1%** | ~2–5% (NPU) | 5–15% | ~2% (meetings) | N/A |
| New hardware required? | ❌ | ✅ $800–1,500 PC | ❌ | ❌ | ❌ |
| EU / on-device | ✅ | ❌ (EEA delayed) | ✅ | ❌ (cloud) | ❌ (cloud) |
| Accessibility architecture | ✅ (Class 10 TM) | ❌ | ❌ | ❌ | ~ (captioning only) |
| Pre-storage redaction | ✅ (10+ categories) | ❌ | ❌ | ❌ | ❌ |
| Entry price | **$0/$29** | Free + $800–1,500 hardware | $0/$400 | $0/25 meetings then $18/mo | $0 limited/$10/mo |

**Exact talking points for Slide 6:**
- Limitless raised $33M, peaked at $350M valuation, acquired at ~$2M ARR → **validates category, removes threat**
- Screenpipe: 17,200+ GitHub stars, $400 one-time, no subscription model, 5–15% CPU, now pivoting to "computer use SDK" (different category) — serves OSS power users, not enterprise
- Granola: $67M raised, $250M valuation, meeting-only, macOS-primary, MCP in Business tier (meeting context only) — validates premium multiples for context tools
- Otter: $100M ARR, HIPAA compliant (July 2025), MCP Server live — proves meeting context = $100M business; desktop context opportunity is equivalent or larger
- **No competitor is building MCP-native, always-on, on-device, multi-modal desktop context for developers.** ContextPulse is alone in the upper-right quadrant.

**Key insight:** "Meta acquired Limitless (the market leader) at $2M ARR and shut it down. Granola raised $67M at $250M valuation. Otter hit $100M ARR. The context/memory layer for AI is the hottest investment category in 2025–2026. **The desktop developer segment is completely open.** ContextPulse is the only MCP-native, on-device, multi-modal context platform."

**Competitor status notes (investor talking points):**
- Limitless/Rewind: acquired+shut down = validates market, removes threat ([TechCrunch Dec 2025](https://techcrunch.com/2025/12/05/meta-acquires-ai-device-startup-limitless/))
- Screenpipe: 16,700+ GitHub stars, $400 entry, no subscription model = different segment (OSS power users)
- Granola: $250M valuation = category validation; meeting-only = different scope than ContextPulse
- Otter: $100M ARR = category proof; meeting-only audio = not a developer tool competitor

---

## Slide 7: Business Model

**Headline:** "Free to adopt, paid to unlock, subscription to scale"

**Pricing ladder (visual staircase):**

```
FREE                PRO              TEAM             ENTERPRISE
Sight Core          Sight Pro        Memory + Agent   Custom
3 MCP tools        10 MCP tools     $20/seat/month   $50K+/year
---                 $29 one-time     Shared context   SOC 2, SSO
Adoption funnel    100K+ devs       Dev teams        Regulated co.
```

**Revenue mix by 2030:**
- Pro one-time: $232K (15%)
- Team subscription: $637K (41%)
- Enterprise contracts: $693K (44%)
- **Total: $1.56M ARR**

**Unit economics:**
- Gross margin: 94%
- Team LTV: $3,200/team | CAC: $200 → 16x LTV/CAC
- Enterprise LTV: $200K | CAC: $10K → 20x LTV/CAC

---

## Slide 8: Traction & Milestones

**Headline:** "Built, shipped, and running in production — on our own machines"

**Product milestones (timeline):**
- Q4 2025: Core capture daemon shipped
- Q1 2026: 10 MCP tools, 145 tests, OCR, smart storage, Phase 3.0 complete
- Q1 2026: IP portfolio filed (patent, trademark, 13 trade secrets documented)
- Q2 2026: PyPI publish, MCP Registry, Product Hunt launch ← **[We are here]**

**Evidence this works:**
- Running in production daily across 9 active projects
- [Voiceasy voice infrastructure: live on Gumroad, v1.0 complete] — ContextVoice foundation ready
- Ed25519 licensing + Lambda webhook infrastructure already deployed and tested

**What $500K will accomplish:**
- Ship Voice module → quad-modal story
- macOS + Linux → 3x TAM
- 3 enterprise pilots → paid contracts by Q4 2027
- Break-even by Q2 2028 ($285K ARR)

---

## Slide 9: Intellectual Property

**Headline:** "13 trade secrets + provisional patent = defensible moat"

**IP portfolio visual:**

```
PATENT PENDING                    TRADEMARKS (Filed)
"Adaptive Multi-Modal             CONTEXTPULSE (Classes 9, 10, 42)
 Context Delivery with            CONTEXTPULSE SIGHT
 Cross-Modal Learning"            CONTEXTVOICE
$65 provisional → $800            CONTEXTPULSE KEYS
utility patent (April 2027)       CONTEXTPULSE FLOW

TRADE SECRETS (13)                COPYRIGHT
Per-monitor capture pipeline      All source code
Content-adaptive storage          (register before launch)
Differential change detection
Pre-storage redaction
Cross-modal learning engine ← DEEPEST MOAT
```

**Why the learning engine is the moat:**
- Each user's system becomes personalized over 6 months
- Voice corrections → keyboard vocabulary
- Screen validates voice transcription accuracy
- Cannot be replicated without copying both architecture AND user's historical data
- Switching cost grows every week of use

---

## Slide 10: Team

**Headline:** "Shipped. Not planning to ship."

**David Jerard — Founder**
- Built ContextPulse Sight: 145 tests, 10 MCP tools, production-ready
- Built Voiceasy: live dictation app on Gumroad (ContextVoice foundation)
- Runs Claude Code + Gemini CLI agent ecosystem across 9 active ventures
- Deployed AWS Lambda + Gumroad licensing infrastructure (proven production stack)
- 8 active ventures: demonstrates product velocity and full-stack capability
- Colorado LLC formed March 2026; IP filing in progress

**Why the founder-product fit is exceptional:**
- ContextPulse's core user is David Jerard
- Every design decision is validated against real daily usage
- "I built this because I was losing hours every day re-explaining context to my AI agents"

**Hiring plan (seed capital):**
- Month 1: Python contractor (macOS + Linux port)
- Month 6: Developer advocate (community + content)
- Month 12: Second engineer (Voice + Keys modules)
- Month 18: First GTM hire (developer relations / inside sales)

---

## Slide 11: Financials & Fundraising

**Headline:** "Break-even at $285K ARR (Q2 2028). Exit at 15x+ ARR."

**Revenue chart (bar graph, 2026-2030):**
- 2026: $15K | 2027: $77K | 2028: $321K | 2029: $849K | 2030: $1.56M

**Unit economics summary:**
- 94% gross margin
- CAC (blended): <$15 for Pro; $200 for Team
- LTV/CAC: 16x (Team), 20x (Enterprise)
- Break-even: Q2 2028 at $285K ARR (bootstrap path)

**The Ask: $500K Seed Round**

| Use | Amount |
|-----|--------|
| Engineering (2 contractors × 12 months) | $200K |
| IP conversion (patent + TM + copyright) | $30K |
| Marketing & GTM | $80K |
| Infrastructure + SOC 2 prep | $20K |
| Legal & G&A | $30K |
| Buffer + runway | $140K |

**Target post-money:** $3.0M
**Runway:** 18 months to Series A metrics ($750K ARR)

---

## Slide 12: Exit & Vision

**Headline:** "The context layer that makes every AI agent smarter — permanently"

**3-year vision:**
"In 2029, ContextPulse runs on 100,000 developer machines. Every AI agent — Claude, Cursor, Copilot, Gemini — has persistent memory of what its user was doing, saying, typing, and looking at. The system gets more accurate every day. Switching away means starting over with a blank slate."

**Exit opportunities:**
| Buyer | Rationale | Timeline |
|-------|-----------|----------|
| Microsoft | MCP-native Recall upgrade, accessibility for Section 508, Windows-first | 2028–2030 |
| Anthropic | Client-side infrastructure for Claude's long-term memory | 2028–2029 |
| GitHub (Microsoft) | Copilot context layer for 100M developers | 2028–2030 |
| Meta | Desktop piece of ambient AI vision (post-Limitless) | 2029–2030 |
| Notion/Atlassian | Developer context for enterprise collaboration | 2028–2030 |

**Acquisition value at exit:**
- Conservative (Year 4, $849K ARR): 15x = **$12.7M**
- Base (Year 5, $1.56M ARR): 15x = **$23.4M**
- Strategic premium (IP + ecosystem position): **$30M–$50M**

**Exact exit comparables for the slide:**

| Exit | Acquirer | Year | ARR at Deal | Price | Multiple |
|------|----------|------|-------------|-------|---------|
| GitHub | Microsoft | 2018 | ~$250M | $7.5B | ~30x |
| Loom | Atlassian | 2023 | ~$50M | $975M | ~20x |
| Limitless (Rewind) | Meta | 2025 | ~$2M | Undisclosed | Strategic |
| Skiff | Notion | 2024 | Pre-revenue | Undisclosed | Acqui-hire |
| Bun (JS runtime) | Anthropic | 2025 | N/A | Undisclosed | Developer infra |

**ContextPulse exit scenarios (on the slide):**
- Conservative (2029, $849K ARR × 15x): **$12.7M**
- Base (2030, $1.56M ARR × 15x): **$23.4M**
- Strategic premium (MCP ecosystem position + IP portfolio): **$30M–$50M**

**The acquirer case in one sentence per buyer:**
- *Microsoft*: "ContextPulse is Recall for the 90% of developers who can't run Recall, with an MCP API that Recall never had."
- *Anthropic*: "ContextPulse is how Claude finally knows what's on your screen — on-device, private, and already running on 100K developer machines." (Anthropic acquired Bun in Dec 2025 — first ever acquisition; developer infrastructure is their M&A thesis.)
- *Meta*: "Limitless was audio context. ContextPulse is screen + keyboard + pointer. Together they complete the ambient AI picture."

---

## Appendix Slides (Available on Request)

- A1: Detailed financial model (5-year P&L)
- A2: Full competitive feature matrix
- A3: IP portfolio detail (patent claims summary)
- A4: Technical architecture diagram
- A5: Privacy & compliance architecture
- A6: Accessibility market deep-dive
- A7: Customer personas and use cases
- A8: Go-to-market timeline (month-by-month)

---

## Presentation Notes

**Total runtime:** 20 minutes + 10 minutes Q&A

**Demo requirement:** Live demo of Slides 3-4 if possible, otherwise 60-second GIF embedded.

**Key messages to land (3-3-3 rule — 3 things they remember after 3 days):**
1. "AI agents start every session blind — ContextPulse gives them eyes, ears, and memory"
2. "On-device only — the only solution that works in regulated industries and the EU"
3. "Cross-modal learning creates a switching-cost moat that grows every week"

**Pre-empt objections:**
- "Microsoft already built this" → "Requires $800+ new laptop, opt-in (low adoption), no EU, no MCP"
- "Why won't Microsoft/Apple just build this?" → "They might. But we'll have 100K+ installations, an IP portfolio, and user-specific models before they ship"
- "Solo founder risk" → "I've already shipped the hard parts. The moat is the architecture, not the headcount."
