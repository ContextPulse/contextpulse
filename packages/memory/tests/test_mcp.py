"""Tests for Memory MCP tools (new 3-tier API with tags + TTL)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _set_memory_dir(tmp_path, monkeypatch):
    """Point MemoryStore at tmp_path for all tests, reset singleton."""
    monkeypatch.setenv("CONTEXTPULSE_MEMORY_DIR", str(tmp_path))
    import contextpulse_memory.mcp_server as mod
    mod._store = None
    yield
    if mod._store is not None:
        mod._store.close()
        mod._store = None


from contextpulse_memory.mcp_server import (
    memory_forget,
    memory_list,
    memory_recall,
    memory_search,
    memory_store,
)


class TestMemoryStoreTool:
    def test_store_returns_success(self):
        result = json.loads(memory_store("test_key", "test_value"))
        assert result["success"] is True
        assert result["key"] == "test_key"

    def test_store_with_tags(self):
        result = json.loads(memory_store("tagged", "value", tags=["project", "urgent"]))
        assert result["success"] is True
        assert result["tags"] == ["project", "urgent"]

    def test_store_with_ttl(self):
        result = json.loads(memory_store("timed", "expires soon", ttl_hours=1.0))
        assert result["success"] is True
        assert result["ttl_hours"] == 1.0

    def test_store_permanent(self):
        result = json.loads(memory_store("permanent", "keeps forever", ttl_hours=0))
        assert result["success"] is True

    def test_store_overwrite(self):
        memory_store("dup", "first")
        result = json.loads(memory_store("dup", "second"))
        assert result["success"] is True


class TestMemoryRecallTool:
    def test_recall_existing(self):
        memory_store("recall_test", "found_me")
        result = json.loads(memory_recall("recall_test"))
        assert result["found"] is True
        assert result["value"] == "found_me"

    def test_recall_nonexistent(self):
        result = json.loads(memory_recall("no_such_key"))
        assert result["found"] is False

    def test_recall_overwritten_value(self):
        memory_store("changing", "first")
        memory_store("changing", "second")
        result = json.loads(memory_recall("changing"))
        assert result["found"] is True
        assert result["value"] == "second"


class TestMemorySearchTool:
    def test_search_finds_match(self):
        memory_store("search_key", "unique_content_alpha_xyz")
        result = json.loads(memory_search("alpha"))
        assert result["count"] >= 1

    def test_search_no_match(self):
        result = json.loads(memory_search("zzz_impossible_zzz_never"))
        assert result["count"] == 0

    def test_search_with_limit(self):
        for i in range(5):
            memory_store(f"batch_{i}", f"searchable data {i}")
        result = json.loads(memory_search("searchable", limit=2))
        assert result["count"] <= 2

    def test_search_by_key_name(self):
        memory_store("project_context", "ContextPulse is a memory engine")
        result = json.loads(memory_search("project"))
        assert result["count"] >= 1


class TestMemoryListTool:
    def test_list_all(self):
        memory_store("a", "1")
        memory_store("b", "2")
        result = json.loads(memory_list())
        assert result["count"] == 2

    def test_list_filter_by_tag(self):
        memory_store("sight_mem", "data", tags=["sight"])
        memory_store("voice_mem", "data", tags=["voice"])
        result = json.loads(memory_list(tag="sight"))
        assert result["count"] == 1

    def test_list_no_matches_for_unknown_tag(self):
        memory_store("plain", "no tags")
        result = json.loads(memory_list(tag="nonexistent_tag"))
        assert result["count"] == 0

    def test_list_with_limit(self):
        for i in range(10):
            memory_store(f"item_{i}", f"value_{i}")
        result = json.loads(memory_list(limit=3))
        assert result["count"] <= 3


class TestMemoryForgetTool:
    def test_forget_existing(self):
        memory_store("forget_me", "data")
        result = json.loads(memory_forget("forget_me"))
        assert result["success"] is True

    def test_forget_nonexistent(self):
        result = json.loads(memory_forget("never_stored"))
        assert result["success"] is False

    def test_forget_then_recall_fails(self):
        memory_store("ephemeral", "data")
        memory_forget("ephemeral")
        result = json.loads(memory_recall("ephemeral"))
        assert result["found"] is False

    def test_forget_returns_key(self):
        memory_store("key_to_forget", "value")
        result = json.loads(memory_forget("key_to_forget"))
        assert result["key"] == "key_to_forget"
