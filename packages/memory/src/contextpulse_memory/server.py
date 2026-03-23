"""ContextPulse Memory — MCP stdio server.

Tools:
  memory_store    — Store a key-value memory with optional tags and TTL
  memory_recall   — Recall a memory by key
  memory_search   — Full-text search across all stored memories
  memory_list     — List memories, optionally filtered by tag
  memory_forget   — Delete a memory by key
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from contextpulse_memory.storage import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.memory.mcp")

# Data directory — override with CONTEXTPULSE_MEMORY_DIR env var
_DEFAULT_DATA_DIR = Path.home() / ".contextpulse" / "memory"
_data_dir = Path(os.environ.get("CONTEXTPULSE_MEMORY_DIR", str(_DEFAULT_DATA_DIR)))

mcp_app = FastMCP("ContextPulse Memory")
_store = MemoryStore(_data_dir)


@mcp_app.tool()
def memory_store(
    key: str,
    value: str,
    tags: list[str] | None = None,
    ttl_hours: float = 24.0,
) -> str:
    """Store a memory under the given key.

    Memories flow through hot tier (5 min, instant access) then persist
    in warm tier (SQLite WAL) for the specified TTL. Keys are unique —
    storing to an existing key overwrites the previous value.

    Args:
        key: Unique identifier for this memory (e.g., "user/name", "project/deadline")
        value: The content to store (free text, JSON, code, etc.)
        tags: Optional list of tags for grouping (e.g., ["project", "deadline"])
        ttl_hours: How long to keep this memory (default 24h, 0 = forever)
    """
    tags = tags or []
    ttl = ttl_hours if ttl_hours > 0 else None
    try:
        _store.store(key=key, value=value, tags=tags, ttl_hours=ttl)
        logger.info("stored memory: %s (ttl=%.1fh, tags=%s)", key, ttl_hours, tags)
        return json.dumps({
            "stored": True,
            "key": key,
            "tags": tags,
            "ttl_hours": ttl_hours,
        })
    except Exception as exc:
        logger.exception("memory_store failed for key %s", key)
        return json.dumps({"stored": False, "error": str(exc)})


@mcp_app.tool()
def memory_recall(key: str) -> str:
    """Recall a memory by its exact key.

    Checks hot tier (in-memory, sub-ms) first, then warm tier (SQLite).
    Returns the memory value and metadata, or null if not found.

    Args:
        key: The exact key used when the memory was stored
    """
    try:
        result = _store.recall(key)
        if result is None:
            return json.dumps({"found": False, "key": key})
        return json.dumps({"found": True, **result})
    except Exception as exc:
        return json.dumps({"found": False, "error": str(exc)})


@mcp_app.tool()
def memory_search(query: str, limit: int = 20) -> str:
    """Full-text search across all stored memories.

    Searches key names, values, and tags using FTS5 (porter stemming).
    Returns results ranked by relevance. Searches warm tier first,
    then cold tier for older entries.

    Args:
        query: Search terms (FTS5 syntax supported, e.g., "deadline AND project")
        limit: Maximum results to return (default 20)
    """
    try:
        results = _store.search(query, limit=limit)
        return json.dumps({
            "results": results,
            "total_found": len(results),
            "query": query,
        }, default=str)
    except Exception as exc:
        return json.dumps({"results": [], "error": str(exc)})


@mcp_app.tool()
def memory_list(tag: str | None = None, limit: int = 50) -> str:
    """List stored memories, optionally filtered by tag.

    Returns memories ordered by most recently updated first.
    Only returns non-expired entries.

    Args:
        tag: Optional tag to filter by (e.g., "project", "deadline")
        limit: Maximum entries to return (default 50)
    """
    try:
        entries = _store.list_all(tag=tag, limit=limit)
        return json.dumps({
            "entries": entries,
            "count": len(entries),
            "filter_tag": tag,
        }, default=str)
    except Exception as exc:
        return json.dumps({"entries": [], "error": str(exc)})


@mcp_app.tool()
def memory_forget(key: str) -> str:
    """Delete a memory by key.

    Removes from both hot and warm tiers immediately. Cold tier
    summaries are not affected (they persist for historical context).

    Args:
        key: The exact key of the memory to delete
    """
    try:
        deleted = _store.forget(key)
        return json.dumps({"deleted": deleted, "key": key})
    except Exception as exc:
        return json.dumps({"deleted": False, "error": str(exc)})


def main() -> None:
    """Entry point for the MCP server (stdio transport)."""
    logger.info("ContextPulse Memory MCP server starting (data_dir=%s)", _data_dir)
    mcp_app.run()


if __name__ == "__main__":
    main()
