# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""AT-6 — mutation discipline + vector parity for store_sqlite.

- ForbiddenMutation on illegal UPDATEs
- valid_to / retracted_at only NULL->value
- confidence non-decreasing
- numpy cosine fast path agrees with cp_core pure cosine within 1e-6
"""

from __future__ import annotations

import pytest
from contextpulse_knowledge import cp_core as core
from contextpulse_knowledge.cp_core import IngestConfig, Observation
from contextpulse_knowledge.store_sqlite import (
    CloseValidity,
    ForbiddenMutation,
    KnowledgeStore,
    RetractFact,
    UpdateConfidence,
)


def _store() -> KnowledgeStore:
    return KnowledgeStore(":memory:", config=IngestConfig())


def _seed_used_app(store: KnowledgeStore):
    obs = Observation(
        source="test",
        source_event_id="e1",
        kind="ocr_result",
        observed_at=1000,
        app="Code.exe",
        window_title="a",
        content="alpha",
    )
    store.observe(obs)
    row = store.conn.execute(
        "SELECT id, valid_to, retracted_at, confidence, valid_from FROM facts "
        "WHERE predicate='session.used_app'"
    ).fetchone()
    return row["id"], row


def test_close_validity_only_null_to_value() -> None:
    store = _store()
    fid, _ = _seed_used_app(store)
    # first close is legal
    store._apply_op(CloseValidity(fid, 2000))
    store.conn.commit()
    # second close (valid_to already set) is forbidden
    with pytest.raises(ForbiddenMutation):
        store._apply_op(CloseValidity(fid, 3000))
    store.close()


def test_close_validity_rejects_le_valid_from() -> None:
    store = _store()
    fid, row = _seed_used_app(store)
    with pytest.raises(ForbiddenMutation):
        store._apply_op(CloseValidity(fid, row["valid_from"]))  # zero-width
    store.close()


def test_confidence_non_decreasing() -> None:
    store = _store()
    fid, row = _seed_used_app(store)
    base = row["confidence"]
    # increase is allowed
    store._apply_op(UpdateConfidence(fid, base + 0.01))
    store.conn.commit()
    # decrease is forbidden
    with pytest.raises(ForbiddenMutation):
        store._apply_op(UpdateConfidence(fid, base - 0.01))
    store.close()


def test_retract_only_null_to_value() -> None:
    store = _store()
    fid, _ = _seed_used_app(store)
    store._apply_op(RetractFact(fid, 5000, superseded_by=None))
    store.conn.commit()
    with pytest.raises(ForbiddenMutation):
        store._apply_op(RetractFact(fid, 6000, superseded_by=None))
    store.close()


def test_insert_observation_plain_insert_fails_loud_on_dup() -> None:
    """A bypassed C2 pre-check must fail the transaction (plain INSERT)."""
    store = _store()
    obs = Observation(
        source="test",
        source_event_id="dup",
        kind="ocr_result",
        observed_at=1000,
        app="Code.exe",
        window_title="a",
        content="x",
    )
    store.observe(obs)
    # bypass the pre-check by applying InsertObservation directly
    from contextpulse_knowledge.store_sqlite import InsertObservation

    with pytest.raises(Exception):
        store._apply_op(InsertObservation(obs=obs, session_id="session:1000", late=False))
        store.conn.commit()
    store.conn.rollback()
    store.close()


# --- vector parity (numpy vs pure) -----------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ([1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]),
        ([1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]),
        ([0.2, 0.5, -0.3, 0.8], [0.1, -0.4, 0.9, 0.2]),
        ([3.0, 4.0], [4.0, 3.0]),
        ([0.0, 0.0], [1.0, 1.0]),
    ],
)
def test_numpy_cosine_agrees_with_pure(a, b) -> None:
    store = _store()
    fast = store._cosine(a, b)
    pure = core.cosine(a, b)
    assert abs(fast - pure) <= 1e-6, f"cosine mismatch: fast={fast} pure={pure}"
    store.close()
