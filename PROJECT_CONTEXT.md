# Project Context

## Overview

ContextPulse is a local-first desktop daemon (Windows primary, macOS Phase 1 ported and
paused) that captures screen, voice, and keyboard/mouse activity in real time and
exposes it to AI agents through the Model Context Protocol (MCP). One process, one
tray icon, ~35 MCP tools, zero cloud dependency for the open-core daemon. It is a
public, open-core project — AGPL-3.0 — with a separately-licensed, cleanly-partitioned
paid cloud tier under active evaluation.

## Goals

- Give AI agents (Claude Code and others) ambient awareness of what David is actually
  doing — screen content, dictated speech, keyboard/mouse activity — without any
  cloud round-trip.
- Prove out a local bi-temporal knowledge-graph ("KG spine") as the durable memory
  layer underneath that context, gated on a measured outcome rather than built on
  faith (see Phase 0 wedge probe below).
- Ship something a stranger could install and use — not a thing that only runs
  because David knows how to start it (Cooper's charter bar).

## Tech Stack

- Python 3.12+ (workspace default 3.14; this repo pins `>=3.12` for broader
  compatibility with contributors).
- `mcp>=1.0,<2` — **deliberately bounded at the major version.** `mcp` 2.0.0 removed
  `mcp.server.fastmcp` and broke every CI job when it shipped; see
  `cp-migrate-mcp-2x-api` (parked, real migration work, not a bounded single-pass fix).
- `uv` + `pyproject.toml` per package (monorepo, `packages/*` workspace members).
- `pystray` (tray icon), `pynput` (global hotkeys — not `keyboard`, which needs
  elevation), `faster-whisper` (dictation), `mss` (screen capture), SQLite with
  `journal_mode=WAL` + `busy_timeout=5000` everywhere (Windows AV/indexer lock
  contention otherwise causes flaky `SQLITE_BUSY`).
- GitHub: public repo `github.com/ContextPulse/contextpulse` (`origin`), private
  backup remote `contextpulse-wip` (`backup`). Code-in-progress and internal dossiers
  (`.internal/`, gitignored) live only on the private side until validated live.

## Packages (`packages/*`, each an independent `uv` package)

| Package | Import name | Purpose |
|---|---|---|
| `core` | `contextpulse_core` | Daemon lifecycle (`daemon.py`), platform abstraction (Windows/macOS/Linux), config, licensing, settings UI |
| `screen` | `contextpulse_sight` | Screen capture, OCR, clipboard — "Sight" |
| `voice` | `contextpulse_voice` | Hotkey → record → transcribe (Whisper) → paste — "Voice", plus vocabulary learning |
| `touch` | `contextpulse_touch` | Keyboard/mouse burst + correction-detection capture — "Touch" |
| `knowledge` | `contextpulse_knowledge` | The bi-temporal KG spine (Phase 1): `cp_core.py` referee, `store_sqlite.py`, bridge/ingestor — gated behind `knowledge_enabled`, off by default |
| `memory` | `contextpulse_memory` | Standalone MCP memory server (3-tier hot/warm/cold), Pro-license-gated search tools |
| `project` | `contextpulse_project` | Project-aware routing layer (keyword-based, routes activity to the right project's journal) |
| `agent` | `contextpulse_agent` | Agent coordination / session protocol — scaffolded, largely unbuilt |
| `meeting` | (unbuilt) | Scaffolded package directory, no source yet |

## Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| Evolve the existing Python daemon, don't rewrite in Rust | Fable clean-room redesign recommended evolution; Rust deferred behind a measured gate | 2026-07-03 |
| Phase 0 wedge probe: kill-switch = 3 attributed saves (fused/temporal recall not answerable by plain FTS) in ~4 weeks, else STOP at near-zero sunk cost | Avoid building the full KG on faith; force a decided outcome | 2026-07-03 |
| Phase 0 reached its documented STOP (2026-08-20) | Save gate did not clear 3 attributed saves in the window | 2026-08-20 |
| **EXTEND the save gate, with a real attribution instrument built FIRST** | A gate reading "0 of 3" is only evidence if something was actually capable of counting to 3 — the original gate measured all fused/temporal-recall saves, not just KG-attributable ones | 2026-08-22 (David) |
| Let the KG keep accumulating; no new dated deadline; do not build the injection redesign yet | Bigger ecosystem problems exist; this is not one of them right now | 2026-08-24 (David) |
| `mcp` pinned `<2` across every package | 2.0.0 removed `mcp.server.fastmcp`; unbounded major ranges are a standing anti-pattern per `developing-python` | 2026-08-18 |
| Public repo stays commercially separate from `ContextPulseCloudProto` (paid cloud tier) | AGPL-3.0 open-core boundary must not leak proprietary/cloud code | 2026-03-24 / ongoing |

## Current State

### Done
- Public open-core daemon (Sight + Voice + Touch), ~35 MCP tools, packaged and
  installable (installer under `installer_output/`, `dist/`).
- Phase 1 KG-spine core built and tested independently: `cp_core.py` (bi-temporal
  referee), `store_sqlite.py`, schema v1, 14 language-neutral conformance vectors,
  29 tests green (adversarially reviewed, not just self-tested — see
  `feedback_conformance_green_not_correct`).
- Single-instance daemon guard now checks the mutex before paying the cost of
  Sight/Voice/Touch/knowledge module init (fixed 2026-08-29, commit `275311e`).
- `contextpulse-memory` package installed editable so `pytest --collect-only` at
  repo root actually collects tests (fixed 2026-08-28).

### In Progress
- Phase 0 wedge probe extended per David's 2026-08-22 ruling. **The attribution
  instrument is BUILT and live** (corrected 2026-09-19; this file previously said
  "not yet built", which was wrong): `probe.record_usage()` writes a `tool_usage`
  row on every real `facts_about`/`context_at` call, wired at `probe_mcp.py:99`
  and `:124`, fail-soft, covered by tests, and reconciled against confirmed saves
  by `scripts/probe_usage_report.py`.
  What the instrument then revealed is the actual blocker: **`tool_usage` held
  exactly ONE row for all time** — `facts_about('1Password')`, 2026-08-24 — against
  1000 accumulated facts. The tools are exposed over MCP and work, but the
  `using-contextpulse` skill never mentioned them, so no agent could discover
  them. The gate's "0 of 3 attributed saves" was never a verdict on recall
  quality; the experiment had no treatment arm. Skill fixed 2026-09-19
  (`~/.claude/skills/using-contextpulse/SKILL.md`, commit 52b5991); the gate is
  measurable from this point forward, and any count taken before it is void.
- `working/` directory retention: was misdiagnosed as unbounded 4.3GB scratch;
  corrected 2026-08-27 — it holds exactly one real asset
  (an interview-episode asset directory, name redacted per the open PII scrub —
  see `cp-public-history-surname`), now protected by `CP_RETENTION_PROTECT` in
  `retention_sweep.py` rather than deleted.
- README/branch fragmentation: doc-only fixes have landed on `main` and
  `phase1-kg-spine` independently at different times, risking drift
  (`cp-readme-fixes-split-across-branches`, partially remediated 2026-08-28).

### Not Started
- The KG injection redesign (deliberately not started per David's 2026-08-24 ruling).
- `packages/agent` and `packages/meeting` — scaffolded, no real implementation.
- `cp-migrate-mcp-2x-api` — the real migration off the `<2` pin (parked, scoped as
  genuine multi-package work, not a bounded fix).

## Next Steps

1. **Phase 0 is RUNNING again as of 2026-09-19** (David's call; decision row
   `cp-phase0-restart-20260919`). The 2026-08-20 STOP at "0 of 3 attributed saves"
   is **void as evidence** — `tool_usage` held one row for all time, so the
   experiment had no treatment arm and the number was never a verdict on recall.
   The restart carries a **two-stage gate**, because the old one could not tell
   "never called" apart from "called and useless" — opposite problems with
   opposite fixes:

   | Stage | Condition | Checked by |
   |---|---|---|
   | 1 — precondition | ≥ 20 `tool_usage` rows after 2026-09-19 | `python scripts/probe_usage_report.py --since 2026-09-19` |
   | 2 — value | ≥ 3 journal rows `category=phase0-save` after 2026-09-19 | same command |

   Checkpoint **2026-10-17**. Precondition met and saves ≥ 3 → the KG earned its
   keep. Precondition met and saves < 3 → a *genuine* stop, and unlike August that
   verdict is real evidence. **Precondition not met → a routing defect, not a
   verdict — re-open it and do not judge the knowledge graph.** The thresholds 20
   and 3 are the agent's; David set the original 3 and delegated the restart.
2. Fix the consolidator's silent failure before trusting any new gate reading
   (`cp-consolidator-silent-zero-fact-runs`). A scheduled run on 2026-09-19 12:30
   read 1500 events, returned in 6.4s and wrote 0 facts while printing "OK"; the
   same wrapper run by hand at 15:07 took 42s and wrote 17. `parse_facts` returns
   `[]` on any parse failure and the caller records `error=None`, so a total
   extraction failure is indistinguishable from a quiet day. A gate fed by a
   silently-empty fact store would repeat the exact 2026-08-20 mistake.
3. Get CI green on the public default branch. It is RED at `5c50d63`, the current
   public HEAD, and has been since the repo was made public: `packages/core`'s own
   tests import `contextpulse_sight` and `contextpulse_knowledge`, and no job in
   `.github/workflows/ci.yml` installs `packages/knowledge` (the cross-platform job
   also omits `packages/screen`), so 4 tests die on import in every job. All 6 open
   Dependabot PRs show the same failures, which is how it was found. Same root cause
   as `cp-knowledge-package-not-in-root-build`.
4. Decide the 6 open Dependabot PRs, and set `versioning-strategy` in
   `.github/dependabot.yml`. All six merely RAISE dependency floors, which narrows
   who can install an open-core library for no stated benefit; the absent strategy
   setting is why they were generated, and they will regenerate weekly until it is
   set. That file also watches only `/`, so no `packages/*/pyproject.toml` is
   monitored at all — a vulnerable pin in a sub-package would never raise a PR.

**Settled 2026-09-19, listed here only so they are not re-proposed:** the README
fragmentation between `main` and `phase1-kg-spine` is reconciled (both at `5c50d63`,
0/0 divergence against both remotes), and the public-history leak is resolved — 102
paths stripped, the repo deleted and recreated to kill `refs/pull/*` reachability,
validated clean on a fresh clone, and returned to public. The private `contextpulse-wip`
remote still holds the pre-rewrite line; that is private and untouched by design.

## Open Questions

- Is ContextPulseCloudProto ("built and never started" per the 2026-08-23 finding)
  revived or formally retired? A productization question for Ivy/David, not this
  repo's code.
- Does `contextpulse-nightly-learning`'s `consolidate_learning` output actually get
  consumed by CP retrieval, or is it a 4th sitting-knowledge accumulator?
  (`cp-nightly-learning-audit`, open since 2026-07-09, never verified.)

## Notes

- Full Fable redesign plan of record:
  `.internal/fable-redesign/cp-implementation-plan-FINAL.md` (gitignored, not in the
  public repo).
- Architecture/business docs referenced in prior sessions
  (`BUSINESS_PLAN.md`, `TECHNICAL_PLAN.md`, `VISION.md`) — check current existence
  before trusting; several were drafted in March 2026 and may have drifted from the
  Fable redesign's simplified direction.
- This file was written 2026-08-29 by Cooper (product engineering director) to close
  a recurring maintenance-friction gap: this repo had neither `PROJECT_CONTEXT.md`
  nor `LESSONS_LEARNED.md` despite being one of the most active projects in the
  estate (`cp-missing-project-context-and-lessons`). Facts above are drawn from the
  live repo state and the shared journal at time of writing — reverify anything
  load-bearing before acting on it, per the estate-wide rule that reports are
  photographs, not live views.
