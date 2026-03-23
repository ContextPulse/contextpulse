"""MCP stdio server exposing persistent memory tools to Claude Code.

Tools:
  memory_store  — store a key-value memory
  memory_recall — retrieve a memory by key (updates access stats)
  memory_search — full-text search across all memories
  memory_list   — list memories with optional filters
  memory_forget — delete a memory by key
"""

import json
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from contextpulse_memory.store import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.memory.mcp")

mcp_app = FastMCP("ContextPulse Memory")

# Default memory directory — can be overridden via environment variable
_DEFAULT_DIR = Path.home() / ".contextpulse" / "memory"
_store: MemoryStore | None = None


def _get_store() -> MemoryStore:
    """Lazy-init the MemoryStore singleton."""
    global _store
    if _store is None:
        import os
        db_dir = Path(os.environ.get("CONTEXTPULSE_MEMORY_DIR", str(_DEFAULT_DIR)))
        _store = MemoryStore(db_dir)
        logger.info("MemoryStore initialized at %s", db_dir)
    return _store


@mcp_app.tool()
def memory_store(key: str, value: str, modality: str = "") -> str:
    """Store a key-value memory. Overwrites if key already exists.

    Use this to persist insights, facts, preferences, or any context
    that should survive across sessions.
    """
    store = _get_store()
    result = store.store(key, value, modality=modality or None)
    return json.dumps({"success": True, "memory": result}, indent=2, default=str)


@mcp_app.tool()
def memory_recall(key: str) -> str:
    """Retrieve a memory by exact key. Updates access stats and may promote tier.

    Returns the memory dict if found, or a not-found message.
    """
    store = _get_store()
    result = store.recall(key)
    if result is None:
        return json.dumps({"found": False, "key": key})
    return json.dumps({"found": True, "memory": result}, indent=2, default=str)


@mcp_app.tool()
def memory_search(query: str, limit: int = 10) -> str:
    """Full-text search across all stored memories.

    Searches both keys and values. Returns up to `limit` results
    ranked by relevance.
    """
    store = _get_store()
    results = store.search(query, limit=limit)
    return json.dumps({"count": len(results), "results": results}, indent=2, default=str)


@mcp_app.tool()
def memory_list(modality: str = "", tier: str = "", limit: int = 50) -> str:
    """List stored memories with optional filters.

    Filter by modality (e.g. "sight", "voice") and/or tier ("hot", "warm", "cold").
    """
    store = _get_store()
    results = store.list_memories(
        modality=modality or None,
        tier=tier or None,
        limit=limit,
    )
    return json.dumps({"count": len(results), "memories": results}, indent=2, default=str)


@mcp_app.tool()
def memory_forget(key: str) -> str:
    """Delete a memory by key. Returns whether the deletion was successful."""
    store = _get_store()
    deleted = store.forget(key)
    return json.dumps({"success": deleted, "key": key})


def main():
    logger.info("Starting ContextPulse Memory MCP server")
    _get_store()  # Initialize eagerly
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
