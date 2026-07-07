# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""migrate.py tests: fresh v0->v1, SchemaTooNew fail-closed, idempotent reopen."""

from __future__ import annotations

import sqlite3

import pytest
from contextpulse_knowledge.migrate import LATEST, SchemaTooNew, migrate


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_fresh_v0_to_v1() -> None:
    conn = _fresh_conn()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    result = migrate(conn)
    assert result == LATEST == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    # core tables exist
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for expected in (
        "observations",
        "entities",
        "facts",
        "fact_provenance",
        "vectors",
        "corrections",
        "purge_log",
        "ingest_state",
    ):
        assert expected in tables, f"missing table {expected}"
    conn.close()


def test_schema_too_new_fails_closed() -> None:
    conn = _fresh_conn()
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(SchemaTooNew):
        migrate(conn)
    conn.close()


def test_idempotent_reopen() -> None:
    conn = _fresh_conn()
    migrate(conn)
    v1 = conn.execute("PRAGMA user_version").fetchone()[0]
    # second call is a no-op (no version step to apply)
    result = migrate(conn)
    assert result == LATEST
    assert conn.execute("PRAGMA user_version").fetchone()[0] == v1
    # integrity still ok
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
