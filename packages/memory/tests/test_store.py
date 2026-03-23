"""Tests for MemoryStore — all CRUD, search, tier promotion, and close."""

import json

import pytest

from contextpulse_memory.store import MemoryStore


class TestStore:
    """Test MemoryStore.store() method."""

    def test_store_new_key(self, store):
        result = store.store("greeting", "hello world")
        assert result["key"] == "greeting"
        assert result["value"] == "hello world"
        assert result["tier"] == "hot"
        assert result["access_count"] == 0

    def test_store_with_modality(self, store):
        result = store.store("note", "important", modality="voice")
        assert result["modality"] == "voice"

    def test_store_overwrites_existing(self, store):
        store.store("key1", "original")
        result = store.store("key1", "updated")
        assert result["value"] == "updated"

    def test_store_json_value(self, store):
        data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        result = store.store("complex", json.dumps(data))
        assert result["value"] == data  # parsed back from JSON

    def test_store_default_modality_empty(self, store):
        result = store.store("plain", "text")
        assert result["modality"] == ""


class TestRecall:
    """Test MemoryStore.recall() with access tracking."""

    def test_recall_existing_key(self, store):
        store.store("name", "David")
        result = store.recall("name")
        assert result is not None
        assert result["value"] == "David"
        assert result["access_count"] == 1

    def test_recall_nonexistent_key(self, store):
        result = store.recall("nonexistent")
        assert result is None

    def test_recall_increments_access_count(self, store):
        store.store("counter", "test")
        store.recall("counter")
        store.recall("counter")
        result = store.recall("counter")
        assert result["access_count"] == 3

    def test_recall_updates_accessed_at(self, store):
        store.store("ts_test", "value")
        first = store.recall("ts_test")
        import time
        time.sleep(0.01)
        second = store.recall("ts_test")
        assert second["accessed_at"] >= first["accessed_at"]


class TestTierPromotion:
    """Test automatic tier promotion based on access count."""

    def test_starts_as_hot(self, store):
        result = store.store("new_mem", "data")
        assert result["tier"] == "hot"

    def test_promotes_to_warm_at_threshold(self, store):
        store.store("popular", "data")
        store.recall("popular")  # access_count = 1
        store.recall("popular")  # access_count = 2
        result = store.recall("popular")  # access_count = 3 -> warm
        assert result["tier"] == "warm"
        assert result["access_count"] == 3

    def test_stays_warm_after_promotion(self, store):
        store.store("sticky", "data")
        for _ in range(5):
            store.recall("sticky")
        result = store.recall("sticky")
        assert result["tier"] == "warm"


class TestSearch:
    """Test MemoryStore.search() full-text search."""

    def test_search_by_key(self, populated_store):
        results = populated_store.search("user_name")
        assert len(results) >= 1
        keys = [r["key"] for r in results]
        assert "user_name" in keys

    def test_search_by_value(self, populated_store):
        results = populated_store.search("ContextPulse")
        assert len(results) >= 1

    def test_search_no_results(self, populated_store):
        results = populated_store.search("zzz_nonexistent_zzz")
        assert len(results) == 0

    def test_search_respects_limit(self, store):
        for i in range(10):
            store.store(f"item_{i}", f"searchable content {i}")
        results = store.search("searchable", limit=3)
        assert len(results) <= 3

    def test_search_empty_store(self, store):
        results = store.search("anything")
        assert results == []


class TestListMemories:
    """Test MemoryStore.list_memories() with filters."""

    def test_list_all(self, populated_store):
        results = populated_store.list_memories()
        assert len(results) == 3

    def test_list_by_modality(self, populated_store):
        results = populated_store.list_memories(modality="voice")
        assert len(results) == 1
        assert results[0]["key"] == "project_focus"

    def test_list_by_tier(self, store):
        store.store("hot_item", "data")
        # Promote one to warm
        store.store("warm_item", "data")
        for _ in range(3):
            store.recall("warm_item")

        hot = store.list_memories(tier="hot")
        warm = store.list_memories(tier="warm")
        assert len(hot) == 1
        assert len(warm) == 1

    def test_list_respects_limit(self, store):
        for i in range(10):
            store.store(f"mem_{i}", f"val_{i}")
        results = store.list_memories(limit=5)
        assert len(results) == 5

    def test_list_empty_store(self, store):
        results = store.list_memories()
        assert results == []


class TestForget:
    """Test MemoryStore.forget() deletion."""

    def test_forget_existing(self, store):
        store.store("to_delete", "data")
        assert store.forget("to_delete") is True
        assert store.recall("to_delete") is None

    def test_forget_nonexistent(self, store):
        assert store.forget("never_existed") is False

    def test_forget_removes_from_search(self, store):
        store.store("removable", "findable content")
        store.forget("removable")
        results = store.search("findable")
        assert len(results) == 0


class TestClose:
    """Test MemoryStore.close()."""

    def test_close_then_operations_fail(self, tmp_path):
        s = MemoryStore(tmp_path)
        s.store("key", "val")
        s.close()
        with pytest.raises(Exception):
            s.recall("key")

    def test_reopen_after_close(self, tmp_path):
        s = MemoryStore(tmp_path)
        s.store("persistent", "data")
        s.close()

        s2 = MemoryStore(tmp_path)
        result = s2.recall("persistent")
        assert result is not None
        assert result["value"] == "data"
        s2.close()
