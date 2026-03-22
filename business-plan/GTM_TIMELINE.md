# ContextPulse — Go-To-Market Timeline
**March 2026 – December 2027 | Month-by-Month Execution Plan**
**Owner: David Jerard (solo founder, all tasks) unless otherwise noted**

---

## Phase 1: Developer Beachhead (Months 1–6, March–August 2026)

### Month 1 — Foundation (March 21 – April 20, 2026)

**IP & Legal**
- [ ] File CONTEXTPULSE trademark — Classes 9, 10, 42 ($1,050)
- [ ] File provisional patent — Patent Center ($65)
- [ ] Register copyright on Sight codebase ($65)

**Product**
- [ ] Fix `pip install -e .` editable install
- [ ] Deploy landing page to contextpulse.ai (copy ready in docs/LANDING_PAGE_COPY.md)
- [ ] Create demo GIF: 30 seconds, shows capture → MCP tool call → context delivered to Claude
- [ ] Publish contextpulse-sight to PyPI with proper packaging

**Distribution Setup**
- [ ] Submit to MCP Registry (registry.modelcontextprotocol.io)
- [ ] Create GitHub repository with star-optimized README (demo GIF above the fold)
- [ ] Set up Discord server (invite link in README)

**Goal:** Infrastructure in place. Landing page live. IP filed. PyPI package installable.

---

### Month 2 — Launch (April 20 – May 20, 2026)

**Launch Events (coordinate same week for maximum cross-channel momentum)**
- [ ] **Product Hunt launch** — submit Friday, launch Monday. Target Top 5 Product of the Day.
  - Assets needed: Demo video (60s max), 2 screenshots, 5 Hunter votes lined up
  - Maker comment strategy: respond to every comment within 1 hour on launch day
- [ ] **Hacker News: Show HN** — "Show HN: ContextPulse – always-on screen context for AI agents via MCP"
  - Post same week as PH but different day (Tuesday morning ET for max visibility)
  - Title must be factual, no marketing language
- [ ] **Reddit posts** — r/ClaudeAI, r/cursor (check rules first)
  - Format: "I built a thing that solved my daily pain" — story-first, not ad
  - Link to GitHub, not landing page

**Community**
- [ ] Reach out to 3 MCP server developers for co-announcement (filesystem MCP, GitHub MCP)
- [ ] Post in Claude Discord's #mcp-servers channel
- [ ] Post in Cursor Discord

**Metrics target (end of Month 2):**
- PyPI installs: 200+
- GitHub stars: 200+
- Pro purchases: 20+ ($580)
- Discord members: 50+

---

### Month 3 — Amplification (May 20 – June 20, 2026)

**Content**
- [ ] Blog post #1: "Why I built ContextPulse" — publish on contextpulse.ai/blog
  - Include technical details about per-monitor capture solving multi-monitor illegibility
  - Submit to changelog.com newsletter, Pragmatic Engineer
- [ ] Blog post #2: "The complete guide to MCP servers for AI development in 2026"
  - SEO target: "MCP servers" keyword (high intent, growing search volume)
  - Feature 10-15 other MCP servers; get reciprocal mentions

**Developer Advocate**
- [ ] Identify 5 power users from Discord/GitHub with public presences
- [ ] Offer Pro license + lifetime Team access for honest public review/tutorial
- [ ] Target: 1 YouTube tutorial, 2 Twitter/X threads, 1 newsletter mention

**Product**
- [ ] Begin Memory module SQLite schema design
- [ ] Begin ContextVoice port from Voiceasy (scoping session)

**Metrics target (end of Month 3):**
- PyPI installs: 500+
- GitHub stars: 400+
- Pro purchases: 50 ($1,450)
- Discord members: 150+

---

### Month 4 — Iteration (June 20 – July 20, 2026)

**User Research**
- [ ] Interview 10 Pro users (30-min Calendly calls)
  - Key questions: What would you pay for? What's missing? Where does it break?
  - Document all feedback → feature prioritization
- [ ] Analyze top 3 support questions → add to FAQ on landing page

**Product**
- [ ] Ship top-requested features from user interviews (bug fixes, UX improvements)
- [ ] Memory module: implement core journal SQLite schema (first internal version)
- [ ] ContextVoice: basic voice capture working in dev environment

**Distribution**
- [ ] Reach out to 3 AI developer newsletters for feature/sponsorship
  - Target: The Pragmatic Engineer, TLDR AI, Ben's Bites (10K–500K subscriber lists)
  - Offer free guest post or tool spotlight (no cost initially)
- [ ] LinkedIn article targeting enterprise dev managers: "What your team's AI assistant doesn't know (and how to fix it)"

**Metrics target (end of Month 4):**
- PyPI installs: 1,000+
- Pro purchases: 100 ($2,900)
- Weekly active MCP users: 50+

---

### Month 5 — macOS Expansion (July 20 – August 20, 2026)

**Product (major milestone)**
- [ ] Ship ContextPulse Sight macOS beta
  - mss and pynput are cross-platform — primarily packaging and testing work
  - macOS system tray via rumps or pystray
  - Target: macOS install working for testers
- [ ] Announce macOS beta to waitlist
- [ ] ContextVoice alpha: basic capture + transcription (Whisper) on Windows

**Distribution**
- [ ] macOS launch as "Update" PH post (not full relaunch, but drives traffic)
- [ ] Reach r/MachineLearning, r/productivity, r/MacApps with macOS announcement
- [ ] Partner with 1 macOS-focused developer tool newsletter

**Business Development**
- [ ] Identify 3 potential enterprise pilot targets (AI-first software agencies)
- [ ] Draft enterprise pilot offer: 90-day free trial for 10+ developers, case study in return

**Metrics target (end of Month 5):**
- Total installs (Win + Mac): 2,000+
- Pro purchases (cumulative): 200 ($5,800)
- macOS beta testers: 100+

---

### Month 6 — Phase 1 Review (August 20 – September 20, 2026)

**Metrics Review & Decision Gate**

| Metric | Target | Decision |
|--------|--------|----------|
| PyPI installs | 2,000+ | ✅ Proceed → Phase 2 |
| GitHub stars | 1,000+ | ✅ Proceed |
| Pro purchases | 200+ | ✅ Proceed |
| MRR equivalent | $1,000+ | ✅ Proceed to Team |
| Discord members | 500+ | ✅ Community traction |

If all targets hit: Launch Team tier and begin seed round conversations.
If <50% of targets: Conduct 20 user interviews, pivot positioning or feature set.

**Phase 1 Output:**
- Established presence in MCP ecosystem
- Proof of developer-market fit
- Pipeline of 3-5 enterprise pilot candidates
- macOS live
- Voice module in beta

---

## Phase 2: Prosumer Expansion (Months 7–18, September 2026 – September 2027)

### Month 7 — Team Tier Launch (September 2026)

**Product**
- [ ] Ship ContextPulse Memory (Team tier): persistent cross-session memory via MCP tools
- [ ] Team admin dashboard: seat management, usage analytics, invite links
- [ ] Billing: Stripe subscription integration (migrate from Gumroad for Team+)

**Launch**
- [ ] Email all Pro users: "Memory is here — invite your team"
- [ ] PH relaunch: "ContextPulse Memory — AI agents that never forget"
- [ ] Case study from Enterprise pilot (if secured): publish on blog

**Pricing validation**
- [ ] Test $15 vs $20 vs $25/seat with A/B landing page variants (Cloudflare AB testing)
- [ ] Track trial-to-paid conversion by price point

**Target:** 10 Team subscriptions by end of month ($1,600 MRR)

---

### Month 8-9 — Voice Integration (October–November 2026)

**Product**
- [ ] Ship ContextVoice (public beta): screen + voice context combined
  - Voice capture: always-on VAD, on-device Whisper, speaker diarization
  - Temporal alignment: speech timestamped against screen activity
  - New MCP tools: search_audio_history, get_recent_speech
- [ ] Upgrade existing Pro users to Voice (included in Pro)
- [ ] Team users: shared voice context (configurable)

**Distribution**
- [ ] ContextVoice launch: target AI content creator YouTube communities
  - Demo: "Ask Claude what your colleague said in that meeting 20 minutes ago"
  - Demo: "Voice-correct your last dictation using screen context"
- [ ] Target: r/productivity, r/ADHD (cognitive accessibility angle)
- [ ] Reach out to 3 podcasts covering AI developer tools

**Target:** 500 cumulative Pro, 25 Team subscriptions ($4,000 MRR)

---

### Month 10-11 — Enterprise Pipeline (December 2026 – January 2027)

**Sales**
- [ ] Close 1st enterprise pilot (target: AI-first software agency, 15-30 devs)
- [ ] Begin SOC 2 Type I preparation (self-assessment with Vanta/Drata at ~$8K)
- [ ] VPAT (Voluntary Product Accessibility Template) completed for Sight + Voice
- [ ] GSA Schedule 70 registration initiated

**Content (enterprise-focused)**
- [ ] Case study #1: first pilot organization (with their permission)
- [ ] White paper: "Privacy-first AI context for regulated industries" (targets healthcare, finance)
- [ ] ROI calculator: "Hours saved per developer per week" (embed on landing page)

**Target:** 1 enterprise pilot, 40 Team subscriptions ($6,400 MRR)

---

### Month 12 — Year 1 Review (January 2027)

**Year 1 Actuals vs. Targets**

| Metric | Target | Notes |
|--------|--------|-------|
| Pro users (cumulative) | 1,000 | $29K one-time |
| Team subscriptions | 50 | $8,000 MRR |
| Enterprise pilots | 1–2 | No revenue yet |
| Monthly active MCP users | 200+ | Core engagement metric |
| GitHub stars | 2,000+ | Community health |
| Annualized revenue | $96K+ | Team ARR + Pro run-rate |

**Decision gate:** If $100K+ ARR, begin formal seed round process. If <$50K ARR, reduce scope to Sight + Voice, extend bootstrap runway.

---

### Month 13-15 — Keys Module & Enterprise Conversion (February–April 2027)

**Product**
- [ ] Ship ContextPulse Keys (beta): keyboard capture with fatigue detection, shortcut tracking
- [ ] New MCP tools: get_typing_patterns, get_fatigue_estimate, get_recent_keystrokes
- [ ] Enterprise: convert first pilot to paid contract ($50K ACV)
- [ ] Accessibility features: typing fatigue detection enabled by default for accessibility users

**Distribution**
- [ ] Keys launch: target developer health communities (RSI, ergonomics)
- [ ] Accessibility angle: ADHD/executive function productivity communities
- [ ] Speaking: Apply to MCP/AI developer conference (Anthropic developer day, AI Engineer Summit)

**Target:** 1st enterprise paid contract, 80 Team subscriptions ($12,800 MRR)

---

### Month 16-18 — Series A Preparation (May–July 2027)

**Financials**
- [ ] Achieve $150K ARR milestone (trigger for seed conversations)
- [ ] Clean up metrics dashboard: MRR, NRR, CAC, LTV, churn — all visible to investors

**Product**
- [ ] Flow module (pointer capture): internal alpha
- [ ] Linux beta (container/server developer market)
- [ ] Cross-modal learning: architecture design completed

**Fundraising prep (if pursuing seed)**
- [ ] Data room: financials, IP portfolio, user metrics, competitive analysis
- [ ] Investor list: 20 target seed funds (a16z Developer Tools, Unusual Ventures, Heavybit, OpenView)
- [ ] Warm intros: via MCP ecosystem connections, Anthropic developer relations

---

## Channel ROI Estimates

| Channel | Cost | Users Acquired (Year 1) | CAC | Quality Score |
|---------|------|------------------------|-----|---------------|
| PyPI/GitHub (organic) | $0 | 800 | $0 | ★★★★★ |
| MCP Registry | $0 | 400 | $0 | ★★★★★ |
| Product Hunt | $500 (video) | 300 | $1.67 | ★★★★☆ |
| Hacker News | $0 | 200 | $0 | ★★★★★ |
| Reddit | $0 | 150 | $0 | ★★★☆☆ |
| Developer advocate | $5,000 (12mo) | 200 | $25 | ★★★★☆ |
| Blog (SEO) | $2,000 (time) | 100 | $20 | ★★★★☆ |
| Newsletter sponsorship | $2,000 | 75 | $27 | ★★★☆☆ |

**Total Year 1:** ~$9,500 marketing spend for ~2,225 users → blended CAC ~$4.27

---

## Key Launch Dependencies

```
PyPI publish
    └── MCP Registry submission (requires PyPI URL)
        └── Product Hunt launch (requires installable package)
            └── Show HN (same week as PH for momentum)
                └── Reddit posts (same week)

macOS port
    └── macOS launch announcement
        └── Expanded prosumer TAM

Memory module
    └── Team tier pricing launch
        └── Stripe subscription billing
            └── Enterprise pilot conversations

Voice module (Voiceasy port)
    └── Cross-modal story complete
        └── Prosumer market expansion
            └── Accessibility narrative
```

---

## Competitive Response Playbook

**If Screenpipe accelerates (currently 16,700+ GitHub stars; backed by Founders Inc. Oct 2024; funding undisclosed):**
- They are pre-PMF by their own admission despite traction — execution gap is the opportunity
- Emphasize: per-monitor capture, <1% CPU vs. Screenpipe's 5–15% CPU, cross-modal roadmap, accessibility IP
- Emphasize: subscription model ($20/seat/mo) vs. Screenpipe's one-time $400 = ContextPulse wins on enterprise and recurring revenue
- Accelerate: Keys + Flow modules (keyboard/pointer) where Screenpipe has no roadmap
- MCP depth: 10 native tools vs. Screenpipe's partial 3-tool add-on
- Privacy: pre-storage credential redaction (Screenpipe stores raw screen text including API keys and passwords)

**If Microsoft Recall adds MCP support:**
- Emphasize: no Copilot+ PC hardware required (works on any machine), cross-platform (macOS/Linux)
- Emphasize: pre-storage redaction (Recall has no credential scrubbing)
- Emphasize: cross-modal roadmap (Recall is screen-only; ContextPulse adds voice/keyboard/pointer)
- Emphasize: open ecosystem (MCP-native = works with Claude, Cursor, Gemini, any agent)

**If a well-funded competitor copies ContextPulse:**
- Execute faster on cross-modal learning engine (18-month lead)
- Lean into accessibility market (where they won't go)
- OSS the Sight core (make it impossible to compete on commoditized features; monetize the learning layer)
