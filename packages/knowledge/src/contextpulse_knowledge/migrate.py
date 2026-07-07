# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Forward-only schema migration for the ContextPulse knowledge store.

Runs on every store open. Fail-closed on a newer DB (never open a schema this
build does not understand). Migrations are embedded in the package (schema.sql
read via importlib.resources).

DIVERGENCE LEDGER (binding notes for the future Rust port — mirrors schema.sql):
  D1: no vec_idx/vec0/sqlite-vec. Vectors = plain table + brute-force cosine
      (numpy fast path, pure-python reference). Rust port may reintroduce
      sqlite-vec behind the storage trait; semantics must match rank_hybrid.
  D2: external-content FTS5 sync triggers written explicitly (target omits).
  D3: obs_fts indexes (content, window_title, app) — parity with live events_fts.
  D4 (REVISED): observations.source_event_id UNIQUE ACROSS ALL SOURCES via
      ux_obs_event. source is a provenance annotation, NOT part of identity.
      Synthetic sources mint globally-unique ids. Bridge-era; review at P4.
  D5: this schema lives in its own knowledge.db, NOT activity.db (capture-path
      freeze; WAL writer isolation). Co-location decision deferred to P3/P5.
  D6: all timestamps INTEGER epoch MILLISECONDS UTC (events.timestamp is REAL
      seconds; bridge converts round(ts*1000), HALF-EVEN rounding — Rust port
      must use round_ties_even, never half-up).
  D7: corrections concretized; vec_meta folded into vectors.model_id.
  D8: no chunks table — chunks derive from observations via cp_core.chunk_text.
  BD-1: project aliases of length <= 3 match window titles on word boundaries
      (behavioral divergence from live ActiveProjectDetector containment).

Append-only with audited exceptions. The ONLY permitted UPDATEs:
  facts.valid_to      NULL -> t   (world changed)
  facts.retracted_at  NULL -> t   (belief revision)  + superseded_by NULL -> id
  facts.confidence    monotone non-decreasing via fusion (fuse is identity at cap)
The ONLY permitted DELETEs: the purge path (writes purge_log).
"""

from __future__ import annotations

import importlib.resources
import sqlite3

LATEST = 1


class SchemaTooNew(RuntimeError):
    """Raised when the DB's user_version exceeds this build's LATEST."""

    def __init__(self, found: int, latest: int) -> None:
        super().__init__(
            f"knowledge.db schema version {found} is newer than this build "
            f"supports ({latest}); refusing to open (fail closed)."
        )
        self.found = found
        self.latest = latest


def read_schema_sql() -> str:
    """Read the packaged schema.sql (importlib.resources — install-location safe)."""
    return (
        importlib.resources.files("contextpulse_knowledge")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )


def _migrations() -> dict[int, str]:
    return {1: read_schema_sql()}


def migrate(conn: sqlite3.Connection) -> int:
    """Forward-only migration. Returns the resulting schema version (LATEST).

    Each version step is applied with ``executescript`` (which manages its own
    transaction boundary) followed by the ``user_version`` bump; on any failure
    the whole step rolls back and the exception propagates (fail loud).
    """
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    if v > LATEST:
        raise SchemaTooNew(v, LATEST)
    migrations = _migrations()
    for target in range(v + 1, LATEST + 1):
        try:
            conn.executescript(migrations[target])
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert result == "ok", f"integrity_check failed: {result}"
    return LATEST
