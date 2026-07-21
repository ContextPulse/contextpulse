# ContextPulse Memory — MVP Design

> Status: **Shipped** (v0.1.0-alpha). This document specifies the MVP as
> **actually implemented** in `packages/memory/`, not a forward-looking proposal.
> It is the concrete MVP spec — files, storage format, MCP tool surface, schema,
> and the integration points that remain open.

---

## 1. Purpose

Cross-session persistent memory for AI agents. A Claude Code (or any MCP client)
session can `memory_store` a fact and a later, unrelated session can
`memory_recall` / `memory_search` it — surviving process restarts, context-window
resets, and multi-day gaps. Free-forever basic key/value; Pro-gated semantic and
hybrid search.

Design constraints that shaped the MVP:
- **Zero external services.** No server, no cloud DB — everything is local SQLite +
  in-memory. Runs on the user's box alongside the ContextPulse daemon.
- **No PyTorch.** Semantic embeddings run through ONNX Runtime + `tokenizers`
  only, so the install stays light and CPU-only.
- **Graceful degradation.** If the embedding model has not downloaded, semantic
  and hybrid search silently fall back to FTS5 keyword search. Nothing hard-fails
  on a missing model.

---

## 2. Package layout (files)

```
packages/memory/
├── pyproject.toml                     # contextpulse-memory, AGPL-3.0-or-later, py>=3.12
├── src/contextpulse_memory/
│   ├── __init__.py                    # exports MemoryStore, {Hot,Warm,Cold}Tier, MemoryQuotaExceeded
│   ├── storage.py                     # three-tier engine (the core — 719 LOC)
│   ├── embeddings.py                  # all-MiniLM-L6-v2 ONNX engine, lazy singleton
│   └── mcp_server.py                  # FastMCP server: 7 memory_* tools + license gating
└── tests/
    ├── conftest.py
    ├── test_storage.py
    ├── test_embeddings.py
    └── test_mcp.py
```

Runtime dependencies (from `pyproject.toml`): `contextpulse-core`, `mcp[cli]`,
`numpy>=1.26`, `onnxruntime>=1.17`, `tokenizers>=0.19`.

### On-disk data files

Default data directory: `~/.contextpulse/memory/`
(override with env var `CONTEXTPULSE_MEMORY_DIR`).

| File                 | Tier | Contents |
|----------------------|------|----------|
| `memory.db`          | Warm | Live entries, FTS5 index, embeddings (SQLite WAL) |
| `memory_cold.db`     | Cold | 15-minute summary windows, FTS5 (SQLite WAL) |
| *(none — in-process)*| Hot  | In-memory `OrderedDict`, lost on restart |

Model cache (auto-downloaded on first semantic use):
`~/.contextpulse/models/minilm/` — `model.onnx` + `tokenizer.json`, pinned to a
specific HuggingFace commit (`optimum/all-MiniLM-L6-v2`).

---

## 3. Three-tier storage model

`MemoryStore` (`storage.py`) orchestrates three tiers. A `store()` writes through
Hot + Warm; a `recall()` reads Hot → Warm; search reads Warm → Cold.

| Tier | Backing | Retention | Read latency | Role |
|------|---------|-----------|--------------|------|
| **Hot**  | in-memory `OrderedDict`, `threading.Lock` | 5 min TTL, max 500 entries (LRU evict) | sub-ms | recently-touched fast path |
| **Warm** | SQLite WAL, FTS5, `porter unicode61` | 24 h default TTL (per-entry override; `ttl_hours=0` ⇒ permanent) | ms | primary durable store + search |
| **Cold** | SQLite WAL, FTS5 summaries | 30+ days, 15-min windows | ms | compressed long-term archive |

Key constants (in `MemoryStore`):
- `DEFAULT_HOT_TTL = 300s` (5 min)
- `DEFAULT_WARM_TTL = 86_400s` (24 h)
- `DEFAULT_MAX_WARM_ENTRIES = 50_000` (~150 MB @ ~3 KB/entry). New-key writes past
  the cap raise `MemoryQuotaExceeded`; upserts of existing keys are always allowed.
- Cold window = `900s` (15 min).

### Write path (`store`)
1. Quota check (only for **new** keys).
2. `hot.put()` with `ttl = min(entry_ttl, 300s)`.
3. Compute embedding via `embeddings.get_engine().embed(value)` — wrapped in
   `try/except`; a missing/failed model just stores `embedding=NULL`.
4. `warm.upsert()` — `ON CONFLICT(key) DO UPDATE`, so keys are idempotent.

### Search paths
- `search()` — FTS5 keyword over Warm; if fewer than `limit` hits, tops up from
  Cold (results tagged `tier="cold"`).
- `semantic_search()` — cosine similarity over all Warm rows with an embedding
  (brute force in NumPy; **TODO in code**: swap to FAISS/annoy past ~10K rows).
- `hybrid_search()` — **Reciprocal Rank Fusion** of FTS + semantic result lists,
  `score = Σ 1/(k + rank)`, `k=60` (Cormack et al. 2009). Normalization-free, so
  BM25 ranks and cosine similarities fuse without rescaling. Over-fetches `limit*3`
  from each list before fusing.

### Maintenance
`mcp_server._maintenance_loop` runs on a daemon thread every 3600s: `prune()`
(evict expired hot + delete expired warm) then `optimize()` (`PRAGMA optimize` on
warm + cold FTS). Non-fatal on error.

---

## 4. Storage format (schema)

### Warm — `memories` table
```sql
CREATE TABLE memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             TEXT NOT NULL UNIQUE,
    value           TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array, stored sorted
    created_at      REAL NOT NULL,                -- unix epoch (float)
    updated_at      REAL NOT NULL,
    expires_at      REAL,                         -- NULL = permanent
    attention_score REAL DEFAULT 0.0,             -- reserved for salience ranking
    source_event_id TEXT,                         -- link back to a ContextPulse event
    modality        TEXT,                         -- e.g. "voice", "screen", "clipboard"
    embedding       BLOB                          -- float32[384], little-endian
);
-- indices: key, expires_at (partial), updated_at DESC, modality (partial)
```
FTS5 shadow table `memories_fts(key, value, tags)`, kept in sync by
`AFTER INSERT/UPDATE/DELETE` triggers. Search falls back to `LIKE` if a query
raises an FTS5 syntax error (so raw user text never crashes search).

**Migration note:** `_init_schema` adds the `embedding` column via `ALTER TABLE`
if absent, so pre-v0.2 databases upgrade in place.

### Cold — `cold_summaries` table
```sql
CREATE TABLE cold_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start REAL NOT NULL,           -- floor(updated_at / 900) * 900
    window_end   REAL NOT NULL,
    summary_json TEXT NOT NULL,           -- {"entry_count": N, "keys": [...]}
    text_content TEXT DEFAULT '',         -- concatenated key+value for FTS
    entry_count  INTEGER DEFAULT 0,
    modalities   TEXT DEFAULT '[]',       -- JSON array
    created_at   REAL NOT NULL DEFAULT (unixepoch('subsec')),
    UNIQUE(window_start)
);
```
Cold uses `isolation_level=None` (explicit transactions) to dodge a Python
3.12/3.13 implicit-transaction quirk that let `INSERT OR REPLACE` see stale WAL
snapshots.

---

## 5. MCP tool surface

Server: `FastMCP("ContextPulse Memory")`, stdio transport, entrypoint
`contextpulse_memory.mcp_server:main`. Tools return JSON strings.

| Tool | Tier gate | Signature | Returns |
|------|-----------|-----------|---------|
| `memory_store` | **Free** | `(key, value, tags=None, ttl_hours=24.0)` | `{success, key, tags, ttl_hours}` |
| `memory_recall` | **Free** | `(key)` | `{found, key, value, tags, tier}` |
| `memory_list` | **Free** | `(tag=None, limit=50)` | `{count, memories[], filter_tag}` |
| `memory_forget` | **Free** | `(key)` | `{success, key}` |
| `memory_stats` | **Free** | `()` | tier counts, db sizes, `embedding_model_loaded` |
| `memory_search` | **Pro** | `(query, limit=20, mode="hybrid")` | `{count, results[], query, mode}` |
| `memory_semantic_search` | **Pro** | `(query, limit=20)` | `{count, results[], query, mode}` |

- Gating decorators live in `mcp_server.py`: `_require_starter` (no-op — basic
  tools are free forever) and `_require_pro` (checks
  `contextpulse_core.license.has_pro_access()`; on failure returns a JSON error
  with `upgrade_url`, it does **not** raise).
- Input hardening: empty key rejected; `ttl_hours` clamped to `[0, 8760]`
  (1 year); `limit` clamped (`memory_list` ≤500, search ≤200); bad FTS syntax
  falls back to LIKE rather than erroring.
- `mode` for `memory_search`: `hybrid` (default) | `keyword` | `semantic`.

---

## 6. Embedding engine

`embeddings.py` — `all-MiniLM-L6-v2`, `EMBEDDING_DIM = 384`, ONNX Runtime + HF
`tokenizers`, no PyTorch. Lazy singleton via `get_engine()`; thread-safe with a
load lock and an inference lock. Model + tokenizer auto-download on first use to
`~/.contextpulse/models/minilm/` from a pinned HF commit (checksum-guarded).
`is_available()` is the gate every semantic path checks before use.

---

## 7. Open integration points (not yet in MVP)

These are the deltas between what ships today and the "concrete spec" the action
asked for. Tracked as follow-ups, not part of v0.1.0:

1. **Journal schema linkage.** The `source_event_id` and `modality` columns exist
   to tie a memory back to a ContextPulse event / the shared-knowledge journal,
   but no writer currently populates them from the auto-capture loop. Wiring this
   is the natural pair to the `route_to_journal` MCP tool in the main
   ContextPulse server. (Related work: SightModule dual-write into the
   auto-capture loop and the knowledge-graph spine.)
2. **`attention_score` is inert.** Column is stored and indexed-adjacent but never
   ranked on. A salience pass (recency × access-count × attention) would let
   `memory_list` / search bias toward important memories.
3. **Vector index scaling.** `semantic_search` is brute-force NumPy over all
   embedded rows. Code carries an explicit `TODO` to move to FAISS/annoy past
   ~10K memories.
4. **Cold summarization is mechanical.** `ColdTier.ingest` concatenates key+value
   text per 15-min window; there is no LLM/extractive summary yet — `summary_json`
   only holds `entry_count` + `keys`.
5. **No automatic Warm→Cold demotion.** `ingest` exists but nothing schedules the
   handoff of aged-out warm entries into cold windows; the maintenance loop only
   prunes + optimizes today.

---

## 8. Testing

`tests/` covers storage (`test_storage.py`), embeddings (`test_embeddings.py`),
and the MCP tool layer (`test_mcp.py`). Per project standard (`developing-python`),
run with `uv run pytest packages/memory/` from the repo root before any change to
this package. Semantic tests must tolerate a missing model (CI has no download) —
they assert the FTS fallback path, not exact cosine values.
