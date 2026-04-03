# ContextPulse Testing Playbook

**Goal:** 10 sessions × 2-3 hours each = 20-30 hours of deliberate testing while doing real project work. Each session has a theme that exercises specific modules intensely.

**Setup before each session:**
1. Start daemon: `pythonw.exe -m contextpulse_core.daemon`
2. Start MCP server: `python -m contextpulse_core.mcp_unified`
3. Run canary: `python scripts/canary_health_check.py` (baseline — all tools responding?)
4. Note the time you started

**After each session:**
1. Run canary again (did anything degrade?)
2. Note any bugs, crashes, or weird behavior in the log below
3. `git diff` to see if any config/data files changed unexpectedly

---

## Session 1: Screen Context Marathon (Sight Module)
**Real work:** Code review or refactor in any project
**What you're testing:** Capture engine, buffer, OCR, privacy

- [ ] Work normally for 15 min (auto-capture builds buffer)
- [ ] Ask Claude: "What have I been working on?" → exercises `get_recent` + `get_activity_summary`
- [ ] Ask Claude: "What's on my screen right now?" → exercises `get_screenshot`
- [ ] Ask Claude: "Read the text on my screen" → exercises `get_screen_text` (OCR)
- [ ] Open a password manager or bank site → verify privacy blocklist kicks in (should skip capture)
- [ ] Press Ctrl+Shift+P (pause) → work for 2 min → unpause → verify gap in buffer
- [ ] Ask Claude: "What was I doing 20 minutes ago?" → exercises `get_context_at`
- [ ] Ask Claude: "Search my screen history for [keyword from earlier]" → exercises `search_history`
- [ ] Switch between 3+ different apps rapidly → check `get_activity_summary` shows all of them
- [ ] If multi-monitor: ask for screenshot of specific monitor vs all monitors

**Edge cases to try:**
- Minimize all windows (empty desktop) → screenshot
- Full-screen video or game → screenshot
- Very small window (< 200px) → screenshot
- Rapid Alt+Tab between apps for 30 seconds → buffer integrity

---

## Session 2: Voice Dictation Workout (Voice Module)
**Real work:** Write commit messages, documentation, or emails by voice
**What you're testing:** Transcription, vocabulary, cleanup, corrections

- [ ] Dictate a simple sentence (Ctrl+Space hold) → verify transcription
- [ ] Dictate project-specific terms: "ContextPulse", "SwingPulse", "Freqtrade", "Alpaca"
- [ ] Dictate a technical sentence with code terms: "refactor the MCP server endpoint"
- [ ] Use fix-last (Ctrl+Shift+Space) on a bad transcription
- [ ] Dictate 10+ sentences over 30 min → check `get_voice_stats`
- [ ] Ask Claude: "Show my recent dictations" → `get_recent_transcriptions`
- [ ] Ask Claude: "What vocabulary corrections are active?" → `get_vocabulary`
- [ ] Deliberately say something that gets mis-transcribed → manually correct it by typing → check if `get_correction_history` catches it
- [ ] Run `learn_from_session` → check if new vocabulary entries are reasonable
- [ ] Run `rebuild_context_vocabulary` → verify it pulls from your PROJECT_CONTEXT.md files

**Edge cases to try:**
- Dictate while music is playing
- Dictate a very long sentence (30+ words)
- Dictate very quickly vs very slowly
- Dictate with background noise (fan, TV)
- Hold Ctrl+Space but say nothing → release

---

## Session 3: Keyboard & Mouse Analytics (Touch Module)
**Real work:** Intensive coding session in VS Code or similar
**What you're testing:** Typing bursts, click tracking, correction detection

- [ ] Code for 20+ minutes straight → check `get_recent_touch_events`
- [ ] Ask Claude: "How's my typing today?" → `get_touch_stats` (WPM, backspace ratio)
- [ ] Do a mix of: typing, clicking, scrolling, dragging → verify all event types appear
- [ ] Type something, backspace heavily to fix it → check backspace ratio reflects it
- [ ] Dictate something, then immediately correct it by typing → `get_correction_history`
- [ ] Ask Claude: "Am I using keyboard or mouse more?" → cross-modal analysis

**Edge cases to try:**
- Hold a key down for 5+ seconds
- Paste a large block of text (Ctrl+V) → does it register as a "typing burst"?
- Click rapidly (10+ clicks in 2 seconds)
- Use keyboard shortcuts extensively (Ctrl+C, Ctrl+V, Ctrl+Z chains)

---

## Session 4: Project Detection & Routing (Project Module)
**Real work:** Switch between 2-3 different projects (StockTrader, CryptoTrader, ContextPulse)
**What you're testing:** Auto-detection, keyword scoring, journal routing

- [ ] Open StockTrader in VS Code → ask Claude: "What project am I working on?" → `get_active_project`
- [ ] Switch to CryptoTrader → ask again → verify it detected the change
- [ ] Ask Claude: "List all my indexed projects" → `list_projects`
- [ ] Ask Claude: "Get the context for StockTrader" → `get_project_context`
- [ ] Ask Claude: "Route this insight to CryptoTrader: canary bot needs dedicated Kraken keys" → `route_to_journal`
- [ ] Test `identify_project` with ambiguous text: "backtest the strategy" → should score multiple projects

**Edge cases to try:**
- Open a non-project directory → what does `get_active_project` return?
- Have two projects open in split-screen → which one wins?
- Type a message mixing project keywords ("backtest crypto in SwingPulse") → scoring

---

## Session 5: Memory System Stress Test (Memory Module)
**Real work:** Save notes, decisions, and learnings as you work
**What you're testing:** CRUD, search, TTL, quota

- [ ] Store 5 memories: `memory_store` with different tags (trading, infra, personal, idea, todo)
- [ ] Recall each by key: `memory_recall`
- [ ] List by tag filter: `memory_list` with tag="trading"
- [ ] Store a memory with TTL (e.g., 3600 seconds) → verify it expires
- [ ] Check stats: `memory_stats` → storage count, size
- [ ] **Pro features:** `memory_search` (hybrid search across memories)
- [ ] **Pro features:** `memory_semantic_search` ("things related to trading strategies")
- [ ] Forget a memory: `memory_forget`
- [ ] Store 20+ memories rapidly → verify no corruption

**Edge cases to try:**
- Store a very long value (10KB+ text)
- Store with Unicode characters, emoji, code blocks
- Search for something that doesn't exist
- Store duplicate keys → does it overwrite or error?

---

## Session 6: Cross-Modal Integration (Pro Features)
**Real work:** Normal mixed workflow — coding, dictating, browsing
**What you're testing:** Event timeline, cross-modal search, combined context

- [ ] Work for 30 min doing a mix of typing, dictating, browsing, and coding
- [ ] `search_all_events` for a keyword you used in both voice AND screen
- [ ] `get_event_timeline` for the last 30 minutes → verify all modalities appear
- [ ] Ask Claude to correlate: "When I was dictating about X, what was on my screen?"
- [ ] Check clipboard history: copy several things → `get_clipboard_history` → `search_clipboard`

**Edge cases to try:**
- Copy something, dictate about it, then search for it across modalities
- Rapid mode switching (type → dictate → type → click) in 60 seconds
- Long idle period (5+ min) → does timeline show the gap?

---

## Session 7: Privacy & Security Gauntlet
**Real work:** Browse normally, including sensitive sites
**What you're testing:** Blocklist, pause, data handling

- [ ] Add a test pattern to blocklist: `CONTEXTPULSE_BLOCKLIST="bank|password|credentials"`
- [ ] Visit a site with "password" in the title → verify capture is skipped
- [ ] Check that `search_history` does NOT contain blocked content
- [ ] Pause (Ctrl+Shift+P) → visit sensitive sites → unpause
- [ ] Verify buffer has a gap during paused period
- [ ] Check `activity.db` directly → no blocked content leaked
- [ ] Try to get Claude to surface blocked content via creative queries

---

## Session 8: Stability & Recovery
**Real work:** Long session (3+ hours if possible)
**What you're testing:** Daemon stability, memory leaks, crash recovery

- [ ] Run for 3+ hours continuous
- [ ] Check memory usage of daemon process at start vs end (Task Manager)
- [ ] Check `~/screenshots/` disk usage growth rate
- [ ] Kill the daemon mid-session (`taskkill /f /pid <PID>`) → restart → verify buffer recovers
- [ ] Kill the MCP server → restart → verify tools still work
- [ ] Run canary health check after 3 hours → compare to start
- [ ] Check `activity.db` size — is housekeeping pruning old entries?

---

## Session 9: Multi-App Workflow (Integration)
**Real work:** Realistic multi-app session — VS Code + browser + terminal + Telegram + email
**What you're testing:** Real-world usage patterns

- [ ] Switch between 5+ apps over 1 hour
- [ ] Use ContextPulse to ask Claude what you've been doing (activity summary)
- [ ] Use voice to draft an email, then refine by typing
- [ ] Copy code from browser → paste into VS Code → verify clipboard history captures both
- [ ] Ask Claude to help debug using screen context ("look at my screen and help me fix this error")
- [ ] Use project detection while working across multiple repos

---

## Session 10: Pre-Launch Checklist
**Real work:** Final validation
**What you're testing:** Everything, one last time

- [ ] Fresh install test: `pip install -e .` from clean venv
- [ ] Run full UAT: `pytest tests/test_user_acceptance.py -v`
- [ ] Run all unit tests: `pytest packages/ -x -q`
- [ ] Run canary: `python scripts/canary_health_check.py`
- [ ] Exercise every MCP tool at least once (use canary results as checklist)
- [ ] Check all entry points work: `contextpulse-mcp`, `contextpulse-sight`
- [ ] Verify README install instructions work end-to-end
- [ ] Check that 3 usage examples exist in README

---

## Bug Log

| Date | Session | Bug Description | Severity | Fixed? |
|------|---------|-----------------|----------|--------|
| | | | | |

## Metrics

| Session | Date | Duration | Canary Start | Canary End | Bugs Found | Notes |
|---------|------|----------|-------------|-----------|------------|-------|
| 1 | | | /30 | /30 | | |
| 2 | | | /30 | /30 | | |
| 3 | | | /30 | /30 | | |
| 4 | | | /30 | /30 | | |
| 5 | | | /30 | /30 | | |
| 6 | | | /30 | /30 | | |
| 7 | | | /30 | /30 | | |
| 8 | | | /30 | /30 | | |
| 9 | | | /30 | /30 | | |
| 10 | | | /30 | /30 | | |
