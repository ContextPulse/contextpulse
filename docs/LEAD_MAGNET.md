# Lead Magnet Analysis — ContextPulse

## Date: 2026-03-21

## Context
- **Product:** ContextPulse Sight (free, open source screen capture MCP server)
- **ICP:** Developers using AI coding assistants daily (Claude Code, Cursor, Gemini CLI)
- **Awareness level:** Problem-Aware to Solution-Aware (they know AI context is painful, may not know MCP solutions exist)
- **Email infrastructure:** Gumroad (already set up for Voiceasy), Lambda + SES (already built)
- **Goal:** Build email list for Memory product launch ($29-49)

## 5 Concepts

### 1. Calculator: "AI Context Cost Calculator"
User inputs: how many AI sessions/day, average screenshots per session, seconds per screenshot workflow. Output: hours/week lost to manual context, projected time saved with ContextPulse, dollar value at their hourly rate.

### 2. Audit: "Is Your AI Agent Setup Missing Context?"
10 questions about their current AI workflow: "Do you screenshot for your AI?", "Do your AI tools share context?", "Can your AI see what app you're using?", etc. Scored 0-100 with tier (Blind, Basic, Context-Aware, Full Stack) and specific recommendations for each gap.

### 3. Checklist: "The MCP Context Stack Setup Guide"
Step-by-step guide: install ContextPulse Sight, configure MCP for Claude Code/Cursor/Gemini, set up privacy blocklist, configure auto-capture intervals, add hotkeys, verify OCR is working. Includes copy-paste commands and env var examples.

### 4. Cheat Sheet: "MCP Tools Quick Reference"
One-page reference card: all 7 ContextPulse MCP tools with syntax, parameters, and example use cases. Plus the top 10 MCP servers every developer should know. Printable/pinnable.

### 5. Quiz: "What's Your AI Context Maturity Level?"
5 questions mapping to maturity stages: Level 1 (Manual — Snip Tool), Level 2 (Partial — some MCP tools), Level 3 (Visual — screen capture), Level 4 (Memory — cross-session persistence), Level 5 (Full Stack — visual + memory + coordination). Each level gets a personalized recommendation with next steps.

## Scoring

| Concept | Perceived Value (5x) | Ease of Creation (5x) | Email Potential (5x) | Total (/75) |
|---------|---------------------|-----------------------|---------------------|-------------|
| AI Context Cost Calculator | 4 (20) | 5 (25) | 5 (25) | **70** |
| AI Agent Setup Audit | 4 (20) | 4 (20) | 5 (25) | **65** |
| MCP Setup Checklist | 3 (15) | 5 (25) | 3 (15) | **55** |
| MCP Tools Cheat Sheet | 3 (15) | 5 (25) | 3 (15) | **55** |
| Context Maturity Quiz | 3 (15) | 3 (15) | 4 (20) | **50** |

## Winner: AI Context Cost Calculator

**Why:** Highest score (70/75). Personalized output (hours saved, dollar value) creates genuine perceived value — developers will want to see their number. The calculation naturally gates behind email ("send my results"). Pure HTML/JS, zero backend, buildable in 2 hours. The output doubles as a sharing mechanism ("I'm losing 6 hours/week to manual screenshots").

**Backup:** AI Agent Setup Audit (65/75) — stronger for solution-aware visitors who already know they need better context. Good for a v2 lead magnet.

## Implementation: AI Context Cost Calculator

### User Flow
1. Landing page button: "Calculate your context cost" (opens modal or scrolls to section)
2. Form with 4 inputs:
   - AI sessions per day (slider, 1-30, default 10)
   - Screenshots per session (slider, 0-10, default 3)
   - Seconds per screenshot workflow (slider, 10-120, default 45)
   - Your hourly rate in $ (input, default 75)
3. "Calculate" button
4. Email gate: "Enter your email to see your results and get setup tips"
5. Results shown + emailed:
   - Screenshots/day: [sessions x screenshots]
   - Minutes lost/day: [total x seconds / 60]
   - Hours lost/week: [minutes x 5 / 60]
   - Annual cost: [hours x 52 x hourly_rate]
   - "With ContextPulse: 0 manual screenshots. [hours] hours/week back."

### Technical Implementation
- **Pattern A: Embedded section** on landing page (no modal complexity)
- Pure HTML + CSS + vanilla JS (no dependencies)
- Email capture: Gumroad free product (simplest — already configured)
  - Create "AI Context Cost Calculator Results" as free Gumroad product
  - On form submit: open Gumroad overlay with email pre-filled
  - After email: redirect to results (or show inline)
- Alternative: skip email gate for v1, just show results with a "Get setup tips by email" optional CTA below results

### Copy
**Section heading:** How much is manual screenshotting costing you?
**Subheading:** Most developers don't realize how much time they lose to context switching. Find out in 30 seconds.
**CTA button:** Calculate my context cost
**Results heading:** Your AI context cost
**Results CTA:** Get ContextPulse Sight free — save [X] hours/week
