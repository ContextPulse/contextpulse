# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Numpy fast-path cosine vs the pure-python reference: must agree within 1e-6
(dossier v2 §1.8 / plan). The pure cp_core.cosine is the REFERENCE the Rust port
also validates against; store_sqlite._cosine is the numpy fast path used live."""

from __future__ import annotations

import math

import pytest
from contextpulse_knowledge import cp_core as core
from contextpulse_knowledge.store_sqlite import KnowledgeStore

# Deterministic vector pairs spanning identical / orthogonal / anti / mixed / zero.
_PAIRS = [
    ([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]),  # identical -> 1.0
    ([1.0, 0.0], [0.0, 1.0]),  # orthogonal -> 0.0
    ([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]),  # anti-parallel -> -1.0
    ([0.2, -0.5, 0.9, 0.1], [0.7, 0.7, -0.1, 0.3]),
    ([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]),  # zero vector -> 0.0 by convention
    ([1e-4, 2e-4, 3e-4], [3e-4, 2e-4, 1e-4]),  # tiny magnitudes
]


@pytest.mark.parametrize("a,b", _PAIRS, ids=[f"pair{i}" for i in range(len(_PAIRS))])
def test_numpy_cosine_agrees_with_pure(a, b):
    store = KnowledgeStore(":memory:")
    try:
        fast = store._cosine(a, b)
        ref = core.cosine(a, b)
        assert math.isclose(fast, ref, abs_tol=1e-6), f"{fast} vs {ref}"
    finally:
        store.close()


def test_pure_cosine_known_values():
    assert core.cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert core.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert core.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero norm guard
