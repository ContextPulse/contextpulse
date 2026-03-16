# ContextPulse — Ecosystem Roadmap

*Ported from SynapseAI concept + market research, reframed under ContextPulse brand*
*Date: 2026-03-16*

---

## Vision

**ContextPulse is the context layer for AI agents.** Screen context is product #1. Persistent memory is product #2. Agent coordination is product #3. Each package is independently useful but compounds when combined.

**One-liner:** "Always-on context for AI agents — visual, memory, and coordination."

---

## Product Suite & Revenue Map

| Package | What It Does | Target | Revenue Model | Priority |
|---------|-------------|--------|---------------|----------|
| **Sight** | Always-on screen capture + MCP server | Developers using AI coding assistants | Free / open source (acquisition funnel) | NOW — nearly shippable |
| **Memory** | Cross-session persistent memory for agents | Power users with 2+ AI tools | $29-49 one-time starter kit (Gumroad) | NEXT — port SynapseAI journal pattern |
| **Agent** | Multi-agent coordination + shared context | Solo founders / small teams | $49-99/mo cloud platform | LATER — requires Memory first |
| **Project** | Auto-generated project context | Developers | Bundled with Memory or free | LATER — nice-to-have |
| **Cloud** | Hosted infrastructure for all packages | Teams, enterprises | $49-99/mo per seat | FUTURE — requires proven self-hosted demand |

**Key insight from market research:** The framework itself has LOW willingness-to-pay. Value is in integrations, hosted infrastructure, and pre-built templates. Sight should be free to build community. Memory is the first revenue product.

---

## Phase Plan

### Phase 1: Sight (NOW — March 2026)
**Status:** Phase 1.5 in progress, 44/44 UAT passing

- [ ] Manual user testing across multiple Claude Code sessions
- [ ] Push to GitHub (public repo)
- [ ] Publish to PyPI
- [ ] Add to Windows Startup for auto-launch
- [ ] Landing page on contextpulse.ai (even a single page)
- [ ] Product Hunt launch for Sight alone

**Revenue:** $0 — this is the funnel. Gets ContextPulse known in the MCP/developer community.

### Phase 2: Memory (April-May 2026)
**The first revenue product.** Port SynapseAI's journal pattern + shared knowledge layer.

MVP scope (smallest thing that ships):
- Shared knowledge files (structured markdown + JSON registries)
- Session start/end hooks that read prior context and log observations
- Append-only journal with deterministic routing script
- MCP tools for memory read/write/search
- Works standalone (doesn't require Sight)

Full scope (from SynapseAI concept):
- Resolution caches (name/ID/system mappings)
- Activity log (what each agent did and when)
- Channel priorities
- Customer/project context caching
- SQLite upgrade path when flat files get expensive

**Revenue:** Starter kit on Gumroad ($29-49 one-time) — templates + framework + setup guide.

### Phase 3: Agent Coordination (June-July 2026)
**Requires Memory.** Multi-agent orchestration.

- Session protocol (how agents announce themselves, share state)
- Scheduled automation (morning briefings, maintenance, CRM logging)
- Model routing (right model for right job)
- Agent templates (YAML with parameterized prompts)

**Revenue:** Subscription ($49-99/mo) for managed orchestration + premium templates.

### Phase 4: Cloud Platform (Q3-Q4 2026)
**Only if self-hosted demand validates.**

- Hosted memory infrastructure
- Web dashboard for managing agents
- Team features (shared memory across org)
- SSO, compliance, enterprise pricing

**Revenue:** $49-99/mo per seat (individuals), custom enterprise pricing.

---

## Competitive Position

From SynapseAI market research — **no product combines all four:**

1. Shared memory across multiple AI agents
2. Agents that genuinely learn from outcomes
3. Non-technical multi-agent setup
4. MCP-native agent orchestration

**ContextPulse's unique angle:** We add a fifth — **visual context**. No competitor captures what's on screen continuously and feeds it to agents. Screenpipe (16K stars) is the closest but it's heavyweight general-purpose digital memory. ContextPulse is lightweight, developer-focused, and packages visual + memory + coordination as composable MCP tools.

### Closest competitors by package:

| ContextPulse Package | Closest Competitor | Our Advantage |
|---------------------|-------------------|---------------|
| Sight | Screenpipe, MCP screenshot servers | Lightweight (<20MB RAM), privacy-first, MCP-native |
| Memory | Claude MEMORY.md, ChatGPT memory | Cross-agent (not per-chat), outcome-based learning |
| Agent | Lindy.ai, CrewAI | MCP-native, not walled garden, non-technical setup |
| Cloud | Dust.tt, Notion Agents | All-in-one context (visual + memory + coordination) |

---

## The Dogfood Proof

David runs this architecture daily across 9 ventures:
- 50+ custom skills
- Shared knowledge layer with cross-agent memory (MEMORY.md, daily journals)
- Scheduled automation via OpenClaw (heartbeats, cron, morning briefings)
- Session learning that routes observations to permanent registries
- ContextPulse Sight already running as MCP server for Claude Code
- Battle-tested on Windows (the hard platform)

The product is the system, extracted and made installable.

---

## Distribution Strategy

| Channel | Product | Timing |
|---------|---------|--------|
| Product Hunt | Sight (free launch) | As soon as Phase 1.5 is complete |
| PyPI | Sight package | Same time as PH launch |
| GitHub | All packages (open source) | Ongoing |
| Gumroad | Memory starter kit ($29-49) | Phase 2 launch |
| contextpulse.ai | Landing page + docs | Phase 1.5 |
| Reddit (r/ClaudeAI, r/ChatGPT, r/artificial) | Sight launch + Memory launch | Staggered |
| Hacker News | Memory launch (technical audience) | Phase 2 |
| LemonSqueezy or Stripe | Cloud platform | Phase 4 |
