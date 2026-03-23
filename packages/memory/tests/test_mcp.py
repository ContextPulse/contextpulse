"""Tests for Memory MCP tools."""

import json
import os

import pytest

# Set memory dir before importing mcp_server to control where DB lands
@pytest.fixture(autouse=True)
def _set_memory_dir(tmp_path, monkeypatch):
    """Point MemoryStore at tmp_path for all tests, reset singleton."""
    monkeypatch.setenv("CONTEXTPULSE_MEMORY_DIR", str(tmp_path))
    # Reset the module-level singleton before each test
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
        assert result["memory"]["key"] == "test_key"

    def test_store_with_modality(self):
        result = json.loads(memory_store("voice_note", "hello", modality="voice"))
        assert result["memory"]["modality"] == "voice"

    def test_store_overwrite(self):
        memory_store("dup", "first")
        result = json.loads(memory_store("dup", "second"))
        assert result["memory"]["value"] == "second"


class TestMemoryRecallTool:
    def test_recall_existing(self):
        memory_store("recall_test", "found_me")
        result = json.loads(memory_recall("recall_test"))
        assert result["found"] is True
        assert result["memory"]["value"] == "found_me"

    def test_recall_nonexistent(self):
        result = json.loads(memory_recall("no_such_key"))
        assert result["found"] is False


class TestMemorySearchTool:
    def test_search_finds_match(self):
        memory_store("search_key", "unique_content_alpha")
        result = json.loads(memory_search("unique_content_alpha"))
        assert result["count"] >= 1

    def test_search_no_match(self):
        result = json.loads(memory_search("zzz_impossible_zzz"))
        assert result["count"] == 0

    def test_search_with_limit(self):
        for i in range(5):
            memory_store(f"batch_{i}", f"searchable data {i}")
        result = json.loads(memory_search("searchable", limit=2))
        assert result["count"] <= 2


class TestMemoryListTool:
    def test_list_all(self):
        memory_store("a", "1")
        memory_store("b", "2")
        result = json.loads(memory_list())
        assert result["count"] == 2

    def test_list_filter_modality(self):
        memory_store("sight_mem", "data", modality="sight")
        memory_store("voice_mem", "data", modality="voice")
        result = json.loads(memory_list(modality="sight"))
        assert result["count"] == 1
        assert result["memories"][0]["modality"] == "sight"

    def test_list_filter_tier(self):
        memory_store("hot_one", "data")
        result = json.loads(memory_list(tier="hot"))
        assert result["count"] == 1


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
