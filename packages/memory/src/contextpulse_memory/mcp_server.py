"""MCP server exposing persistent memory tools to Claude Code.

Free forever (no license required):
  memory_store          — store a key-value memory with tags and TTL
  memory_recall         — retrieve a memory by key
  memory_list           — list memories with optional tag filter
  memory_forget         — delete a memory by key

Pro license (or active 30-day trial):
  memory_search         — hybrid / keyword / semantic search across all memories
  memory_semantic_search — pure semantic (vector) search
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from contextpulse_memory.storage import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.memory.mcp")

mcp_app = FastMCP("ContextPulse Memory")


# ── License gating ───────────────────────────────────────────────────

def _require_starter(func):
    """No-op gate — basic memory tools (store/recall/list/forget) are free forever."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper


def _require_pro(func):
    """Gate a tool behind Pro license (or active trial)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from contextpulse_core.license import has_pro_access, get_license_tier
        if has_pro_access():
            return func(*args, **kwargs)
        tier = get_license_tier()
        return json.dumps({
            "error": (
                "This tool requires a ContextPulse Pro license. "
                "Starter licenses include memory_store, memory_recall, memory_list, and memory_forget."
            ),
            "current_tier": tier or "free",
            "upgrade_url": "https://contextpulse.ai/pricing",
        })
    return wrapper

_DEFAULT_DIR = Path.home() / ".contextpulse" / "memory"
_store: MemoryStore | None = None
_store_lock = threading.Lock()


def _get_store() -> MemoryStore:
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            db_dir = Path(os.environ.get("CONTEXTPULSE_MEMORY_DIR", str(_DEFAULT_DIR)))
            _store = MemoryStore(db_dir)
            logger.info("MemoryStore initialized at %s", db_dir)
    return _store


@mcp_app.tool()
@_require_starter
def memory_store(
    key: str,
    value: str,
    tags: list[str] | None = None,
    ttl_hours: float = 24.0,
) -> str:
    """Store a key-value memory. Overwrites if key already exists.

    Memories persist in warm tier (SQLite WAL) and hot tier (in-memory, 5 min).
    Use tags to group related memories; set ttl_hours=0 for permanent storage.

    Args:
        key: Unique identifier (e.g., "user/preferences", "project/deadline")
        value: Content to store (free text, JSON, code snippets, etc.)
        tags: Optional grouping tags (e.g., ["project", "contextpulse"])
        ttl_hours: Time-to-live in hours (default 24h, 0 = permanent)
    """
    if not key or not key.strip():
        return json.dumps({"success": False, "error": "key cannot be empty"})
    if ttl_hours < 0:
        return json.dumps({"success": False, "error": "ttl_hours cannot be negative"})
    ttl_hours = min(ttl_hours, 8760.0)  # cap at 1 year
    store = _get_store()
    tags = tags or []
    ttl = ttl_hours if ttl_hours > 0 else None
    try:
        store.store(key=key, value=value, tags=tags, ttl_hours=ttl)
        return json.dumps({"success": True, "key": key, "tags": tags, "ttl_hours": ttl_hours})
    except Exception as exc:
        logger.exception("memory_store failed: %s", key)
        return json.dumps({"success": False, "error": str(exc)})


@mcp_app.tool()
@_require_starter
def memory_recall(key: str) -> str:
    """Retrieve a memory by exact key. Checks hot tier first, then warm tier.

    Returns the memory value and metadata, or not-found if missing/expired.

    Args:
        key: The exact key used when the memory was stored
    """
    store = _get_store()
    result = store.recall(key)
    if result is None:
        return json.dumps({"found": False, "key": key})
    return json.dumps({"found": True, **result}, default=str)


@mcp_app.tool()
@_require_pro
def memory_search(
    query: str,
    limit: int = 20,
    mode: str = "hybrid",
) -> str:
    """Search stored memories by keyword, semantic similarity, or both.

    Modes:
      hybrid   (default) — combines FTS5 keyword rank with semantic cosine
                           similarity (fts 40%, semantic 60%). Falls back to
                           keyword-only if the embedding model is not loaded.
      keyword  — FTS5 full-text search with porter stemming.
                 Supports FTS5 syntax: "word1 AND word2", "phrase", "word*".
                 Results span warm tier and cold tier summaries.
      semantic — Pure vector search using all-MiniLM-L6-v2 embeddings.
                 Falls back to keyword search if model is unavailable.

    Args:
        query: Search terms (FTS5 syntax supported in keyword/hybrid modes)
        limit: Maximum results to return (1–200, default 20)
        mode:  Search mode — "hybrid", "keyword", or "semantic" (default "hybrid")
    """
    limit = max(1, min(limit, 200))
    if not query or not query.strip():
        return json.dumps({"count": 0, "results": [], "query": query, "error": "query cannot be empty"})
    if mode not in ("hybrid", "keyword", "semantic"):
        mode = "hybrid"
    store = _get_store()
    if mode == "hybrid":
        results = store.hybrid_search(query, limit=limit)
    elif mode == "semantic":
        results = store.semantic_search(query, limit=limit)
    else:
        results = store.search(query, limit=limit)
    return json.dumps({
        "count": len(results),
        "results": results,
        "query": query,
        "mode": mode,
    }, default=str)


@mcp_app.tool()
@_require_pro
def memory_semantic_search(query: str, limit: int = 20) -> str:
    """Search memories by semantic meaning using vector embeddings.

    Uses the all-MiniLM-L6-v2 model to find conceptually similar memories even
    when they use different words.  Falls back to FTS keyword search if the
    embedding model has not been downloaded yet.

    Args:
        query: Natural language query (plain text — no FTS syntax needed)
        limit: Maximum results to return (1–200, default 20)
    """
    limit = max(1, min(limit, 200))
    if not query or not query.strip():
        return json.dumps({"count": 0, "results": [], "query": query, "error": "query cannot be empty"})
    store = _get_store()
    results = store.semantic_search(query, limit=limit)
    return json.dumps({
        "count": len(results),
        "results": results,
        "query": query,
        "mode": "semantic",
    }, default=str)


@mcp_app.tool()
@_require_starter
def memory_list(tag: str | None = None, limit: int = 50) -> str:
    """List stored memories, optionally filtered by tag.

    Returns non-expired memories ordered by most recently updated first.

    Args:
        tag: Optional tag to filter by (e.g., "project", "deadline")
        limit: Maximum entries to return (default 50)
    """
    limit = max(1, min(limit, 500))
    store = _get_store()
    entries = store.list_all(tag=tag, limit=limit)
    return json.dumps({
        "count": len(entries),
        "memories": entries,
        "filter_tag": tag,
    }, default=str)


@mcp_app.tool()
@_require_starter
def memory_forget(key: str) -> str:
    """Delete a memory by key. Returns whether deletion succeeded.

    Removes from hot and warm tiers immediately.

    Args:
        key: The exact key of the memory to delete
    """
    store = _get_store()
    deleted = store.forget(key)
    return json.dumps({"success": deleted, "key": key})


@mcp_app.tool()
@_require_starter
def memory_stats() -> str:
    """Return storage statistics for the memory system.

    Reports entry counts per tier, database sizes, data directory path,
    and whether the semantic embedding model is loaded.
    """
    store = _get_store()
    stats = store.stats()

    # Add embedding engine availability
    try:
        from contextpulse_memory.embeddings import get_engine
        engine = get_engine()
        stats["embedding_model_loaded"] = engine.is_available()
    except Exception:
        stats["embedding_model_loaded"] = False

    # Human-friendly size fields
    stats["warm_db_kb"] = round(stats["warm_db_bytes"] / 1024, 1)
    stats["cold_db_kb"] = round(stats["cold_db_bytes"] / 1024, 1)

    return json.dumps(stats, default=str)


def _maintenance_loop(interval_s: int = 3600) -> None:
    """Background thread: prune expired entries and optimize FTS indices.

    Runs every *interval_s* seconds (default 1 hour). Safe to run concurrently
    with all read/write operations — WarmTier and ColdTier are thread-safe.
    """
    import time as _time

    while True:
        _time.sleep(interval_s)
        try:
            store = _get_store()
            pruned = store.prune()
            store.optimize()
            logger.info(
                "Maintenance: pruned %d hot + %d warm entries; FTS indices optimized",
                pruned.get("hot", 0), pruned.get("warm", 0),
            )
        except Exception:
            logger.exception("Maintenance loop error (non-fatal)")


def main() -> None:
    logger.info("Starting ContextPulse Memory MCP server")
    _get_store()
    # Start background maintenance thread (prune + PRAGMA optimize every hour)
    maintenance_thread = threading.Thread(
        target=_maintenance_loop, daemon=True, name="memory-maintenance"
    )
    maintenance_thread.start()
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
