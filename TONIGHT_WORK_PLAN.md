# ContextPulse — Overnight Work Plan (2026-03-22)

**Author:** Claw (OpenClaw agent)
**Requested by:** David Jerard
**Status:** COMPLETE (Tasks 2+3 done 2026-03-22)

---

## Context

David spent today building clipboard monitoring, agent stats, diff scoring, the `contextpulse-project` package (5 MCP tools, 38 tests), and extensive docs/brand/strategy work. There are 40+ uncommitted files. The spine (`contextpulse-core/spine/`) already exists (~540 LOC: EventBus, ContextEvent, ModalityModule). The memory package (`contextpulse-memory`) is a stub (empty `__init__.py`).

## Work Order (execute sequentially)

### Task 1: Commit + Push All Work ⬜
- Stage all new and modified files (excluding `leak.json` and any `.env` / credential files)
- Commit with descriptive message covering: clipboard monitor, agent stats, diff scoring, project package, docs/brand/strategy
- Push to remote
- **Output:** Clean git status, commit SHA logged

### Task 2: Spine Contract Audit + Hardening ✅
- Added `cognitive_load: float = 0.0` to `ContextEvent` (was missing vs spec §3)
- Uncommented `KEYS` and `FLOW` modalities + 8 new EventTypes (KEYSTROKE, TYPING_BURST, TYPING_PAUSE, SHORTCUT, CLICK, SCROLL, HOVER_DWELL, DRAG)
- Added `cognitive_load` column to EventBus DB schema + INSERT statement
- Fixed FTS tokenizer: `tokenize='porter unicode61'` (was missing)
- Added `get_config_schema()` abstract method to `ModalityModule` (per spec §7)
- Enhanced `test_spine/` with new tests for cognitive_load, KEYS/FLOW modalities, get_config_schema contract
- **Result:** 95 core tests passing

### Task 3: Memory MVP (`contextpulse-memory`) ✅
- Built `packages/memory/src/contextpulse_memory/storage.py`:
  - `HotTier`: in-memory OrderedDict, 5 min TTL, max 500 entries, tag filtering, eviction
  - `WarmTier`: SQLite WAL, FTS5 (porter unicode61), tags, TTL, prune_expired, upsert
  - `ColdTier`: FTS5 summarized archive, 15-min windows, separate memory_cold.db
  - `MemoryStore`: orchestrates all three tiers with cross-modal tagging
- Updated `mcp_server.py` to use new MemoryStore (tags + ttl_hours params on all tools)
- Updated `pyproject.toml` with dev deps, entry point `contextpulse-memory-mcp`
- Tests: 88 memory tests passing (test_storage.py + test_mcp.py)
- **Result:** 221 total tests passing (core + memory + project)

### Task 4: Open-Source Prep (if time permits) ⬜
- Audit repo for sensitive files (`leak.json`, credentials, API keys, `.env`)
- Remove or gitignore any sensitive files found
- Draft `LICENSE` (AGPL-3.0 for base, note commercial license for Pro features)
- Write/update root `README.md` with: what ContextPulse is, install instructions, MCP setup, architecture diagram
- Identify open-core boundary: what's public vs proprietary
- **Output:** Repo ready for public visibility (no secrets, clear license, good README)

### Task 5: Sight Phase 4 Features (if time permits) ⬜
- OCR confidence tracking on activity records
- Capture frequency auto-tuning based on activity level
- Privacy auto-detection (redact sensitive content)
- **Output:** Enhanced Sight with confidence/tuning features

---

## Completion Protocol

When done:
1. Update this file — mark each task ✅ or ❌ with notes
2. Write summary to `claw-memory/2026-03-22.md`
3. Log to journal: `python ~/.claude/shared-knowledge/scripts/log-entry.py --type session-end --content "<summary>" --project ContextPulse --agent claw`
4. Log completed actions: `python ~/.claude/shared-knowledge/scripts/log-entry.py --type action-completed --content "<task>" --project ContextPulse --agent claw`

## Key Files

- Architecture spec: `business-plan/CENTRAL_MEMORY_ENGINE.md` (2,760 lines)
- Spine implementation: `packages/core/src/contextpulse_core/spine/`
- Memory stub: `packages/memory/`
- Project package: `packages/project/`
- Sight package: `packages/screen/`
