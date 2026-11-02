"""Shared fixtures for contextpulse-memory tests."""

import pytest
from contextpulse_memory import embeddings as emb_module
from contextpulse_memory.storage import MemoryStore


@pytest.fixture(autouse=True)
def _no_real_embedding_model(tmp_path_factory, monkeypatch):
    """Keep every memory test off the network and out of the live model folder.

    ``MemoryStore.store()`` embeds each value through the module singleton, and
    the singleton's first use downloads the ~90 MB MiniLM model from Hugging Face
    into ``~/.contextpulse/models/minilm``. Without this fixture the first test to
    store anything (a) depends on the network -- CI run 40084365405 on 2026-10-24
    failed when that download hit the 30 s pytest timeout -- and (b) on a developer
    machine reads, and on a load failure deletes, the model files the running
    daemon uses.

    Each test gets a fresh engine whose model folder is a temp dir and whose
    download step refuses. The engine then reports unavailable and the store
    falls back to FTS, which is the behaviour these tests were written against.
    Tests that need embeddings still install ``engine_with_mock_model``, which
    replaces ``_instance`` after this fixture runs.
    """
    monkeypatch.setattr(emb_module, "_MODEL_DIR", tmp_path_factory.mktemp("minilm"))

    def _refuse_download(self: emb_module.EmbeddingEngine) -> None:
        raise RuntimeError("model download disabled under test")

    monkeypatch.setattr(emb_module.EmbeddingEngine, "_download_if_needed", _refuse_download)
    monkeypatch.setattr(emb_module, "_instance", emb_module.EmbeddingEngine())


@pytest.fixture
def store(tmp_path):
    """Create a MemoryStore backed by a temporary directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()
