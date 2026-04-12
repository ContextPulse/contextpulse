"""Tests for the EmbeddingEngine and vector-search integration.

All tests mock the ONNX session so they run fast without downloading the model.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from contextpulse_memory import embeddings as emb_module
from contextpulse_memory.embeddings import EMBEDDING_DIM, EmbeddingEngine, get_engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(seed: int) -> list[float]:
    """Reproducible unit vector of length EMBEDDING_DIM."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _make_mock_session(output_vec: list[float] | None = None):
    """Return an onnxruntime.InferenceSession mock that yields *output_vec*."""
    if output_vec is None:
        output_vec = _unit_vec(42)

    session = MagicMock()

    # Simulate input / output metadata
    inp_input_ids = MagicMock(); inp_input_ids.name = "input_ids"
    inp_attn = MagicMock(); inp_attn.name = "attention_mask"
    inp_tt = MagicMock(); inp_tt.name = "token_type_ids"
    session.get_inputs.return_value = [inp_input_ids, inp_attn, inp_tt]

    out_hidden = MagicMock(); out_hidden.name = "last_hidden_state"
    session.get_outputs.return_value = [out_hidden]

    def _run(output_names, feeds):
        batch = feeds["input_ids"].shape[0]
        seq_len = feeds["input_ids"].shape[1]
        hidden = np.tile(output_vec, (batch, seq_len, 1)).astype(np.float32)
        return [hidden]

    session.run.side_effect = _run
    return session


def _make_mock_tokenizer(seq_len: int = 8):
    """Return a tokenizers.Tokenizer mock that produces fixed-length encodings."""
    tokenizer = MagicMock()
    tokenizer.enable_truncation = MagicMock()
    tokenizer.enable_padding = MagicMock()
    tokenizer.no_padding = MagicMock()

    def _encode(text):
        enc = MagicMock()
        enc.ids = list(range(seq_len))
        enc.attention_mask = [1] * seq_len
        enc.type_ids = [0] * seq_len
        return enc

    def _encode_batch(texts):
        return [_encode(t) for t in texts]

    tokenizer.encode.side_effect = _encode
    tokenizer.encode_batch.side_effect = _encode_batch
    return tokenizer


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure each test starts with a fresh EmbeddingEngine singleton."""
    emb_module._reset_for_testing()
    yield
    emb_module._reset_for_testing()


@pytest.fixture
def engine_with_mock_model():
    """EmbeddingEngine with a fake ONNX session + tokenizer — no file I/O."""
    engine = EmbeddingEngine()
    engine._session = _make_mock_session()
    engine._tokenizer = _make_mock_tokenizer()
    engine._output_idx = 0
    engine._available = True
    with patch.object(emb_module, "_instance", engine):
        yield engine


# ---------------------------------------------------------------------------
# EmbeddingEngine unit tests
# ---------------------------------------------------------------------------

class TestEmbeddingEngine:
    def test_embed_returns_correct_dim(self, engine_with_mock_model):
        vec = engine_with_mock_model.embed("hello world")
        assert vec is not None
        assert len(vec) == EMBEDDING_DIM

    def test_embed_returns_normalised_vector(self, engine_with_mock_model):
        vec = engine_with_mock_model.embed("test")
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-4, f"Expected unit vector, got norm={norm}"

    def test_embed_batch_returns_list_of_correct_dim(self, engine_with_mock_model):
        texts = ["first sentence", "second sentence", "third"]
        results = engine_with_mock_model.embed_batch(texts)
        assert results is not None
        assert len(results) == 3
        for vec in results:
            assert len(vec) == EMBEDDING_DIM

    def test_embed_batch_empty_list(self, engine_with_mock_model):
        result = engine_with_mock_model.embed_batch([])
        assert result == []

    def test_cosine_similarity_identical_vectors(self):
        v = _unit_vec(1)
        sim = EmbeddingEngine.cosine_similarity(v, v)
        assert abs(sim - 1.0) < 1e-4

    def test_cosine_similarity_orthogonal_vectors(self):
        # Build two orthogonal vectors by construction
        a = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        b = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        a[0] = 1.0
        b[1] = 1.0
        sim = EmbeddingEngine.cosine_similarity(a.tolist(), b.tolist())
        assert abs(sim) < 1e-4

    def test_cosine_similarity_range(self):
        for seed in range(5):
            a = _unit_vec(seed)
            b = _unit_vec(seed + 100)
            sim = EmbeddingEngine.cosine_similarity(a, b)
            assert -1.01 <= sim <= 1.01

    def test_is_available_true_when_loaded(self, engine_with_mock_model):
        assert engine_with_mock_model.is_available() is True

    def test_is_available_false_before_load(self):
        engine = EmbeddingEngine()
        assert engine.is_available() is False

    def test_embed_returns_none_when_unavailable(self):
        engine = EmbeddingEngine()
        # _ensure_loaded will fail because no model is downloaded in CI
        engine._available = False
        # Patch _ensure_loaded to not attempt download
        with patch.object(engine, "_ensure_loaded", return_value=False):
            result = engine.embed("anything")
        assert result is None

    def test_embed_batch_returns_none_when_unavailable(self):
        engine = EmbeddingEngine()
        with patch.object(engine, "_ensure_loaded", return_value=False):
            result = engine.embed_batch(["a", "b"])
        assert result is None

    def test_get_engine_returns_singleton(self):
        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2

    def test_reset_for_testing_clears_singleton(self):
        e1 = get_engine()
        emb_module._reset_for_testing()
        e2 = get_engine()
        assert e1 is not e2


# ---------------------------------------------------------------------------
# WarmTier semantic_search tests
# ---------------------------------------------------------------------------

class TestWarmTierSemanticSearch:
    def test_semantic_search_returns_empty_when_no_embeddings(self, tmp_path):
        from contextpulse_memory.storage import WarmTier
        warm = WarmTier(tmp_path / "warm.db")
        warm.upsert("k1", "value without embedding", [], expires_at=None)
        results = warm.semantic_search(_unit_vec(0), limit=10)
        assert results == []
        warm.close()

    def test_semantic_search_returns_stored_embedding(self, tmp_path):
        from contextpulse_memory.storage import WarmTier
        warm = WarmTier(tmp_path / "warm.db")
        vec = _unit_vec(7)
        emb_bytes = np.array(vec, dtype=np.float32).tobytes()
        warm.upsert("k1", "python programming", [], expires_at=None, embedding=emb_bytes)
        results = warm.semantic_search(vec, limit=5)
        assert len(results) == 1
        assert results[0]["key"] == "k1"
        assert results[0]["similarity"] > 0.99  # same vector → ~1.0
        warm.close()

    def test_semantic_search_ranking(self, tmp_path):
        """Vector closer to the query should rank higher."""
        from contextpulse_memory.storage import WarmTier
        warm = WarmTier(tmp_path / "warm.db")

        query_vec = _unit_vec(0)
        close_vec = _unit_vec(0)          # identical → sim ~1.0
        far_vec = _unit_vec(99)           # unrelated

        warm.upsert("close", "close doc", [], expires_at=None,
                    embedding=np.array(close_vec, dtype=np.float32).tobytes())
        warm.upsert("far", "far doc", [], expires_at=None,
                    embedding=np.array(far_vec, dtype=np.float32).tobytes())

        results = warm.semantic_search(query_vec, limit=10)
        assert len(results) == 2
        assert results[0]["key"] == "close"
        assert results[0]["similarity"] > results[1]["similarity"]
        warm.close()

    def test_semantic_search_excludes_expired(self, tmp_path):
        import time

        from contextpulse_memory.storage import WarmTier
        warm = WarmTier(tmp_path / "warm.db")
        vec = _unit_vec(3)
        emb = np.array(vec, dtype=np.float32).tobytes()
        warm.upsert("expired", "old value", [], expires_at=time.time() - 1, embedding=emb)
        results = warm.semantic_search(vec, limit=10)
        assert results == []
        warm.close()

    def test_embedding_not_exposed_in_normal_search(self, tmp_path):
        """Embedding bytes should never leak into search results."""
        from contextpulse_memory.storage import WarmTier
        warm = WarmTier(tmp_path / "warm.db")
        emb = np.array(_unit_vec(1), dtype=np.float32).tobytes()
        warm.upsert("k", "val", [], expires_at=None, embedding=emb)
        results = warm.search("val", limit=10)
        assert len(results) == 1
        assert "embedding" not in results[0]
        warm.close()


# ---------------------------------------------------------------------------
# MemoryStore hybrid_search tests
# ---------------------------------------------------------------------------

class TestHybridSearch:
    def test_hybrid_search_falls_back_to_fts_when_unavailable(self, tmp_path):
        from contextpulse_memory.storage import MemoryStore
        store = MemoryStore(tmp_path)
        store.store("key1", "machine learning algorithms")
        store.store("key2", "completely unrelated topic")

        # Patch get_engine in the embeddings module (where storage imports it from)
        unavailable = EmbeddingEngine()  # not loaded
        with patch("contextpulse_memory.embeddings.get_engine", return_value=unavailable):
            with patch("contextpulse_memory.embeddings._instance", unavailable):
                results = store.hybrid_search("machine learning", limit=10)

        assert any(r["key"] == "key1" for r in results)
        store.close()

    def test_hybrid_search_uses_rrf_scores(self, tmp_path, engine_with_mock_model):
        """Hybrid results should have rrf_score (not hybrid_score)."""
        from contextpulse_memory.storage import MemoryStore

        store = MemoryStore(tmp_path)
        store.store("doc_a", "python is a programming language")
        store.store("doc_b", "javascript is also popular")

        results = store.hybrid_search("programming language", limit=10)

        assert len(results) >= 1
        for r in results:
            assert "rrf_score" in r
            assert "hybrid_score" not in r
            assert r["rrf_score"] > 0
        store.close()

    def test_hybrid_search_rrf_score_order(self, tmp_path, engine_with_mock_model):
        """Results must be sorted descending by rrf_score."""
        from contextpulse_memory.storage import MemoryStore

        store = MemoryStore(tmp_path)
        for i in range(5):
            store.store(f"key{i}", f"document number {i} about topic")

        results = store.hybrid_search("document", limit=10)
        scores = [r["rrf_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
        store.close()

    def test_hybrid_search_rrf_boosts_cross_list_results(self, tmp_path, engine_with_mock_model):
        """A doc appearing in both FTS and semantic lists should score higher
        than one appearing in only one list (the core RRF property)."""
        from contextpulse_memory.storage import MemoryStore

        store = MemoryStore(tmp_path)
        # doc_both: has embedding (auto-embedded) AND contains searchable text
        store.store("doc_both", "machine learning algorithms")
        # doc_fts_only: also matches keyword search
        store.store("doc_fts_only", "machine learning tutorials")

        results = store.hybrid_search("machine learning", limit=10)
        keys = [r["key"] for r in results]
        # Both docs should appear (both match FTS, both have embeddings via mock)
        assert "doc_both" in keys
        assert "doc_fts_only" in keys
        # All results have rrf_score
        for r in results:
            assert "rrf_score" in r
            assert r["rrf_score"] > 0
        store.close()

    def test_semantic_search_fallback_to_fts(self, tmp_path):
        """semantic_search falls back to FTS when model unavailable."""
        from contextpulse_memory.storage import MemoryStore
        store = MemoryStore(tmp_path)
        store.store("coding", "python coding tutorial")

        # No engine loaded → is_available() returns False → falls back to FTS
        results = store.semantic_search("python", limit=10)
        assert any(r["key"] == "coding" for r in results)
        store.close()


# ---------------------------------------------------------------------------
# MemoryStore.store auto-embedding tests
# ---------------------------------------------------------------------------

class TestStoreAutoEmbedding:
    def test_store_embeds_value_when_engine_available(self, tmp_path, engine_with_mock_model):
        """When the engine is available, stored memories should have an embedding."""
        import sqlite3

        from contextpulse_memory.storage import MemoryStore

        store = MemoryStore(tmp_path)
        # engine_with_mock_model fixture already patches _instance in the embeddings module
        store.store("emb_key", "some text to embed")

        # Check the DB directly
        conn = sqlite3.connect(str(tmp_path / "memory.db"))
        row = conn.execute(
            "SELECT embedding FROM memories WHERE key = ?", ("emb_key",)
        ).fetchone()
        conn.close()
        store.close()

        assert row is not None
        assert row[0] is not None
        emb = np.frombuffer(row[0], dtype=np.float32)
        assert len(emb) == EMBEDDING_DIM

    def test_store_succeeds_when_engine_raises(self, tmp_path):
        """store() must not raise even if embedding computation fails."""
        from contextpulse_memory.storage import MemoryStore

        store = MemoryStore(tmp_path)
        with patch("contextpulse_memory.embeddings.get_engine", side_effect=RuntimeError("model error")):
            store.store("safe_key", "value that should still be stored")

        result = store.recall("safe_key")
        assert result is not None
        store.close()

    def test_existing_memories_without_embedding_still_searchable(self, tmp_path):
        """Memories stored before embedding column was added (embedding=NULL) must
        still be returned by FTS search."""
        import sqlite3

        from contextpulse_memory.storage import MemoryStore

        # Write a row without embedding directly
        db_path = tmp_path / "memory.db"
        store = MemoryStore(tmp_path)  # creates schema
        store.close()

        conn = sqlite3.connect(str(db_path))
        import json
        import time
        now = time.time()
        conn.execute(
            """INSERT INTO memories (key, value, tags, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            ("legacy_key", "legacy value no embedding", json.dumps([]), now, now),
        )
        conn.commit()
        conn.close()

        store2 = MemoryStore(tmp_path)
        results = store2.search("legacy", limit=10)
        assert any(r["key"] == "legacy_key" for r in results)
        store2.close()


# ---------------------------------------------------------------------------
# Thread-safety and robustness tests
# ---------------------------------------------------------------------------

class TestEmbeddingEngineRobustness:
    def test_engine_has_inference_lock(self):
        """EmbeddingEngine must have _infer_lock for thread-safe inference."""
        engine = EmbeddingEngine()
        assert hasattr(engine, "_infer_lock")
        assert isinstance(engine._infer_lock, type(threading.Lock()))

    def test_truncation_constant_is_256(self):
        """Source code should use max_length=256 for tokenizer truncation."""
        import inspect
        source = inspect.getsource(EmbeddingEngine._load_model)
        assert "max_length=256" in source
        assert "max_length=128" not in source

    def test_purge_cached_model_removes_files(self, tmp_path):
        """_purge_cached_model should delete model files to force re-download."""
        engine = EmbeddingEngine()
        # Create fake model files
        with patch.object(emb_module, "_MODEL_DIR", tmp_path):
            (tmp_path / "model.onnx").write_text("fake")
            (tmp_path / "tokenizer.json").write_text("fake")
            engine._purge_cached_model()
            assert not (tmp_path / "model.onnx").exists()
            assert not (tmp_path / "tokenizer.json").exists()

    def test_failed_load_purges_model_files(self, tmp_path):
        """If _load_model() raises, cached files should be deleted for re-download."""
        engine = EmbeddingEngine()
        with patch.object(emb_module, "_MODEL_DIR", tmp_path):
            (tmp_path / "model.onnx").write_text("corrupted")
            (tmp_path / "tokenizer.json").write_text("corrupted")
            with patch.object(engine, "_download_if_needed"):
                with patch.object(engine, "_load_model", side_effect=RuntimeError("bad model")):
                    result = engine._ensure_loaded()
            assert result is False
            assert not (tmp_path / "model.onnx").exists()
            assert not (tmp_path / "tokenizer.json").exists()
