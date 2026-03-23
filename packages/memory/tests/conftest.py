"""Shared fixtures for contextpulse-memory tests."""

import pytest

from contextpulse_memory.storage import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Create a MemoryStore backed by a temporary directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()
