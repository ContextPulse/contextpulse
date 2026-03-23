"""Tests for three-tier MemoryStore: HotTier, WarmTier, ColdTier, MemoryStore."""

from __future__ import annotations

import time

import pytest

from contextpulse_memory.storage import ColdTier, HotTier, MemoryStore, WarmTier


# ---------------------------------------------------------------------------
# HotTier tests
# ---------------------------------------------------------------------------

class TestHotTier:
    def test_put_and_get(self):
        hot = HotTier()
        hot.put("k1", "v1", tags=["a"])
        result = hot.get("k1")
        assert result is not None
        assert result[0] == "v1"
        assert result[1] == ["a"]

    def test_get_missing_key(self):
        hot = HotTier()
        assert hot.get("missing") is None

    def test_delete(self):
        hot = HotTier()
        hot.put("del_me", "val", tags=[])
        assert hot.delete("del_me") is True
        assert hot.get("del_me") is None

    def test_delete_missing(self):
        hot = HotTier()
        assert hot.delete("missing") is False

    def test_expired_entry_returns_none(self):
        hot = HotTier()
        hot.put("short_lived", "val", tags=[], ttl=-1)  # already expired
        assert hot.get("short_lived") is None

    def test_overwrite_existing_key(self):
        hot = HotTier()
        hot.put("k", "first", tags=[])
        hot.put("k", "second", tags=["x"])
        result = hot.get("k")
        assert result[0] == "second"
        assert result[1] == ["x"]

    def test_len(self):
        hot = HotTier()
        hot.put("a", "1", tags=[])
        hot.put("b", "2", tags=[])
        assert len(hot) == 2

    def test_max_size_evicts_oldest(self):
        hot = HotTier(max_size=3)
        for i in range(5):
            hot.put(f"key_{i}", f"val_{i}", tags=[])
        assert len(hot) <= 3

    def test_keys_matching_tag(self):
        hot = HotTier()
        hot.put("k1", "v", tags=["project"])
        hot.put("k2", "v", tags=["other"])
        hot.put("k3", "v", tags=["project", "urgent"])
        tagged = hot.keys_matching_tag("project")
        assert set(tagged) == {"k1", "k3"}

    def test_evict_expired(self):
        hot = HotTier()
        hot.put("fresh", "v", tags=[], ttl=3600)
        hot.put("stale", "v", tags=[], ttl=-1)  # immediately expired
        count = hot.evict_expired()
        assert count >= 1
        assert hot.get("fresh") is not None


# ---------------------------------------------------------------------------
# WarmTier tests
# ---------------------------------------------------------------------------

@pytest.fixture
def warm(tmp_path):
    w = WarmTier(tmp_path / "warm.db")
    yield w
    w.close()


class TestWarmTier:
    def test_upsert_and_get(self, warm):
        warm.upsert("k1", "hello", tags=["a"], expires_at=None)
        result = warm.get("k1")
        assert result is not None
        assert result["key"] == "k1"
        assert result["value"] == "hello"
        assert "a" in result["tags"]

    def test_get_missing(self, warm):
        assert warm.get("missing") is None

    def test_upsert_overwrites(self, warm):
        warm.upsert("k", "first", tags=[], expires_at=None)
        warm.upsert("k", "second", tags=["x"], expires_at=None)
        result = warm.get("k")
        assert result["value"] == "second"
        assert "x" in result["tags"]

    def test_delete_existing(self, warm):
        warm.upsert("del", "v", tags=[], expires_at=None)
        assert warm.delete("del") is True
        assert warm.get("del") is None

    def test_delete_missing(self, warm):
        assert warm.delete("missing") is False

    def test_list_all(self, warm):
        warm.upsert("a", "1", tags=[], expires_at=None)
        warm.upsert("b", "2", tags=[], expires_at=None)
        results = warm.list_all()
        assert len(results) == 2

    def test_list_filter_by_tag(self, warm):
        warm.upsert("sight1", "data", tags=["sight"], expires_at=None)
        warm.upsert("voice1", "data", tags=["voice"], expires_at=None)
        results = warm.list_all(tag="sight")
        assert len(results) == 1
        assert "sight" in results[0]["tags"]

    def test_list_excludes_expired(self, warm):
        past = time.time() - 1
        warm.upsert("expired", "data", tags=[], expires_at=past)
        warm.upsert("active", "data", tags=[], expires_at=None)
        results = warm.list_all()
        keys = [r["key"] for r in results]
        assert "expired" not in keys
        assert "active" in keys

    def test_search_fts(self, warm):
        warm.upsert("note1", "connection refused error in prod", tags=[], expires_at=None)
        warm.upsert("note2", "all tests passing", tags=[], expires_at=None)
        results = warm.search("connection refused")
        assert len(results) >= 1
        assert results[0]["key"] == "note1"

    def test_search_no_match(self, warm):
        results = warm.search("zzz_no_match_zzz")
        assert len(results) == 0

    def test_prune_expired(self, warm):
        past = time.time() - 1
        warm.upsert("ex1", "v", tags=[], expires_at=past)
        warm.upsert("ex2", "v", tags=[], expires_at=past)
        warm.upsert("keep", "v", tags=[], expires_at=None)
        pruned = warm.prune_expired()
        assert pruned == 2
        assert warm.count() == 1

    def test_count(self, warm):
        assert warm.count() == 0
        warm.upsert("a", "1", tags=[], expires_at=None)
        assert warm.count() == 1

    def test_idempotent_schema(self, tmp_path):
        w1 = WarmTier(tmp_path / "warm2.db")
        w2 = WarmTier(tmp_path / "warm2.db")
        w1.close()
        w2.close()


# ---------------------------------------------------------------------------
# ColdTier tests
# ---------------------------------------------------------------------------

@pytest.fixture
def cold(tmp_path):
    c = ColdTier(tmp_path / "cold.db")
    yield c
    c.close()


class TestColdTier:
    def test_ingest_empty(self, cold):
        assert cold.ingest([]) == 0

    def test_ingest_entries(self, cold):
        entries = [
            {"key": "k1", "value": "hello world", "updated_at": time.time(), "modality": "sight"},
            {"key": "k2", "value": "voice note", "updated_at": time.time(), "modality": "voice"},
        ]
        count = cold.ingest(entries)
        assert count >= 1

    def test_search_after_ingest(self, cold):
        entries = [{"key": "meeting_notes", "value": "discussed deploy strategy", "updated_at": time.time(), "modality": None}]
        cold.ingest(entries)
        results = cold.search("deploy")
        assert len(results) >= 1

    def test_search_no_match(self, cold):
        cold.ingest([{"key": "k", "value": "something", "updated_at": time.time(), "modality": None}])
        results = cold.search("zzz_no_match")
        assert len(results) == 0

    def test_count(self, cold):
        assert cold.count() == 0
        cold.ingest([{"key": "a", "value": "v", "updated_at": time.time(), "modality": None}])
        assert cold.count() >= 1


# ---------------------------------------------------------------------------
# MemoryStore (orchestrator) tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mem(tmp_path):
    store = MemoryStore(tmp_path)
    yield store
    store.close()


class TestMemoryStore:
    def test_store_and_recall(self, mem):
        mem.store("greeting", "hello world")
        result = mem.recall("greeting")
        assert result is not None
        assert result["value"] == "hello world"

    def test_recall_hot_tier(self, mem):
        mem.store("quick", "fast access")
        result = mem.recall("quick")
        # Should hit hot tier (tier key present)
        assert result is not None
        assert result["value"] == "fast access"

    def test_recall_missing(self, mem):
        assert mem.recall("nothing_here") is None

    def test_store_with_tags(self, mem):
        mem.store("tagged_mem", "content", tags=["project", "urgent"])
        result = mem.recall("tagged_mem")
        assert result is not None

    def test_store_with_ttl(self, mem):
        mem.store("short", "value", ttl_hours=1.0)
        result = mem.recall("short")
        assert result is not None

    def test_forget(self, mem):
        mem.store("deletable", "value")
        assert mem.forget("deletable") is True
        assert mem.recall("deletable") is None

    def test_forget_missing(self, mem):
        assert mem.forget("ghost") is False

    def test_search(self, mem):
        mem.store("context_note", "ContextPulse is a memory engine for AI agents")
        results = mem.search("memory engine")
        assert len(results) >= 1

    def test_list_all(self, mem):
        mem.store("a", "1")
        mem.store("b", "2")
        mem.store("c", "3")
        entries = mem.list_all()
        assert len(entries) == 3

    def test_list_with_tag(self, mem):
        mem.store("tagged", "value", tags=["important"])
        mem.store("untagged", "value")
        results = mem.list_all(tag="important")
        assert len(results) == 1

    def test_prune(self, mem):
        # Prune should not crash even when nothing is expired
        result = mem.prune()
        assert "hot" in result
        assert "warm" in result

    def test_overwrite_same_key(self, mem):
        mem.store("k", "first")
        mem.store("k", "second")
        result = mem.recall("k")
        assert result["value"] == "second"

    def test_cross_modal_tagging(self, mem):
        mem.store("sight_event", "screen content", modality="sight", tags=["sight"])
        mem.store("voice_event", "spoken content", modality="voice", tags=["voice"])
        sight_results = mem.list_all(tag="sight")
        assert len(sight_results) == 1
