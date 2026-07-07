# contextpulse-knowledge

Local bi-temporal fused knowledge-graph spine for ContextPulse — **Phase 1**.

This package turns the flat `events` capture log into a queryable knowledge graph:
deterministic Tier-0 facts (sessions, apps, project, vocab) with **bi-temporal**
validity (when a fact was true in the world) and assertion time (when the system
believed it), provenance back to the source observations, and fused/temporal
recall via `facts_about`, `context_at`, `timeline`, and hybrid `search`.

## Architecture

- **`cp_core.py`** — the referee. **Pure logic** (no I/O, no SQLite, no clock):
  value objects, deterministic IDs, entity resolution, the bi-temporal fusion
  rules, the ingest pipeline, and the query semantics. Everything else defers to
  it. Purity is enforced by `tests/test_purity.py`. This is also the **reference
  implementation the future Rust crate is validated against** (see below).
- **`store_sqlite.py`** — the imperative shell: opens/migrates `knowledge.db`,
  runs the idempotency pre-check, applies `cp_core` ChangeSets transactionally,
  executes queries, and owns the numpy cosine fast path.
- **`schema.sql` / `migrate.py`** — forward-only schema v1 (`user_version=1`).
- **`extractors/`** — Tier-0 deterministic extractors (sessions, apps, vocab).
- **`bridge.py`** — `events` → `observations` backfill + live EventBus listener.
- **`conformance/vectors/`** — 14 language-neutral JSON conformance vectors. The
  Python harness (`tests/test_conformance.py`) and the future Rust harness
  consume the *same* files — this is the "port hedge": it lets the Rust port be a
  mechanical, provably-equivalent step whenever the Phase 5/6 gate opens.

## Status

Phase 1 per `.internal/fable-redesign/phase1-build-ready-design-2026-07-07-v2.md`
(Fable-authored, Fable-refuted). Gated behind the Phase 0 wedge-probe save signal;
`knowledge.enabled=false` yields a byte-identical capture path.
