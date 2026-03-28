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


# ---------------------------------------------------------------------------
# HotTier edge-case tests
# ---------------------------------------------------------------------------

class TestHotTierEdgeCases:
    def test_concurrent_put_get(self):
        """Verify thread safety under concurrent put/get."""
        import threading

        hot = HotTier(max_size=500)
        errors: list[str] = []

        def writer(start: int):
            for i in range(start, start + 50):
                hot.put(f"key_{i}", f"val_{i}", tags=["w"])

        def reader(start: int):
            for i in range(start, start + 50):
                result = hot.get(f"key_{i}")
                # Result may be None if not yet written — that's fine.
                if result is not None and result[0] != f"val_{i}":
                    errors.append(f"key_{i} had unexpected value {result[0]}")

        threads = []
        for batch in range(0, 200, 50):
            threads.append(threading.Thread(target=writer, args=(batch,)))
            threads.append(threading.Thread(target=reader, args=(batch,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread-safety violations: {errors}"

    def test_large_value(self):
        """Store and retrieve a 100KB string value."""
        hot = HotTier()
        big = "x" * 100_000
        hot.put("big", big, tags=["large"])
        result = hot.get("big")
        assert result is not None
        assert len(result[0]) == 100_000
        assert result[0] == big

    def test_special_chars_in_key(self):
        """Keys with spaces, unicode, and slashes."""
        hot = HotTier()
        keys = [
            "key with spaces",
            "key/with/slashes",
            "key\\backslash",
            "emoji_\U0001f680_rocket",
            "\u00e9\u00e8\u00ea_accents",
            "tab\there",
        ]
        for k in keys:
            hot.put(k, f"val_for_{k}", tags=[])
        for k in keys:
            result = hot.get(k)
            assert result is not None, f"Missing key: {k!r}"
            assert result[0] == f"val_for_{k}"

    def test_custom_ttl_respected(self):
        """Put with ttl=0.1, sleep 0.2, verify expired."""
        hot = HotTier()
        hot.put("fleeting", "gone_soon", tags=[], ttl=0.1)
        time.sleep(0.2)
        assert hot.get("fleeting") is None

    def test_lru_order_preserved(self):
        """Put 3 items, re-put the middle one, add 2 more past max_size=3.

        Re-putting the middle key moves it to the end of the OrderedDict,
        so eviction should remove the two oldest untouched keys instead.
        """
        hot = HotTier(max_size=3)
        hot.put("first", "1", tags=[])
        hot.put("middle", "2", tags=[])
        hot.put("last", "3", tags=[])

        # Re-put middle — moves it to end of OrderedDict
        hot.put("middle", "2_updated", tags=["refreshed"])

        # Add two more — should evict "first" and "last" (the two oldest)
        hot.put("extra1", "4", tags=[])
        hot.put("extra2", "5", tags=[])

        assert len(hot) == 3
        assert hot.get("middle") is not None, "middle should survive (recently put)"
        assert hot.get("middle")[0] == "2_updated"
        assert hot.get("first") is None, "first should have been evicted"


# ---------------------------------------------------------------------------
# WarmTier edge-case tests
# ---------------------------------------------------------------------------

class TestWarmTierEdgeCases:
    def test_search_fts_syntax_error_fallback(self, warm):
        """Invalid FTS query like 'AND OR' should fall back to LIKE search."""
        warm.upsert("note1", "some AND OR content here", tags=[], expires_at=None)
        # "AND OR" is invalid FTS5 syntax — should not raise, should fall back
        results = warm.search("AND OR")
        # Fallback LIKE should find it since the value contains "AND OR"
        assert len(results) >= 1
        assert results[0]["key"] == "note1"

    def test_large_payload_upsert(self, warm):
        """Upsert and retrieve a 100KB value."""
        big = "data_" * 20_000  # 100KB
        warm.upsert("bigval", big, tags=["big"], expires_at=None)
        result = warm.get("bigval")
        assert result is not None
        assert result["value"] == big
        assert len(result["value"]) == 100_000

    def test_modality_stored_and_retrievable(self, warm):
        """Upsert with modality='sight', verify it appears in the result."""
        warm.upsert("sight_mem", "screen data", tags=[], expires_at=None, modality="sight")
        result = warm.get("sight_mem")
        assert result is not None
        assert result["modality"] == "sight"

    def test_attention_score_stored(self, warm):
        """Upsert with attention_score=0.95, verify it persists."""
        warm.upsert(
            "important", "critical context", tags=[],
            expires_at=None, attention_score=0.95,
        )
        result = warm.get("important")
        assert result is not None
        assert abs(result["attention_score"] - 0.95) < 1e-6

    def test_list_with_limit(self, warm):
        """Insert 10 entries, list with limit=3, verify only 3 returned."""
        for i in range(10):
            warm.upsert(f"item_{i}", f"val_{i}", tags=[], expires_at=None)
        results = warm.list_all(limit=3)
        assert len(results) == 3

    def test_list_includes_expired_flag(self, warm):
        """include_expired=True should return expired entries too."""
        past = time.time() - 100
        warm.upsert("expired_entry", "old", tags=[], expires_at=past)
        warm.upsert("active_entry", "new", tags=[], expires_at=None)

        # Without flag — expired excluded
        normal = warm.list_all()
        normal_keys = [r["key"] for r in normal]
        assert "expired_entry" not in normal_keys

        # With flag — expired included
        with_expired = warm.list_all(include_expired=True)
        with_expired_keys = [r["key"] for r in with_expired]
        assert "expired_entry" in with_expired_keys
        assert "active_entry" in with_expired_keys


# ---------------------------------------------------------------------------
# ColdTier edge-case tests
# ---------------------------------------------------------------------------

class TestColdTierEdgeCases:
    def test_ingest_multiple_windows(self, cold):
        """Entries spanning 2 different 15-minute windows produce 2 summaries."""
        now = time.time()
        # Two entries 20 minutes apart — guaranteed different 15-min windows
        entries = [
            {"key": "early", "value": "morning data", "updated_at": now - 1200, "modality": None},
            {"key": "late", "value": "afternoon data", "updated_at": now, "modality": None},
        ]
        written = cold.ingest(entries)
        assert written == 2
        assert cold.count() == 2

    def test_ingest_replaces_same_window(self, cold):
        """Re-ingesting entries in the same window should UPDATE, not duplicate."""
        now = time.time()
        entries1 = [{"key": "k1", "value": "first pass", "updated_at": now, "modality": None}]
        entries2 = [{"key": "k2", "value": "second pass", "updated_at": now + 1, "modality": None}]

        cold.ingest(entries1)
        assert cold.count() == 1

        # Same 15-minute window — should replace via INSERT OR REPLACE
        cold.ingest(entries2)
        assert cold.count() == 1  # still 1 window

    def test_modalities_tracked(self, cold):
        """Verify the modalities field in the cold summary is populated."""
        now = time.time()
        entries = [
            {"key": "s1", "value": "screen", "updated_at": now, "modality": "sight"},
            {"key": "v1", "value": "voice", "updated_at": now + 1, "modality": "voice"},
        ]
        cold.ingest(entries)
        results = cold.search("screen")
        assert len(results) >= 1
        modalities_raw = results[0]["modalities"]
        import json
        modalities = json.loads(modalities_raw) if isinstance(modalities_raw, str) else modalities_raw
        assert "sight" in modalities
        assert "voice" in modalities

    def test_search_fts_syntax_fallback(self, cold):
        """Invalid FTS query falls back to LIKE search."""
        now = time.time()
        cold.ingest([{"key": "k", "value": "fallback AND OR test", "updated_at": now, "modality": None}])
        # "AND OR" is invalid FTS5 — should fall back to LIKE
        results = cold.search("AND OR")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# MemoryStore integration edge-case tests
# ---------------------------------------------------------------------------

class TestMemoryStoreEdgeCases:
    def test_store_permanent_no_expiry(self, mem):
        """ttl_hours=None stores permanently (no expires_at in warm)."""
        mem.store("forever", "never expires", ttl_hours=None)
        result = mem.warm.get("forever")
        assert result is not None
        assert result["expires_at"] is None
        assert result["value"] == "never expires"

    def test_search_spans_warm_and_cold(self, mem):
        """Put in warm, ingest to cold, search finds results from both tiers."""
        # Warm entry
        mem.store("warm_note", "deploy strategy warm")

        # Cold entry (bypass warm, go direct to cold)
        cold_entries = [
            {"key": "cold_note", "value": "deploy strategy cold", "updated_at": time.time(), "modality": None}
        ]
        mem.cold.ingest(cold_entries)

        results = mem.search("deploy strategy", limit=10)
        result_values = [r.get("value", r.get("text_content", "")) for r in results]
        has_warm = any("warm" in v for v in result_values)
        has_cold = any("cold" in v for v in result_values)
        assert has_warm, "warm-tier result missing from search"
        assert has_cold, "cold-tier result missing from search"

    def test_recall_falls_through_to_warm(self, mem):
        """After hot tier expires, recall should still find the warm-tier entry."""
        # Store with a very short hot TTL — store() uses min(ttl_hours*3600, 300)
        # but we can manipulate the hot tier directly
        mem.warm.upsert("fallthrough", "warm_value", tags=["t"], expires_at=None)
        # Do NOT put in hot — simulates hot miss
        result = mem.recall("fallthrough")
        assert result is not None
        assert result["value"] == "warm_value"

    def test_prune_removes_expired_entries(self, mem):
        """Store with short TTL, wait, prune, verify gone from warm."""
        past = time.time() - 10
        # Insert directly into warm with already-expired timestamp
        mem.warm.upsert("doomed", "bye", tags=[], expires_at=past)
        assert mem.warm.get("doomed") is not None  # get doesn't filter expired

        result = mem.prune()
        assert result["warm"] >= 1

        # Entry should be gone after prune
        assert mem.warm.count() == 0 or mem.warm.get("doomed") is None
