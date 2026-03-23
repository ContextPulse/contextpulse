# ContextPulse — Overnight Work Plan (2026-03-22)

**Author:** Claw (OpenClaw agent)
**Requested by:** David Jerard
**Status:** IN PROGRESS

---

## Context

David spent today building clipboard monitoring, agent stats, diff scoring, the `contextpulse-project` package (5 MCP tools, 38 tests), and extensive docs/brand/strategy work. There are 40+ uncommitted files. The spine (`contextpulse-core/spine/`) already exists (~540 LOC: EventBus, ContextEvent, ModalityModule). The memory package (`contextpulse-memory`) is a stub (empty `__init__.py`).

## Work Order (execute sequentially)

### Task 1: Commit + Push All Work ⬜
- Stage all new and modified files (excluding `leak.json` and any `.env` / credential files)
- Commit with descriptive message covering: clipboard monitor, agent stats, diff scoring, project package, docs/brand/strategy
- Push to remote
- **Output:** Clean git status, commit SHA logged

### Task 2: Spine Contract Audit + Hardening ⬜
- Review existing spine at `packages/core/src/contextpulse_core/spine/` against `CENTRAL_MEMORY_ENGINE.md` spec
- Verify `ContextEvent` schema matches the architecture doc (event_id, timestamp, modality, data, metadata, correlation_hints)
- Verify `EventBus` supports: subscribe/publish, filtering by modality, async delivery, back-pressure
- Verify `ModalityModule` ABC has: start/stop lifecycle, event emission, health check
- Add tests for spine contracts if missing (`packages/core/tests/test_spine/`)
- Fix any gaps between spec and implementation
- **Output:** Spine passes all contract tests, README updated with usage examples

### Task 3: Memory MVP (`contextpulse-memory`) ⬜
- Build the memory package at `packages/memory/` with:
  - **Storage:** SQLite-backed key-value + semantic memory store
  - **MCP tools:** `memory_store`, `memory_recall`, `memory_search`, `memory_list`, `memory_forget`
  - **Event integration:** Subscribe to EventBus, auto-store significant events
  - **Memory tiers:** Hot (in-memory dict, 5 min TTL) → Warm (SQLite WAL, 24h) → Cold (FTS5 summarized, 30+ days)
  - **Cross-modal tagging:** Events tagged with modality source for correlation queries
- Write tests for all MCP tools and storage layer
- Reference: `business-plan/CENTRAL_MEMORY_ENGINE.md` sections 4 (Memory Tiers) and 8 (MCP Tool Design)
- **Output:** Working memory package with tests passing, installable via `pip install -e packages/memory`

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
