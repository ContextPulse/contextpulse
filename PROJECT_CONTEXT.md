# Project Context

## Overview

ContextPulse is a local-first desktop daemon (Windows primary, macOS Phase 1 ported and
paused) that captures screen, voice, and keyboard/mouse activity in real time and
exposes it to AI agents through the Model Context Protocol (MCP). One process, one
tray icon, **37 MCP tools** (counted 1:1 against a live MCP client 2026-09-19; the
"~35" this file and the README carried was never measured), zero cloud dependency
for the open-core daemon. It is a
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
- **Privacy/security modules, added 0.1.1 (2026-09-19)** — all in `packages/core/src/contextpulse_core/`:
  `redact.py` (the single pattern table, moved here from `screen` so every package
  shares one matcher), `purge.py` (retro-sweep of stored rows + FTS rebuild),
  `search_filter.py` (tokenizer-aware match, so search counts are computed against
  redacted text rather than raw), `mcp_auth.py` (per-install bearer token) and
  `env_guard.py` (snapshots the environment before `load_dotenv` so a `.env` cannot
  silently disable auth).
- **The live `contextpulse.ai` page is `.internal/site/index.html`** — byte-identical
  to what is served, verified by fetch-and-diff 2026-09-19. It is gitignored and not
  under git, so it has no revert point. `ContextPulse-jv-private/site/index.html` is
  an OLDER 28-tool design; both carry a `wrangler.toml` naming the same Cloudflare
  Pages project `contextpulse-site`, so deploying the jv-private copy would replace
  the live page with the old one. Deploy from the directory holding the corrected
  page: `npx wrangler pages deploy . --project-name=contextpulse-site --branch=main`.

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
| Redact at **write AND at the MCP boundary**, for every modality — not at one chokepoint | An external report showed `redact_sensitive` had exactly one caller (`ocr_worker.py`), so OCR was protected and clipboard, voice, keystroke, memory and knowledge text were not. A single chokepoint is a chokepoint only for the path that happens to call it | 2026-09-19 |
| Keep the hand-written `redact.py`; do not adopt a redaction library | Measured: ours matches 27/34 probe families against gitleaks' 16/34; Presidio would add 31 runtime deps to a package with 3, for a per-clipboard-event daemon. Vendoring gitleaks' 222 MIT rules **as data** is a 0.1.2 item | 2026-09-19 |
| HOLD the public push of 0.1.1 until David reads the claims changes | The release changes public privacy claims, and publication is his call | 2026-09-19 (David) |
| Config unification keeps `buffer_max_age` 1800 and deletes the placebo-era 300 | The 300 was typed into a Settings dialog whose value the daemon never read; carrying it forward would import a number that never had an effect | 2026-09-19 (David) |

## Current State

### Done
- Public open-core daemon (Sight + Voice + Touch), 37 MCP tools, packaged and
  installable (installer under `installer_output/`, `dist/`).
- **Version 0.1.1 SHIPPED 2026-09-19 23:40 MDT, a security release.** David approved the
  public push after reading the release review doc; PR #17 merged to public `main`
  (`50a4393`, all 14 CI checks green), GitHub release `v0.1.1` published, draft advisory
  `GHSA-xfr9-62vj-4227` (medium, credits Hronom) awaiting the 7-day response to the
  reporter before publication. Private backup `release-0.1.1` mirrors it. The release
  includes the config unification (see below) and the MCP local auth. David's daemon
  and MCP server run it since 23:43; the MCP token was rotated once after the rollout.
  - Redaction now runs at write **and** at the MCP boundary for every stored-text
    modality: clipboard, OCR, voice transcripts, keystroke burst/correction text,
    memory and the knowledge-ingest bridge. Before this, `redact_sensitive` had one
    caller and only OCR text was covered.
  - `purge.py` plus a **one-time startup sweep, per store**, rewrites rows captured
    before the fix. Ran live on David's machine after the 22:07 restart: markers
    present for all five stores (activity, knowledge, memory, memory_cold, probe);
    25 activity rows rewritten.
  - Search was a **count oracle** — `search_clipboard`, `search_history` and
    `memory_search` matched raw stored text and returned counts, so a secret was
    recoverable character by character without ever appearing in output. Closed by
    `search_filter.py`, which asks the same question of the *redacted* text through
    the same tokenizer.
  - The MCP endpoint now requires a **per-install bearer token** (`mcp_auth.py`,
    `env_guard.py`): `O_EXCL` create, user-only ACL, fails closed. Verified live on
    the release code — 401 / 401 / 200 / 403.
  - `clipboard_enabled` is **wired**. It was declared with a default of true and read
    nowhere, so a user who turned clipboard capture off was still captured.
  - Per David's ruling the 19 affected clipboard rows were purged and the pre-purge
    `activity.db` backup was permanently deleted, not recycled — it held the raw values.
  - Full suite on the merged tree: **2015 passed, 14 skipped, 0 failed.**
- Public CI and Security workflows green on `origin/main`, zero open PRs (2026-09-19).
  Root cause was that no job installed `packages/knowledge`; four `packages/core`
  tests import sibling packages. Dependabot noise closed with `versions:` ignore
  rules — the `update-types: semver-major` form does **not** work for range-widening
  updates on a library with no lockfile, proven by a controlled pair of runs.
- Phase 1 KG-spine core built and tested independently: `cp_core.py` (bi-temporal
  referee), `store_sqlite.py`, schema v1, 14 language-neutral conformance vectors,
  29 tests green (adversarially reviewed, not just self-tested — see
  `feedback_conformance_green_not_correct`).
- Single-instance daemon guard now checks the mutex before paying the cost of
  Sight/Voice/Touch/knowledge module init (fixed 2026-08-29, commit `275311e`).
- `contextpulse-memory` package installed editable so `pytest --collect-only` at
  repo root actually collects tests (fixed 2026-08-28).

### In Progress
- **The 0.1.1 public push went out 2026-09-19 23:40 MDT** after David read the release
  review doc (his hold of 22:10 lifted at 22:25). Shipped in it: the claim corrections from branch
  `docs/claims-corrections` (README 12 changes, SECURITY.md 4 principles rewritten
  plus a "what redaction does not cover" section, CONTRIBUTING 1), the corrected
  live site page, a 0.1.x release, and a public GitHub Security Advisory crediting
  Yevhen Tienkaiev by name.
- **The external security report's clock is running.** Acknowledgment due
  **2026-09-21 17:19 MDT**, detailed response due **2026-09-26**, per the 48h/7d
  commitment ContextPulse's own `SECURITY.md` publishes. The clock started when the
  report arrived, not when we reply. The acknowledgment is drafted and approved and
  is receipt-only by design; David sends it himself.
- **Config unification SHIPPED in 0.1.1 and is live on David's machine** (independent
  review: no blockers; its should-fixes landed; cut over 23:43 with `buffer_max_age`
  300 deleted from `config.json` per his ruling — it was the Settings spinbox ceiling,
  not a preference). It was the fix for the dead-control
  finding — the Settings dialog writes `config.json` while the capture daemon reads
  an env-var-only config frozen at start, so **6 fields are dead, 12 misleading and
  5 partial**, and the blocklist meant to stop capture of password managers and 2FA
  prompts is empty in the path that actually runs. Ledger:
  `.internal/audit-2026-09-19/dead-controls.md`.
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
   | 1 — precondition | report's **"Tool calls total" ≥ 22** | `python scripts/probe_usage_report.py --since 2026-09-19` |
   | 2 — value | ≥ 3 journal rows `category=phase0-save` after 2026-09-19 | same command |

   Stage 1 is an **absolute against a baseline of 2** (one call 2026-08-24, one
   verification call 2026-09-19 16:09), i.e. 20 new calls. It is phrased that way
   because `--since` filters only the save count; the tool-call figure is
   all-time and ignores it (`cp-probe-usage-report-since-ignores-tool-calls`).
   Stage 2 *is* filtered correctly. **Do not restate stage 1 as "20 calls since
   <date>" until that defect is fixed** — the report cannot express it.

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
3. **Ship 0.1.1 once David clears the claims changes** — push, tag, publish the
   advisory, deploy the corrected site page. The advisory's exposure statement must
   name **OCR'd screen text alongside clipboard text**, and must note that
   pre-0.1.1 probe-consolidator runs sent raw captured text to an external model.
   The scope widened twice during the fix; do not ship the first draft's narrower wording.
4. ~~Review and cut over config unification~~ — done 2026-09-19 23:43; shipped in 0.1.1.
   Verify once in daylight: focus a window whose title matches a default blocklist
   pattern and confirm `Blocked window -- skipping` in the log; change a Settings
   value and confirm it applies without a restart.
5. The **0.1.2 list**, none of them release blockers and all of them named by the
   reviews that cleared 0.1.1:
   - Database files have no user-only ACLs.
   - Vendor gitleaks' 222 MIT rules **as generated data** — 221 compile under Python
     `re` after two mechanical rewrites, zero new runtime deps. Hazard measured: 165
     of them use a capture group, so the dispatcher must replace the `group(1)` span,
     not `group(0)`, or it swallows surrounding context.
   - `xoxc-`/`xoxd-` Slack tokens are a missing alternation in one pattern (residual
     N-3 from the release review).
   - `packages/memory` cold-tier tests build timestamps one second apart and assume a
     shared time window, so they fail intermittently on any platform; and
     `test_platform_windows.py::TestClipboard::test_clipboard_read_roundtrip` uses the
     real Win32 clipboard and fails whenever the live daemon is polling it.
   - `tests/test_user_acceptance.py::test_daemon_lifecycle` writes `_test_daemon.log`
     into the **live** output dir (`~/screenshots`), so the UAT file cannot run on the
     dev machine at all. Should use `tmp_path` / `CONTEXTPULSE_OUTPUT_DIR`.
   - The startup sweep should take `BEGIN IMMEDIATE` on the marker. Today the daemon
     and the MCP server both scan; one wins and the other logs "database is locked"
     and stands down. Correct, but a duplicate scan.
6. Resolve `cp-security-alias-missing` — **needs David's hands.** The
   `security@contextpulse.ai` send-as alias does not exist, measured live:
   `users.settings.sendAs.list` returns exactly one identity. Until it exists, every
   acknowledgment this project drafts is unsendable under its own identity rule,
   because Gmail stamps the account default on a draft with no `From` and that
   carries his full name into correspondence with an outside reporter.
7. Decide whether `infra/infra-bak/` is the intended home for the AMI/boot scripts or
   an abandoned backup (`cp-infra-scripts-moved-to-bak-docs-dead`). `infra/ami/` and
   `infra/boot/` are now empty and five doc references across three skills are dead.
   Nothing is lost — it was a move. `.gitignore` ignores `infra/` wholesale, so this
   appears in no diff and no commit; a path gate is the only thing that sees it.

**Settled 2026-09-19, listed here only so they are not re-proposed:** public CI and
Security are green with zero open PRs and the Dependabot noise is closed by config
(the only mechanism that works for a grouped update — closing a grouped PR creates no
ignore, and `@dependabot ignore this major version` does not work on one either); the
README fragmentation between `main` and `phase1-kg-spine` is reconciled (it was `5c50d63`
at reconciliation, since advanced by the 0.1.1 work); and the public-history leak is resolved — 102
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
