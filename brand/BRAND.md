# ContextPulse Brand Guide

## Name
**ContextPulse**

## Tagline
> Always-on context for AI agents

## Positioning
ContextPulse is invisible infrastructure that gives AI agents persistent awareness of your desktop, your work history, and your project state. It runs in the background, captures what matters, and makes it instantly available to any AI agent via MCP.

## Product Suite

The parent brand is **ContextPulse**. Sub-products use `Context[Noun]` format — human, sensory, no technical jargon.

**ContextPulse Core** is the shared foundation: spine (EventBus, ContextEvent contracts), project-aware routing, licensing, and configuration. It ships free with every product and is not sold separately.

### Products (Senses & Faculties)

| Package | User-Facing Name | One-Liner | Build Phase |
|---------|-----------------|-----------|-------------|
| contextpulse-sight | **ContextSight** | See the screen | Shipping (Phase 3.0) |
| contextpulse-touch | **ContextTouch** | Feel the keyboard + mouse | Planned |
| contextpulse-ear | **ContextEar** | Listen to the outside world (email, Slack, web searches) | Planned |
| contextpulse-memory | **ContextMemory** | Remember across sessions | Planned |
| contextpulse-heart | **ContextHeart** | Know what matters (values, goals, mission weighting) | Planned |
| contextpulse-people | **ContextPeople** | Know who matters (people context, relationship history) | Planned |

### Core (not sold separately)

| Package | Purpose |
|---------|---------|
| contextpulse-core | Shared config, licensing, settings, GUI theme |
| contextpulse-spine | EventBus, ContextEvent contracts, ModalityModule base |
| contextpulse-project | Project-aware routing (detect active project, route context) |

## Target Audience

**Primary:** Developers who use AI coding assistants (Claude Code, Cursor, GitHub Copilot, Gemini CLI) and want their AI to have richer context without manual screenshots or copy-paste.

**Profile:**
- Technical level: intermediate to advanced developers
- Pain: AI assistants start each session blind — no visual context, no memory of past work
- Motivation: productivity, flow state preservation, less context-switching
- Platform: Windows first (Mac later)

## Brand Personality
1. **Invisible** — you forget it's running until you need it
2. **Fast** — sub-second captures, <1% CPU, never in the way
3. **Trustworthy** — privacy-first, blocklists, auto-pause on lock
4. **Precise** — the right context at the right time, not a firehose
5. **Developer-native** — CLI-first, MCP protocol, env vars, no GUI bloat

## Revenue Model

ContextPulse is NOT open source. The GitHub repo is private. Code is distributed via PyPI packages with license verification.

| Product | Free Tier | Paid Tier |
|---------|-----------|-----------|
| **Sight** | Core capture, 3 MCP tools, basic buffer, privacy controls | **Sight Pro ($29 one-time)** — all 10 MCP tools, OCR search, smart storage, activity DB, clipboard, multi-agent stats |
| **Touch** | — | TBD |
| **Ear** | — | TBD |
| **Memory** | — | $29-49 one-time (TBD) |
| **Heart** | — | TBD |
| **People** | — | TBD |

## Logo System

- **Umbrella mark (ContextPulse)**: Pulse wave icon — used on website, social, docs header
- **Sight mark**: Eye + pulse wave — used for Sight-specific contexts (system tray, Sight landing section, PyPI page)
- **Simplified mark (A3)**: Clean eye + pulse at 16-48px — system tray icon, favicon

Logo files: `brand/logo/`
- `umbrella-primary.png` — **Primary umbrella mark** (CP monogram from pulse wave, teal-to-green gradient)
- `umbrella-simplified.png` — **Simplified mark** (minimal pulse wave, for favicon/tray when CP is too detailed)
- `logo-primary-A1.png` — Sight-specific mark (eye + pulse, navy + teal)
- `logo-backup-A3-clean.png` — Sight simplified (small sizes)

## Domain Portfolio

**Platform:**
| Domain | Purpose |
|--------|---------|
| contextpulse.ai | Primary brand |
| contextpulse.dev | Developer-facing, docs |
| contextpulse.io | Credibility / redirect |
| context-pulse.com | .com fallback |

**Sub-Products:**
| Product | Domains |
|---------|---------|
| **Sight** | contextsight.ai, context-sight.com |
| **Touch** | contexttouch.ai, contexttouch.com |
| **Ear** | contextear.ai, contextear.com |
| **Memory** | contextmemory.dev |
| **Heart** | contextheart.ai, contextheart.com |
| **People** | contextpeople.ai, contextpeople.com |

**Registrar:** Cloudflare (david@jerardventures.com)
**Current deployment:** contextpulse.pages.dev (Cloudflare Pages)
