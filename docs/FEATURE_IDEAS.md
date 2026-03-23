# ContextPulse Sight — Feature Ideas for Differentiation

Generated 2026-03-21 from market research and competitive analysis.

## Tier 1: High Impact, Buildable Now

### 1. Clipboard Context Capture
Capture clipboard contents alongside screenshots. When a developer copies an error message, stack trace, or URL, that's high-signal context. No MCP tool captures this. The clipboard is often more informative than the screen itself.
- **Effort:** Small — hook Win32 clipboard events, store alongside frame metadata
- **Value:** Huge — error messages, URLs, code snippets captured automatically

### 2. MCP Config Generator
`contextpulse-sight --setup claude-code` auto-generates the MCP JSON config and adds it to the right settings file. Every MCP tool has painful manual JSON setup — solving this is a real differentiator for onboarding.
- **Effort:** Small — template generation + file write
- **Value:** Removes #1 friction point (setup)

### 3. Multi-Agent Awareness
Track which MCP client is connected and what tools they've called. "Claude Code requested 3 screenshots in the last hour, Cursor requested 0." Feeds into the Agent product later, but even in Sight it tells you which agents are actually using context.
- **Effort:** Medium — need to track MCP client identity per connection
- **Value:** Analytics + future Agent product foundation

### 4. Diff-Aware Capture
Instead of binary change detection (changed/not), compute a visual diff score. "Screen changed 85% — likely switched apps" vs "Screen changed 3% — cursor moved." Lets agents decide whether new context is worth processing. Saves token costs.
- **Effort:** Medium — pixel-level diff scoring, threshold tuning
- **Value:** Token cost savings for agents, smarter context decisions

## Tier 2: Strong Differentiators, Medium Effort

### 5. Contextual Annotations
Let the user tag captures with voice or text notes via hotkey. "This is the bug I'm debugging" attached to the current frame. When an agent calls `search_history("bug")`, annotated frames surface first.
- **Effort:** Medium — hotkey capture, annotation storage, search integration
- **Value:** No competitor has user-annotated visual context

### 6. Project-Aware Capture
Detect which project/repo the user is working in (window title, active IDE, git repo path) and tag captures with project context. Agents can ask "show me what I was looking at in the StockTrader project."
- **Effort:** Medium — window title parsing, git detection, metadata tagging
- **Value:** Multi-project developers get scoped context

### 7. Token Cost Estimation
Each capture includes estimated Claude token cost if the image were sent. "This frame would cost ~1,200 tokens as image, or ~45 tokens as OCR text." Helps agents make cost-conscious decisions.
- **Effort:** Small — formula based on resolution + compression
- **Value:** Cost transparency, agent optimization

### 8. Capture Webhooks / Event Stream
Emit events when interesting things happen (new app focused, idle detected, screen dramatically changed). Other MCP tools or automation could subscribe. Makes ContextPulse a platform, not just a tool.
- **Effort:** Medium — event system, webhook dispatch
- **Value:** Platform play, integration ecosystem

## Tier 3: Visionary, Larger Effort

### 9. Screen Narration
Periodically run a lightweight local vision model (LLaVA, moondream) to generate one-sentence descriptions of what's on screen. Agents search natural language context without processing images themselves. "User was reviewing a pull request on GitHub."
- **Effort:** Large — local model integration, inference pipeline
- **Value:** Massive token savings, natural language search over visual history

### 10. Cross-Machine Sync
For developers using desktop + laptop, sync the activity database (not images) between machines. Your AI agent knows what you were doing on the other machine. Privacy-preserving: only text metadata syncs, images stay local.
- **Effort:** Large — sync protocol, conflict resolution, encryption
- **Value:** Multi-device developers, enterprise teams

## Recommended Build Order

1. **MCP Config Generator** (Tier 1, smallest effort, biggest onboarding impact)
2. **Clipboard Context Capture** (Tier 1, unique differentiator)
3. **Token Cost Estimation** (Tier 2, small effort, high value)
4. **Diff-Aware Capture** (Tier 1, improves agent efficiency)
5. **Project-Aware Capture** (Tier 2, multi-project users)
6. **Contextual Annotations** (Tier 2, no competitor has this)
7. **Multi-Agent Awareness** (Tier 1, feeds Agent product)
8. **Capture Webhooks** (Tier 2, platform play)
9. **Screen Narration** (Tier 3, game-changer when ready)
10. **Cross-Machine Sync** (Tier 3, enterprise play)
