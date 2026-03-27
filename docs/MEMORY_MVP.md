# ContextPulse Memory — MVP Design Spec

## Overview

The `contextpulse-memory` package provides a three-tier memory store for AI agents. It is already implemented; this document formalizes the MVP interface and constraints for consumers (MCP tools, agent coordination, future SDK).

## Three-Tier Architecture

```
Agent / MCP Tools
       │
       ▼
┌─────────────────────────────────────────┐
│  MemoryStore  (contextpulse_memory)     │
│  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │ Hot Tier │→ │Warm Tier │→ │ Cold  │ │
│  │ in-mem   │  │ SQLite   │  │Tier   │ │
│  │ 5 min TTL│  │ 24h WAL  │  │FTS5   │ │
│  └──────────┘  └──────────┘  └───────┘ │
└─────────────────────────────────────────┘
```

### Hot Tier
- **Storage**: In-memory `OrderedDict`, thread-safe
- **Capacity**: 500 entries max (LRU eviction)
- **TTL**: 5 minutes default, configurable per-write
- **Latency**: Sub-millisecond
- **Use case**: Active session context, clipboard fragments, recent voice snippets

### Warm Tier
- **Storage**: SQLite with WAL mode, FTS5 full-text search
- **Retention**: 24 hours (configurable `expires_at`)
- **Indexes**: key, expires_at, updated_at, modality
- **Search**: Porter stemming, unicode61 tokenization
- **Use case**: Current session memories, today's activity summaries

### Cold Tier
- **Storage**: SQLite FTS5, compressed summaries
- **Retention**: 30+ days
- **Use case**: Historical context, long-term patterns, session archives

## Public API (MVP)

```python
from contextpulse_memory import MemoryStore

store = MemoryStore(warm_db_path="~/.contextpulse/memory.db",
                   cold_db_path="~/.contextpulse/memory_cold.db")

# Write (auto-promotes hot → warm)
store.store(key, value, tags=["screen", "session:abc"], ttl=3600)

# Read (checks hot → warm → cold in order)
entry = store.recall(key)          # -> MemoryEntry | None

# Search (warm + cold FTS)
results = store.search("quarterly review", limit=10)

# Delete
store.forget(key)

# Maintenance
store.prune()                      # evict expired entries from all tiers
```

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Hot tier eviction | LRU when > 500 entries | Bounded memory usage |
| Warm tier persistence | SQLite WAL | Zero-config, good concurrency for single-machine |
| FTS tokenizer | Porter + unicode61 | Handles stemming (run/running/runs) |
| Threading | `threading.Lock` per tier | No asyncio dep; daemon uses threads |
| TTL granularity | Float seconds | Allows sub-second TTLs for tests |

## MCP Tool Surface (Pro-gated)

The `mcp_server.py` exposes 5 tools:

| Tool | Tier(s) | Auth |
|---|---|---|
| `memory_store` | hot+warm | Free |
| `memory_recall` | hot+warm+cold | Free |
| `memory_search` | warm+cold | **Pro** |
| `memory_forget` | hot+warm | Free |
| `memory_list` | warm | **Pro** |

## MVP Constraints

- Single machine only (no sync, no cloud replication in v0.1)
- Max warm DB size: ~500MB (pruning enforced nightly)
- Cold ingestion is manual (batch job, not real-time)
- No encryption at rest in v0.1 (planned for v0.3)

## v0.2 Roadmap

- Semantic search (embedding-based recall via sentence-transformers)
- Automatic hot→warm promotion on TTL expiry rather than silent drop
- Memory importance scoring based on access frequency
- Agent-scoped namespacing (memories isolated per agent type)

## Status

Implementation: **Complete** (61 tests passing, all three tiers)
MCP integration: **Complete** (5 tools, Pro-gating via `@_require_pro`)
Design doc: **This document**
