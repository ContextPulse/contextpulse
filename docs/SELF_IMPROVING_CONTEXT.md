# ContextPulse — The Self-Improving Context Engine

*Product vision doc — 2026-03-21*

## Core Principle

Observe errors and friction → detect patterns → fix upstream → user never notices.

This is the same loop proven in ContextPulse Voice (Voiceasy's nightly dictation analysis → custom vocabulary → errors fixed before they reach the LLM). Apply it to every signal ContextPulse captures.

---

## The Data We Collect

| Signal | Source | Already Built |
|--------|--------|---------------|
| Screenshots (every 5s, change-detected) | Sight daemon | Yes |
| OCR text from every frame | Sight OCR worker | Yes |
| Window titles + app names + timestamps | Sight activity DB | Yes |
| Clipboard contents + history | Sight clipboard monitor | Yes |
| MCP tool calls (which agent, when, what) | Sight agent stats | Yes |
| Voice transcriptions + corrections | Voice (Voiceasy) | Yes |
| Session start/end + agent decisions | Memory (shared-knowledge) | Partial |

## What Each Product Learns

### Sight: Self-Optimizing Capture

**OCR quality optimization**
- Track OCR confidence per app/window. Dark IDEs with small fonts score lower.
- Auto-adjust preprocessing (contrast, zoom, crop) per app profile.
- Result: OCR accuracy climbs from ~65% to 90%+ without user configuration.

**Capture frequency tuning**
- Learn per-app screen change rates. VS Code changes every 3s, docs are static for 2 minutes.
- Auto-adjust capture interval per foreground app instead of fixed 5s.
- Result: Better temporal coverage with less disk usage.

**Privacy auto-detection**
- User pauses capture for 1Password but sometimes forgets.
- Learn which apps trigger manual pauses → auto-add to blocklist.
- Result: Privacy protection without manual configuration.

**Relevance scoring**
- Track when agents actually use captured frames vs ignore them.
- Learn which screen states are high-value context vs noise.
- Prioritize storing useful frames, expire worthless ones faster.
- Result: Smaller buffer, higher signal-to-noise ratio.

**Smart storage evolution**
- Currently 4 static modes (smart/visual/both/text).
- Learn per-app optimal storage mode. Terminal = always text. Design tool = always visual.
- Result: 59% disk savings becomes 75%+ as storage adapts to actual usage.

### Memory: Persistent Learning

**Context pre-delivery**
- Track what agents ask for at session start. StockTrader sessions always need: current strategy, account balance, last changes.
- Pre-package that context and deliver it before the agent asks.
- Result: Zero-latency session starts. Agent already knows what it needs.

**Correction cascading**
- Track user corrections to agent suggestions (changed `var` to `const`, rejected old auth patterns, etc.)
- Make corrections permanent — not per-session (which resets) but per-project.
- Result: Agent stops making the same mistake. Corrections compound, never regress.

**Decision context**
- Correlate: what was on screen + what clipboard contained + what the agent suggested + whether user accepted/rejected.
- Build a decision history: "When you set the price at $29, you were looking at Screenpipe's $400 page."
- Result: Agents can reference *why* past decisions were made, not just *what* was decided.

**Error pattern detection**
- OCR captures error messages, stack traces, terminal output across sessions.
- Detect recurring errors: "This same ValueError appeared 3 times this week in different sessions."
- Correlate: "This error always appears after editing config.py."
- Result: Agent says "I see the same error from Tuesday — here's how you fixed it" before user even asks.

### Voice: Context-Aware Transcription

**Screen-aware vocabulary**
- When user dictates while looking at Python code, "def" is misheard as "deaf."
- Use screen context (current language, visible identifiers) to bias transcription.
- Result: Transcription accuracy improves based on what's on screen.

**Nightly correction loop** (already built in Voiceasy)
- Review LLM corrections to speech-to-text output.
- Generate custom vocabulary and auto-fix rules.
- Errors get fixed before they reach the LLM next time.

### Agent: Coordination Intelligence

**Workflow prediction**
- "Every time you open Postman, you switch to VS Code within 90 seconds."
- Agent pre-loads the relevant API file before you switch.
- Result: Context is ready before the user needs it.

**Agent effectiveness scoring**
- Track: which agent asked for what, how often its suggestions were accepted.
- "Claude Code suggestions accepted 73% this week, up from 61%."
- "OCR-based suggestions have higher acceptance than image-based."
- Result: Route tasks to the agent that performs best for that context.

**Cross-agent knowledge sharing**
- Agent A learns a preference at 2 PM. Agent B knows it at 3 PM.
- No re-teaching across tools. One correction propagates everywhere.

---

## The Compounding Flywheel

```
Week 1:  OCR 65% accurate. Agent asks 50 screenshots/day.
         User corrects agent 20 times. Capture uses 2MB/min.

Week 4:  OCR preprocessing tuned per-app → 85%.
         Capture frequency optimized → 30 screenshots (same coverage).
         15 of 20 corrections learned → agent self-corrects.
         Disk usage down 40%.

Week 12: OCR hits 92%. Agent pre-loads context before asked.
         User corrections drop to 2-3/week.
         Capture uses 60% less disk. Sessions start instantly.
```

The user configures nothing. They just use the product and it gets better.

---

## Why This Is the Moat

1. **Switching cost compounds** — The longer you run ContextPulse, the more it knows. Switching to a competitor means starting from zero.
2. **Network effects across products** — Sight feeds Memory. Memory feeds Agent. Voice enriches both. Each product makes the others better.
3. **Data advantage** — No competitor has continuous screen context + voice + clipboard + agent interaction data in one local system. Screenpipe records everything but doesn't *learn* from it.
4. **Privacy as enabler** — Because everything is local, users trust it with sensitive data that cloud tools never see. More data → better learning → wider moat.

---

## Revenue Implication

- **Sight Free** captures the data. Users see immediate value (AI can see their screen).
- **Sight Pro** unlocks the learning features (OCR optimization, relevance scoring, smart storage evolution). Worth paying for because it gets better over time.
- **Memory** is where corrections compound across sessions. This is the product that makes switching impossible.
- **Voice** adds another input modality that enriches the learning.
- **Agent** coordinates everything — the orchestration layer that acts on what Memory learned.

The pitch: "Your AI tools start from zero every session. ContextPulse remembers."

---

## Implementation Priority

| Feature | Product | Difficulty | Impact | Priority |
|---------|---------|-----------|--------|----------|
| Nightly correction loop (voice) | Voice | Done | High | Shipped |
| OCR confidence tracking per app | Sight | Low | Medium | Next |
| Capture frequency auto-tuning | Sight | Medium | Medium | Phase 2 |
| Correction cascading (persistent) | Memory | Medium | High | Phase 2 |
| Context pre-delivery | Memory | Medium | High | Phase 2 |
| Privacy auto-detection | Sight | Low | Medium | Phase 2 |
| Error pattern detection | Memory | High | High | Phase 3 |
| Screen-aware voice vocabulary | Voice | High | High | Phase 3 |
| Relevance scoring | Sight | High | Medium | Phase 3 |
| Workflow prediction | Agent | High | Medium | Phase 4 |
| Agent effectiveness scoring | Agent | Medium | Medium | Phase 4 |
