"""Shared fixtures for contextpulse-memory tests."""

import pytest

from contextpulse_memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Create a MemoryStore backed by a temporary directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    """A MemoryStore pre-loaded with sample data."""
    store.store("user_name", "David", modality="system")
    store.store("project_focus", "ContextPulse is the priority", modality="voice")
    store.store("api_key_pattern", "Always use env vars for secrets", modality="sight")
    return store
