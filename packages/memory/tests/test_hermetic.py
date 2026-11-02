"""The memory suite must not download the embedding model or touch the live model folder.

Regression for CI run 40084365405 (2026-10-24): a MemoryStore.store() call in
test_hybrid_search_falls_back_to_fts_when_unavailable reached the real Hugging Face
download and hit the 30 s timeout on windows-latest, leaving public main red.
"""

from pathlib import Path

from contextpulse_memory import embeddings as emb_module
from contextpulse_memory.storage import MemoryStore


def test_model_folder_is_not_the_live_one_under_test():
    live = Path.home() / ".contextpulse" / "models" / "minilm"
    assert emb_module._MODEL_DIR != live


def test_storing_a_value_does_not_load_a_real_model(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        store.store("k", "machine learning algorithms")
        engine = emb_module.get_engine()
        assert engine.is_available() is False
        assert engine.embed("anything") is None
    finally:
        store.close()
