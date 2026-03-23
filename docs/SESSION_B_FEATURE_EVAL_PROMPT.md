# Prompt: Evaluate Feature Proposals for Technical Feasibility

Read `C:\Users\david\projects\ContextPulse\docs\FEATURE_PROPOSALS.md` — it contains 10 proposed features for ContextPulse Sight, organized into 3 tiers with complexity estimates from the marketing/research side.

Your job: evaluate each proposal for **technical feasibility** given the current codebase. For each feature:

1. **Read the relevant source files** to understand what infrastructure already exists:
   - `packages/screen/src/contextpulse_sight/app.py` — main daemon, tray, hotkeys
   - `packages/screen/src/contextpulse_sight/capture.py` — mss capture, per-monitor
   - `packages/screen/src/contextpulse_sight/buffer.py` — rolling buffer, change detection
   - `packages/screen/src/contextpulse_sight/activity.py` — SQLite + FTS5 activity DB
   - `packages/screen/src/contextpulse_sight/ocr_worker.py` — background OCR pipeline
   - `packages/screen/src/contextpulse_sight/mcp_server.py` — 7 MCP tools
   - `packages/screen/src/contextpulse_sight/events.py` — event-driven capture
   - `packages/screen/src/contextpulse_sight/config.py` — env var configuration

2. **For each of the 10 features**, assess:
   - Can it reuse existing infrastructure? Which modules?
   - What new dependencies are needed (if any)?
   - Estimated lines of code (rough)
   - Any gotchas or risks specific to our stack (Python 3.14, Windows, pystray, mss, etc.)
   - Does it break any existing tests (118 passing)?
   - Revised complexity estimate: trivial / low / medium / high

3. **Output a revised build order** based on technical feasibility (not just marketing value). If a feature is easier to build than the marketing side estimated, move it up. If harder, move it down or flag blockers.

4. **For the top 3 easiest features**, sketch a brief implementation plan: which files to modify, new files to create, key functions to add.

Save your analysis to `docs/FEATURE_FEASIBILITY.md`.

Do NOT build any features yet — this is an evaluation pass only. The build decisions will come after reviewing your analysis.
