# ContextPulse Monetization Strategy

## Open-Core Model

ContextPulse uses an open-core licensing model:
- **Free tier (AGPL-3.0):** Core capture tools are open-source and always free
- **Pro tier (licensed):** Cross-modal and advanced analytics tools require an Ed25519 license key
- **7-day trial:** All Pro features available for 7 days on first use, no signup required

Target customer: developers and power users who want convenience, support, and automatic updates rather than self-hosting from source.

---

## Free Tier — What's Included

### Sight (10 tools)
| Tool | Description |
|------|-------------|
| get_screenshot | Capture screen (active/all/monitor/region) |
| get_recent | Recent frames from rolling buffer |
| get_screen_text | OCR current screen at full resolution |
| get_buffer_status | Daemon health + token cost estimates |
| get_activity_summary | App usage over last N hours |
| search_history | FTS5 search across window titles + OCR |
| get_context_at | Frame + metadata from N minutes ago |
| get_clipboard_history | Recent clipboard entries |
| search_clipboard | Search clipboard by text |
| get_agent_stats | MCP client usage stats |

### Voice (3 tools)
| Tool | Description |
|------|-------------|
| get_recent_transcriptions | Recent voice dictation history |
| get_voice_stats | Dictation count, duration, accuracy |
| get_vocabulary | Current word corrections |

### Touch (3 tools)
| Tool | Description |
|------|-------------|
| get_recent_touch_events | Typing bursts, clicks, scrolls |
| get_touch_stats | Keystroke count, WPM, corrections |
| get_correction_history | Voice dictation corrections detected |

### Project (5 tools)
| Tool | Description |
|------|-------------|
| identify_project | Score text against all projects |
| get_active_project | Detect project from CWD/window title |
| list_projects | All indexed projects |
| get_project_context | Full PROJECT_CONTEXT.md for a project |
| route_to_journal | Route insight to project journal |

**Total free: 21 tools**

---

## Pro Tier — What's Included

### Current Pro Tools (2)
| Tool | Description |
|------|-------------|
| search_all_events | Cross-modal FTS search (screen + voice + clipboard + keys) |
| get_event_timeline | Temporal view of all events across modalities |

### Planned Pro Features (future releases)

1. **get_daily_digest** -- AI-generated summary of the day's activity across all modalities. Aggregates screen time, voice dictations, typing patterns, and app usage into a concise daily report with productivity insights.

2. **search_by_embedding** -- Semantic search across all captured content using vector embeddings. Goes beyond keyword matching to find conceptually related content (e.g., searching for "deployment error" also finds screenshots of stack traces and voice notes mentioning "production issues").

3. **get_focus_report** -- Deep-work and focus session analytics. Detects uninterrupted work blocks, tracks context-switching frequency, and scores focus quality based on app-switching patterns and typing burst continuity.

4. **export_session_context** -- Export a time-windowed slice of all captured context (screen, voice, clipboard, keys) as a structured JSON or markdown bundle. Useful for sharing context with teammates or archiving project sessions.

5. **smart_capture_rules** -- User-configurable capture policies (e.g., "capture every 2s when in VS Code, every 30s in Slack, never in banking apps"). Conditional capture logic beyond the current static interval + privacy blocklist.

---

## Pricing

### Recommended Pricing
| Plan | Monthly | Annual | Savings |
|------|---------|--------|---------|
| **Starter** | $12/mo | $99/yr | 31% off |
| **Pro** | $15/mo | $129/yr | 28% off |

Both tiers currently unlock the same Pro tools. The tier distinction exists in the license payload to support future differentiation (e.g., Pro gets priority support, early access to new features, or higher API limits when cloud sync is added).

### Revenue Projections

Assuming 5-10% free-to-Pro conversion rate and blended $11/mo ARPU (mix of monthly and annual):

| Free Users | Conversion | Paying Users | MRR | ARR |
|------------|------------|-------------|-----|-----|
| 100 | 5% | 5 | $55 | $660 |
| 100 | 10% | 10 | $110 | $1,320 |
| 500 | 5% | 25 | $275 | $3,300 |
| 500 | 10% | 50 | $550 | $6,600 |
| 1,000 | 5% | 50 | $550 | $6,600 |
| 1,000 | 10% | 100 | $1,100 | $13,200 |
| 5,000 | 5% | 250 | $2,750 | $33,000 |
| 5,000 | 10% | 500 | $5,500 | $66,000 |

Break-even on infrastructure costs (Lambda, DynamoDB, SES, domain) is approximately 10-15 paying users.

---

## Upgrade Flow

### User Journey: Free to Pro

1. **Discovery:** User installs ContextPulse (free), configures MCP servers in Claude Code
2. **Trial trigger:** First call to a Pro tool (search_all_events or get_event_timeline) auto-starts 7-day trial
3. **Trial period:** Full Pro access for 7 days, no signup or credit card required
4. **Trial expiry:** Pro tools return a clear message with tier info and upgrade URL
5. **Purchase:** User visits https://contextpulse.ai/pricing, purchases via Gumroad
6. **Delivery:** Gumroad webhook fires to Lambda, which generates Ed25519 license key and emails it via SES
7. **Activation:** User pastes license key in ContextPulse Settings > License tab, or saves to `%APPDATA%\ContextPulse\license.key`
8. **Verification:** License verified locally via Ed25519 signature check -- no phone-home required

### Rejection Message (shown when Pro tool is called without access)
```
This tool requires a ContextPulse Pro license.
Current tier: free.
Upgrade at https://contextpulse.ai/pricing
```

---

## License Enforcement Approach

### Design Philosophy

ContextPulse is open source (AGPL-3.0). A determined self-hoster can remove the `@_require_pro` decorator and recompile. This is by design:

- **Not a DRM system.** The license gate is a convenience boundary, not a security boundary.
- **Target customer** is someone who values their time more than the monthly fee. They want automatic updates, support, and a working product out of the box.
- **AGPL copyleft** requires anyone distributing a modified version to also open-source their changes, which discourages commercial forks.

### What We Enforce

| Mechanism | Purpose |
|-----------|---------|
| Ed25519 signature | Prevents forging license keys without the private key |
| Expiration field (`exp`) | Time-limited licenses for monthly billing |
| HMAC on trial.json | Prevents extending the 7-day trial by editing the start timestamp |
| Machine-bound trial HMAC | Trial files cannot be copied between machines |
| Tier + feature fields | Future-proofs for per-feature gating |

### What We Don't Enforce

| Non-enforcement | Rationale |
|-----------------|-----------|
| Obfuscated code | Open source; obfuscation is counterproductive |
| Phone-home validation | Privacy-first product; offline-only verification |
| Hardware fingerprinting | Too fragile; MAC-based HMAC for trial only |
| Anti-tamper on the decorator | Source is public; AGPL handles redistribution |

---

## Audit Notes (2026-03-26)

### Findings

1. **Decorator application:** Correct. Both Pro tools have `@_require_pro` applied in the right decorator order (`@mcp_app.tool()` > `@_track_call` > `@_require_pro`).

2. **Ed25519 verification:** Solid. Uses `cryptography` library. Signature is verified against raw payload bytes. Tampered payloads are rejected. Public key is hardcoded; private key is Lambda-only.

3. **Trial tamper vulnerability (FIXED):** `trial.json` previously stored a plain `{"start": timestamp}` that could be trivially edited to reset the trial. Added HMAC-SHA256 tamper detection keyed to machine identity (MAC address + public key). Tampered files are now treated as expired.

4. **Error message quality:** Good. Includes current tier and upgrade URL. Could be improved in the future with trial days remaining when in trial.

5. **Other MCP servers:** Voice, Touch, and Project servers correctly have no Pro gating -- all their tools are free tier.

6. **Self-hoster bypass:** Expected and acceptable. The `@_require_pro` check is a single function call that can be patched out. The AGPL license is the real protection against commercial redistribution.
