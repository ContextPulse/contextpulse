# ContextPulse — 5-Year Financial Model
**March 2026 | Jerard Ventures LLC | CONFIDENTIAL**

---

## Key Assumptions

### Market Assumptions
- AI developer tools (code tools) market: $4.86B (2023) → ~$26B (2030), 27.1% CAGR — Source: [Grand View Research, AI Code Tools Market Report](https://www.grandviewresearch.com/industry-analysis/ai-code-tools-market-report)
- AI productivity tools (broader TAM): $8.8B–$15B (2024), 26–28% CAGR — Source: [Grand View Research, AI Productivity Tools](https://www.grandviewresearch.com/industry-analysis/ai-productivity-tools-market-report)
- Assistive technology market: $26.8B–$33.3B globally (2024), ~8.9% CAGR, projected $65.2B by 2034 — Source: [IMARC Group](https://www.imarcgroup.com/assistive-technology-market); [Custom Market Insights](https://www.custommarketinsights.com/report/assistive-technology-market/)
- MCP ecosystem: 5,500+ official servers on PulseMCP registry (Oct 2025); 16,000+ total across all sources; 97M monthly SDK downloads (Python + TypeScript, Dec 2025); 407% server growth from initial batch; ~90% enterprise MCP adoption forecast by end-2025 — Source: [MCP 1-year anniversary blog](http://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/); [MCP Manager adoption stats](https://mcpmanager.ai/blog/mcp-adoption-statistics/)
- Total professional developers: 27M globally — Source: Evans Data Corporation Global Developer Population, 2025
- AI coding assistant adoption: 62% of developers today, 80%+ by 2028 — Source: GitHub Octoverse 2024; Stack Overflow Developer Survey 2024
- ContextPulse SOM: Target 0.005%–0.05% of SAM (very conservative)

### Pricing Assumptions
- **Sight Pro:** $29 one-time (consistent with Voiceasy positioning, Gumroad-compatible)
- **Team tier launches:** Q1 2027 at $20/seat/month, increasing to $25 by 2029
- **Enterprise tier launches:** Q3 2028 at $50K/year minimum
- **Pro → Team conversion:** 10% of Pro cohort converts within 12 months
- **Team average seat size:** 8 seats (small dev team)
- **Enterprise average contract:** $50K Year 1, growing to $75K by 2030
- **Annual churn (Team):** 15% annually (target NRR: 115% via seat expansion)
- **Enterprise churn:** 5% annually

### Cost Assumptions
- **COGS:** AWS Lambda ($0.0000002/invocation), S3 (near-zero for on-device product), license delivery (~$0.10/user/month). Gross margin ~92% in Year 1, declining to 88% as Team/Enterprise support scales.
- **Engineering:** $60/hr blended contractor rate. Solo founder Year 1 draws minimal salary.
- **Marketing:** Primarily organic/community in Year 1-2; paid channels Year 3+
- **G&A:** IP costs (patents, trademarks, copyright), legal, accounting
- **No VC fees:** Bootstrap path modeled. See "Funded Scenario" section for seed alternative.

---

## Bootstrap Scenario (Primary Path)

### Revenue Waterfall

#### Pro Tier (One-Time Revenue)

| Year | New Pro Users | Price | Revenue | Notes |
|------|--------------|-------|---------|-------|
| 2026 | 500 | $29 | $14,500 | Post-PH launch; organic/MCP registry |
| 2027 | 2,000 | $29 | $58,000 | Includes macOS users (Q3 2026) |
| 2028 | 4,000 | $29 | $116,000 | Voice module drives upsell |
| 2029 | 6,000 | $29 | $174,000 | Keys module adds TAM |
| 2030 | 8,000 | $29 | $232,000 | Full quad-modal platform |

*Note: Cumulative installed base matters for Team upsell; one-time revenue modeled as annual cohort.*

#### Team Tier (Subscription ARR)

| Year | New Teams | Avg Seats | Seat Price | New MRR | Churned MRR | Net ARR |
|------|-----------|-----------|-----------|---------|-------------|---------|
| 2026 | 0 | — | — | — | — | $0 |
| 2027 | 10 | 8 seats | $20/mo | $1,600 | $0 | **$19,200** |
| 2028 | 50 | 9 seats | $20/mo | $9,000 | ($2,880) | **$105,120** |
| 2029 | 120 | 10 seats | $22/mo | $26,400 | ($15,768) | **$339,720** |
| 2030 | 200 | 11 seats | $25/mo | $55,000 | ($50,958) | **$636,762** |

*NRR modeled at 115% (seat expansion offsets churn)*

#### Enterprise Tier (Annual Contracts)

| Year | New Contracts | Avg ACV | New ARR | Churned | Net ARR |
|------|--------------|---------|---------|---------|---------|
| 2026 | 0 | — | — | — | $0 |
| 2027 | 0 | — | — | — | $0 |
| 2028 | 2 | $50,000 | $100,000 | $0 | **$100,000** |
| 2029 | 4 | $60,000 | $240,000 | ($5,000) | **$335,000** |
| 2030 | 5 | $75,000 | $375,000 | ($16,750) | **$693,250** |

#### Total Revenue Summary

| Year | Pro Revenue | Team ARR | Enterprise ARR | **Total Revenue** | YoY Growth |
|------|------------|----------|---------------|-------------------|------------|
| **2026** | $14,500 | $0 | $0 | **$14,500** | — |
| **2027** | $58,000 | $19,200 | $0 | **$77,200** | +432% |
| **2028** | $116,000 | $105,120 | $100,000 | **$321,120** | +316% |
| **2029** | $174,000 | $339,720 | $335,000 | **$848,720** | +164% |
| **2030** | $232,000 | $636,762 | $693,250 | **$1,562,012** | +84% |

*Conservative model. Upside scenario (accessibility contract, enterprise expansion) = 2x these figures.*

---

### Cost Structure

#### COGS (Cost of Revenue)

| Year | Infrastructure | Support | License Delivery | Total COGS | Gross Margin |
|------|---------------|---------|-----------------|-----------|-------------|
| 2026 | $500 | $0 | $200 | $700 | **95%** |
| 2027 | $2,000 | $1,000 | $800 | $3,800 | **95%** |
| 2028 | $8,000 | $5,000 | $2,000 | $15,000 | **95%** |
| 2029 | $20,000 | $20,000 | $5,000 | $45,000 | **95%** |
| 2030 | $40,000 | $50,000 | $10,000 | $100,000 | **94%** |

*Note: On-device architecture means minimal COGS. Infrastructure costs are primarily Lambda invocations for license verification and email delivery.*

#### Operating Expenses

**Research & Development (Engineering)**

| Year | Founder Salary | Contractors | Total R&D |
|------|---------------|-------------|----------|
| 2026 | $0 (sweat equity) | $30,000 | $30,000 |
| 2027 | $40,000 | $60,000 | $100,000 |
| 2028 | $80,000 | $100,000 | $180,000 |
| 2029 | $120,000 | $150,000 | $270,000 |
| 2030 | $150,000 | $200,000 | $350,000 |

*Year 2026: Founder draws minimal salary; all revenue reinvested. Year 2027+: Modest founder salary as revenue grows.*

**Sales & Marketing**

| Year | Content/Community | Developer Advocate | Paid Channels | Total S&M |
|------|------------------|-------------------|--------------|----------|
| 2026 | $3,000 | $0 | $2,000 | $5,000 |
| 2027 | $8,000 | $10,000 | $5,000 | $23,000 |
| 2028 | $15,000 | $20,000 | $20,000 | $55,000 |
| 2029 | $25,000 | $40,000 | $50,000 | $115,000 |
| 2030 | $40,000 | $80,000 | $80,000 | $200,000 |

**General & Administrative**

| Year | IP (Patents/TM) | Legal/Accounting | Tools/SaaS | Total G&A |
|------|----------------|-----------------|-----------|----------|
| 2026 | $2,000 | $2,000 | $1,000 | $5,000 |
| 2027 | $5,000 | $5,000 | $3,000 | $13,000 |
| 2028 | $8,000 | $10,000 | $5,000 | $23,000 |
| 2029 | $10,000 | $15,000 | $8,000 | $33,000 |
| 2030 | $12,000 | $20,000 | $10,000 | $42,000 |

*2026 IP budget: $1,180 for trademark filing ($1,050) + provisional patent ($65) + copyright ($65). Year 2027: Patent conversion ($800) + continuation strategy + additional TM filings.*

#### Full P&L

| | **2026** | **2027** | **2028** | **2029** | **2030** |
|---|---|---|---|---|---|
| Revenue | $14,500 | $77,200 | $321,120 | $848,720 | $1,562,012 |
| COGS | ($700) | ($3,800) | ($15,000) | ($45,000) | ($100,000) |
| **Gross Profit** | **$13,800** | **$73,400** | **$306,120** | **$803,720** | **$1,462,012** |
| **Gross Margin** | **95%** | **95%** | **95%** | **95%** | **94%** |
| R&D | ($30,000) | ($100,000) | ($180,000) | ($270,000) | ($350,000) |
| S&M | ($5,000) | ($23,000) | ($55,000) | ($115,000) | ($200,000) |
| G&A | ($5,000) | ($13,000) | ($23,000) | ($33,000) | ($42,000) |
| **Total OpEx** | **($40,000)** | **($136,000)** | **($258,000)** | **($418,000)** | **($592,000)** |
| **EBITDA** | **($26,200)** | **($62,600)** | **$48,120** | **$385,720** | **$870,012** |
| **EBITDA Margin** | (181%) | (81%) | 15% | 45% | 56% |
| Headcount (FTE equiv.) | 1 | 2 | 4 | 6 | 9 |

**Break-Even:** Q2 2028, approximately $285K ARR.

---

## 2026 Monthly Cash Flow (Bootstrap Viability)

*Critical for a solo bootstrapped founder: shows when cash runs out and how much self-funding is required.*

### Revenue Assumptions (Monthly, 2026)
Pro sales are heavily front-loaded around the PH + HN launch in Month 2 (April), then taper to organic baseline.

| Month | Event | Pro Sales | Revenue | Operating Expenses | Net Cash Flow | Cumulative Cash |
|-------|-------|----------|---------|-------------------|---------------|-----------------|
| Mar (M1) | Pre-launch: IP filings, PyPI prep | 0 | $0 | ($4,500) | ($4,500) | ($4,500) |
| Apr (M2) | **PH + HN launch spike** | 120 | $3,480 | ($5,000) | ($1,520) | ($6,020) |
| May (M3) | Post-launch organic + blog | 80 | $2,320 | ($3,500) | ($1,180) | ($7,200) |
| Jun (M4) | User interviews + macOS prep | 60 | $1,740 | ($3,500) | ($1,760) | ($8,960) |
| Jul (M5) | macOS beta launch (secondary spike) | 75 | $2,175 | ($3,500) | ($1,325) | ($10,285) |
| Aug (M6) | Phase 1 review / stabilization | 40 | $1,160 | ($3,500) | ($2,340) | ($12,625) |
| Sep (M7) | Team tier launch (no MRR in first month) | 35 | $1,015 | ($4,000) | ($2,985) | ($15,610) |
| Oct (M8) | Voice beta; first Team MRR ($400) | 25 | $1,125 | ($4,000) | ($2,875) | ($18,485) |
| Nov (M9) | Voice launch amplification | 25 | $725 + $800 MRR | $1,525 | ($3,500) | ($21,985) |
| Dec (M10) | End-of-year developer tool buying | 20 | $580 + $1,200 MRR | $1,780 | ($3,000) | ($24,985) |
| **Total 2026** | | **~500** | **~$14,500** | **($40,000)** | **($25,500)** | |

*MRR figures in Oct–Dec represent Team tier launch (Month 7 onward), added to one-time Pro revenue.*

### Bootstrap Runway Note

**Required founder self-funding (2026):** ~$25,000–$28,000 (absorbed via Voiceasy revenue, consulting, or founder savings).

- Voiceasy Gumroad revenue (~$3K–$6K/yr at current trajectory) offsets a portion
- The founder draws $0 salary in 2026; all contractor spending ($30K) is optional/deferrable
- **Conservative path:** Cut contractors entirely → 2026 cash burn drops to ~$15K (IP + marketing only)
- **Hard floor:** IP filings ($1,180) + landing page + PyPI are non-negotiable ~$3K cash outlay

**Cash-out risk:** Near-zero if founder defers contractor work. The product is already shippable without additional engineering spend. Contractors accelerate Voice + macOS — they are not on the critical path for the Sight Pro launch.

---

## Funded Scenario (Seed Round — $500K)

### Use of Funds

| Category | Amount | Purpose |
|---------|--------|---------|
| Engineering | $200,000 | 2 contract developers × 12 months ($83/hr blended) |
| IP | $30,000 | Provisional→utility patent ($800), trademark strategy, copyright |
| Marketing & GTM | $80,000 | Developer advocate (12 mo), PH campaign, content |
| Infrastructure | $20,000 | AWS, tooling, monitoring, SOC 2 Type I prep |
| Legal & G&A | $30,000 | Entity costs, contracts, accounting |
| Buffer / Runway | $140,000 | 6-month founder runway + contingency |
| **Total** | **$500,000** | **18-month runway to Series A metrics** |

### Funded Scenario Revenue (Accelerated)

With seed capital, hire faster → ship Voice and Keys 6 months earlier → 2x user acquisition rate.

| Year | Conservative (Bootstrap) | Accelerated (Funded) |
|------|--------------------------|---------------------|
| 2026 | $14,500 | $25,000 |
| 2027 | $77,200 | $180,000 |
| 2028 | $321,120 | $750,000 |
| 2029 | $848,720 | $1,800,000 |
| 2030 | $1,562,012 | $3,200,000 |

**Series A trigger:** $500K–$1M ARR, reached Q1-Q2 2028 in funded scenario.

### Seed Round Valuation Framework

- Pre-revenue stage: $2M–$5M pre-money (typical for developer tools with working product + IP)
- Defensible at $4M pre-money given: working product, 145 tests, provisional patent, trademark, Voiceasy infrastructure already built
- Target dilution: 15–20% at seed = $500K raise on $2.5M pre / $3M post valuation

---

## Unit Economics Deep Dive

### Pro Tier

| Metric | Value | Notes |
|--------|-------|-------|
| Price | $29 one-time | Gumroad + Stripe |
| CAC | $8–15 | Organic (community, MCP registry, PH) |
| LTV | $29 + upsell value | One-time purchase |
| Pro → Team upsell | 10% within 12 months | = $29 + ($20/mo × 12 = $240) = $269 combined |
| Gross margin | 95% | ~$0.50 infrastructure per user |
| Payback period | Immediate | One-time payment |

### Team Tier

| Metric | Value | Notes |
|--------|-------|-------|
| Price | $20/seat/month | Team avg = 8 seats = $160/team/month |
| CAC | $150–$300 per team | Inside sales + trial friction |
| LTV | $160/mo × 20 months avg = $3,200/team | Based on 15% annual churn |
| Gross margin | 93% | Light support cost per team |
| Payback period | 1–2 months | |
| NRR target | 115% | Seat expansion offsets churn |

### Enterprise Tier

| Metric | Value | Notes |
|--------|-------|-------|
| ACV | $50,000–$100,000 | Annual contract |
| CAC | $5,000–$15,000 | Sales cycle 3–6 months |
| LTV | $150K–$300K | 5% churn, 3-year avg |
| Gross margin | 85% | SOC 2 audit amortized |
| Payback period | 1–3 months | |
| NRR target | 125% | Dept-by-dept expansion |

---

## Funding & Valuation Milestones

| Milestone | ARR | Valuation Range | Event |
|-----------|-----|-----------------|-------|
| Today | $0 | $1–3M | Working MVP, IP assets, provisional patent |
| PH Launch | $15K | $2–4M | First 500 users, MCP registry listed |
| Voice Shipped | $80K | $3–6M | Cross-modal story complete |
| Team Tier Launch | $200K | $5–10M | Recurring revenue established |
| Break-even | $300K | $8–15M | Profitable unit economics |
| Series A | $750K | $15–30M | Enterprise traction + macOS |
| Acquisition | $2–5M | $20–75M | 10–15x ARR multiple |

---

## CAC Stress Test: Are Our Assumptions Realistic?

### Benchmarks vs. ContextPulse Assumptions

| Source | PLG SaaS CAC | Sales-Led SaaS CAC | Notes |
|--------|-------------|-------------------|-------|
| OpenView PLG Report 2025 | **~$205 average** | $702 average | PLG = product-led growth, free tier drives acquisition |
| HockeyStack Industry Data | $150–$300 (dev tools) | $400–$800 | Developer tools segment specifically |
| High Alpha SaaS Benchmarks | $100–$250 (PLG, <$10K ACV) | $1,500–$5,000 (enterprise) | ACV-matched comparison |
| **ContextPulse (Pro tier)** | **$8–15 (assumed)** | — | Organic: MCP registry, PH, community |
| **ContextPulse (Team tier)** | **$150–$300 (assumed)** | — | Trial + inside sales |

**Assessment:** Our Pro CAC of $8–15 is aggressive but defensible for an MCP registry / Product Hunt launch driven by community. PLG tools with strong product-market fit and developer community traction (e.g., early Raycast, early Linear) have demonstrated sub-$20 organic CAC. This requires: (1) the MCP registry listing converts meaningfully, (2) PH/HN launch generates sustained organic installs, and (3) the free tier has genuine utility that drives word-of-mouth. All three are achievable.

Our Team CAC of $150–$300 is **conservative relative to the $205 PLG average** — meaning it's an honest assumption, not an optimistic one. The benchmark assumes free-to-paid conversion friction; our Team tier requires deliberate intent-to-buy.

### CAC Payback Period Analysis

| Tier | CAC | ACV | Monthly Revenue/User | Payback |
|------|-----|-----|---------------------|---------|
| Pro (individual) | $8–15 | $29 one-time | $29 (immediate) | **Immediate** |
| Team (8 seats × $20) | $150–300/team | $1,920/year | $160/month | **1–2 months** |
| Enterprise | $5,000–$15,000 | $50,000–$100,000 | $4,167–$8,333/month | **1–3 months** |

VC benchmark for healthy PLG: payback under 12 months. Our model shows 1–3 months across all tiers — well within healthy range. The key risk: **if Team CAC rises to $500+ due to slow word-of-mouth or high trial friction**, payback extends to 3–4 months, which is still within the 12-month healthy threshold but requires monitoring.

### What Could Break the Model

**Bull case triggers (upside):**
- MCP registry organic discovery exceeds 2,000 installs in year 1 → Pro CAC drops to $3–5
- A Hacker News "Show HN" post goes top-10 → 500+ installs in 48 hours → CAC near $0 for that cohort
- An enterprise picks up a $100K contract in year 2 → Team revenue timeline accelerates by 6 months

**Bear case risks:**
1. **Low MCP registry conversion:** If the registry drives installs but not $29 purchases, Pro revenue lags. Mitigation: free tier must be genuinely useful to build habit before the paid gate.
2. **Team tier requires outbound sales:** If inbound word-of-mouth is insufficient and Team requires SDR effort, CAC could reach $600–$1,000. At $600 CAC and $160/month revenue, payback extends to ~4 months — still viable but requires earlier investment in sales infrastructure.
3. **Screenpipe free tier undercuts paid adoption:** Screenpipe's $0 OSS core captures the "won't pay" segment. ContextPulse's $29 Pro must demonstrate clear superiority in the developer workflow to justify the conversion. Pre-storage redaction and the MCP tool count (10 vs 3) are the key differentiators.
4. **Churn higher than 15% annually:** At 25% annual Team churn, net ARR growth in 2029 falls from $339K to $267K — still positive, but extends break-even to Q3 2028. Monitor 90-day activation rate as a leading churn indicator.

### Comparable Company Data Points

- **Raycast** (developer launcher): Bootstrapped to $0 price point, monetized at $8/month for AI tier. Demonstrated developers adopt free tools and convert when AI features are gated. ContextPulse mirrors this structure.
- **Warp Terminal**: Raised $73M+ at $200M valuation with free tier; team/enterprise monetization. Similar developer-first, PLG, free-to-enterprise funnel.
- **Linear** (project management): $35M ARR, PLG-driven, $8/user/month, developer-first. CAC confirmed sub-$100 in early years via community word-of-mouth.

**Implication:** Developer tools with strong community narratives and genuine free tiers can achieve PLG CAC below the $205 benchmark. ContextPulse's MCP-native positioning (a built-in discovery channel with 97M monthly SDK downloads) is a structural advantage that Raycast or Warp did not have.

---

## Sensitivity Analysis

### Revenue Sensitivity to Pro User Acquisition (Year 3, 2028)

| Pro Users (2028) | Pro Revenue | Total Revenue (w/ Team/Enterprise) |
|-----------------|-------------|-------------------------------------|
| 1,000 (bear) | $29,000 | $175,000 |
| 4,000 (base) | $116,000 | $321,120 |
| 8,000 (bull) | $232,000 | $587,000 |

### Break-Even Sensitivity

| Founder Salary | Break-Even ARR | Break-Even Date |
|---------------|----------------|-----------------|
| $0 (equity only) | $180K | Q4 2027 |
| $80K/year (base) | $285K | Q2 2028 |
| $150K/year (market) | $420K | Q1 2029 |

---

## Exit Valuation Model

### Acquisition at Year 4 (2029) — Base Case

| Metric | Value |
|--------|-------|
| TTM Revenue | $848,720 |
| ARR (Team + Enterprise) | $674,720 |
| Revenue multiple (strategic) | 15x |
| **Acquisition value** | **$10.1M** |

### Acquisition at Year 5 (2030) — Bull Case

| Metric | Value |
|--------|-------|
| TTM Revenue | $1,562,012 |
| ARR (Team + Enterprise) | $1,330,012 |
| Revenue multiple (strategic, with IP + accessibility) | 15–25x |
| **Acquisition value** | **$20M–$33M** |

### Strategic Premium Scenario

If ContextPulse becomes the de facto MCP context layer (5,000+ monthly active MCP server users), strategic buyers pay for ecosystem position:
- Microsoft: 25x ARR for MCP-native developer tool lock-in = $33M at $1.3M ARR
- Anthropic: 20x ARR for client-side context infrastructure = $26M at $1.3M ARR
- Meta: Strategic fit with wearables/ambient AI; may pay above-multiple for team + IP

**Limitless precedent:** Meta acquired Limitless at $2M ARR (December 2025) for an undisclosed price. Limitless had raised $33M+ at a peak valuation of ~$350M. The exit may have been flat-to-down on valuation — confirming this is a strategic/talent acquisition, not a revenue multiple story. ContextPulse's differentiated IP portfolio, MCP ecosystem position, and higher-margin software-only model (no hardware) makes it a more compelling acquisition target than Limitless was.

---

## Comparable Transaction Benchmarks

Real transactions anchor ContextPulse's exit valuation with buyers and investors.

| Company | Category | Acquirer | Date | ARR at Deal | Deal Value | Multiple |
|---------|----------|----------|------|-------------|------------|----------|
| **Loom** | Dev workflow video | Atlassian | Oct 2023 | ~$50M | $975M | ~20x ARR |
| **Figma** (blocked) | Design tool | Adobe | Sep 2022 | ~$400M | $20B | ~50x ARR |
| **GitHub** | Dev platform | Microsoft | Jun 2018 | ~$250M | $7.5B | ~30x ARR |
| **Granola AI** | Meeting notes | — (Series B) | May 2025 | Undisclosed | $250M valuation | Premium |
| **Mem0** | AI memory layer | — (Series A) | Oct 2025 | Growing rapidly | Undisclosed | Infrastructure premium |

**Key takeaway:** Developer-category tools trade at 20–50x ARR in strategic acquisitions. ContextPulse's 15x ARR base case is conservative. If MCP ecosystem position creates a winner-take-all dynamic (as GitHub did for version control), multiples above 25x are achievable.

---

*All projections are forward-looking estimates. See BUSINESS_PLAN.md Section 15 for risk factors.*
