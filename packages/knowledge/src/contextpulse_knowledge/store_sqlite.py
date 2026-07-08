# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""store_sqlite — the imperative shell around cp_core (the referee).

Responsibilities:
  * open + migrate knowledge.db
  * the C2 idempotency PRE-CHECK (skip observe entirely if source_event_id
    already exists — BEFORE any plan_ingest)
  * apply(ChangeSet) transactionally, in list order
  * the queries (facts_about / context_at / timeline / search), delegating
    ordering + precedence to cp_core semantics
  * a numpy cosine fast path that agrees with cp_core's pure cosine within 1e-6
  * the permitted-mutation discipline (ForbiddenMutation on any non-sanctioned
    UPDATE; confidence non-decreasing; valid_to / retracted_at only NULL->value)

cp_core is imported for semantics; this module owns ALL I/O.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import threading
from typing import Iterable, Literal, Optional, Sequence

from . import cp_core as core
from .cp_core import (
    AddProvenance,
    AppendPurgeLog,
    ChangeSet,
    CloseValidity,
    ExistingFacts,
    Fact,
    IngestConfig,
    IngestState,
    InsertAlias,
    InsertCorrection,
    InsertEntity,
    InsertFact,
    InsertObservation,
    Observation,
    ProjectDef,
    PurgeFact,
    PurgeObservation,
    RetractFact,
    SetState,
    UpdateConfidence,
)
from .migrate import migrate

try:  # numpy is a hard dep, but keep the pure path usable in isolation
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


class ForbiddenMutation(RuntimeError):
    """Raised when a fact UPDATE violates the append-only mutation discipline."""


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------


def _pack_f32(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *[float(x) for x in vec])


def _unpack_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _content_hash(content: Optional[str]) -> Optional[bytes]:
    if not content:
        return None
    return hashlib.sha256(content.encode("utf-8")).digest()


# ---------------------------------------------------------------------------
# row -> Fact
# ---------------------------------------------------------------------------

_FACT_COLS = (
    "id, subject_id, predicate, object_entity_id, object_value, valid_from, "
    "valid_to, asserted_at, retracted_at, superseded_by, confidence, extraction, meta"
)


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        subject_id=row["subject_id"],
        predicate=row["predicate"],
        object_entity_id=row["object_entity_id"],
        object_value=row["object_value"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        asserted_at=row["asserted_at"],
        retracted_at=row["retracted_at"],
        superseded_by=row["superseded_by"],
        confidence=row["confidence"],
        extraction=row["extraction"],
        meta=json.loads(row["meta"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

MODEL_ID = "all-MiniLM-L6-v2@10244843"


class KnowledgeStore:
    def __init__(self, path: str = ":memory:", config: Optional[IngestConfig] = None):
        # check_same_thread=False: the bridge hands one store to a background ingest
        # thread (KnowledgeIngestor) while the daemon/backfill runs the catch-up on the
        # main thread. The _lock below serializes the write path so those never overlap
        # on the connection (SQLite requires the caller to serialize a shared connection).
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        migrate(self.conn)
        self.config = config or IngestConfig()

    def close(self) -> None:
        self.conn.close()

    # -- state --------------------------------------------------------------

    def _load_state(self) -> IngestState:
        row = self.conn.execute("SELECT value FROM ingest_state WHERE key='sessionizer'").fetchone()
        if not row:
            return IngestState()
        d = json.loads(row["value"])
        return IngestState(
            open_session_start=d.get("open_session_start"),
            open_session_last=d.get("open_session_last"),
            open_app_first_seen=tuple(tuple(x) for x in d.get("open_app_first_seen", [])),
            open_app_last_seen=tuple(tuple(x) for x in d.get("open_app_last_seen", [])),
            open_project_id=d.get("open_project_id"),
            open_project_valid_from=d.get("open_project_valid_from"),
        )

    def _save_state(self, state: IngestState) -> None:
        d = {
            "open_session_start": state.open_session_start,
            "open_session_last": state.open_session_last,
            "open_app_first_seen": [list(x) for x in state.open_app_first_seen],
            "open_app_last_seen": [list(x) for x in state.open_app_last_seen],
            "open_project_id": state.open_project_id,
            "open_project_valid_from": state.open_project_valid_from,
        }
        self.conn.execute(
            "INSERT INTO ingest_state(key, value) VALUES('sessionizer', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(d),),
        )

    # -- generic ingest_state accessors (bridge watermark, etc.) ------------

    def get_ingest_state(self, key: str) -> Optional[dict]:
        """Read a JSON ingest_state row by key (e.g. 'bridge_watermark'). None if absent."""
        with self._lock:
            row = self.conn.execute("SELECT value FROM ingest_state WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else None

    def set_ingest_state(self, key: str, value: dict) -> None:
        """Upsert a JSON ingest_state row. Autocommits (call outside an open txn)."""
        with self._lock:
            if self.conn.in_transaction:
                self.conn.commit()
            self.conn.execute(
                "INSERT INTO ingest_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    # -- alias snapshot for a type -----------------------------------------

    def _aliases_for_type(self, type_: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT a.alias, a.entity_id FROM entity_aliases a "
            "JOIN entities e ON e.id = a.entity_id WHERE e.type = ?",
            (type_,),
        ).fetchall()
        return {r["alias"]: r["entity_id"] for r in rows}

    # -- existing facts for fusion keys ------------------------------------

    def _fetch_existing(self, keys: Iterable[tuple[str, str]]) -> ExistingFacts:
        by_key: dict[tuple[str, str], tuple[Fact, ...]] = {}
        for subject_id, predicate in keys:
            rows = self.conn.execute(
                f"SELECT {_FACT_COLS} FROM facts "
                "WHERE subject_id=? AND predicate=? AND retracted_at IS NULL "
                "ORDER BY valid_from ASC, id ASC",
                (subject_id, predicate),
            ).fetchall()
            by_key[(subject_id, predicate)] = tuple(_row_to_fact(r) for r in rows)
        return ExistingFacts(by_key=by_key)

    # ======================================================================
    # NORMAL INGEST ENTRY POINT (harness routes every observe through here)
    # ======================================================================

    def observe(self, obs: Observation, now: Optional[int] = None) -> bool:
        """Ingest one observation. Returns True if ingested, False if skipped.

        C2 PRE-CHECK: if source_event_id already exists, skip ENTIRELY — no
        plan_ingest, no ChangeSet, no state update, no embedding enqueue.
        """
        if now is None:
            now = obs.observed_at
        # The C2 pre-check and the apply must be ONE atomic critical section: with a
        # concurrent ingest thread, a check-then-act gap would let the same event slip
        # past the pre-check twice and hit the ux_obs_event unique index. RLock serializes.
        with self._lock:
            # ---- C2 pre-check (before plan_ingest) -----------------------
            exists = self.conn.execute(
                "SELECT 1 FROM observations WHERE source_event_id=? LIMIT 1",
                (obs.source_event_id,),
            ).fetchone()
            if exists:
                return False

            state = self._load_state()
            keys = core.derive_fusion_keys(obs, state, self.config)
            existing = self._fetch_existing(keys)
            aliases = self._aliases_for_type("project")  # only project uses alias table in P1
            changeset, new_state = core.plan_ingest(obs, state, existing, aliases, self.config, now)
            # C-3: the ChangeSet + state write are ONE all-or-nothing transaction. A failure
            # (e.g. schema CHECK violation) rolls back the whole set so no partial observation
            # survives — otherwise the C2 pre-check would later skip a never-fully-ingested
            # event forever. Fail loud: re-raise after rollback.
            self._apply_transactional(changeset, new_state)
            self._enqueue_embeddings(obs, now)  # best-effort; never rolls back ingest
            return True

    def flush(self, now: int, force: bool = False) -> None:
        with self._lock:
            state = self._load_state()
            changeset, new_state = core.plan_flush(state, now, self.config, force=force)
            self._apply_transactional(changeset, new_state)  # C-3: atomic

    def correct_fact(
        self,
        *,
        subject_id: str,
        predicate: str,
        new_object_value: Optional[str] = None,
        new_object_entity_id: Optional[str] = None,
        object_value: Optional[str] = None,
        valid_from: Optional[int] = None,
        asserted_at: int,
    ) -> None:
        """Select exactly one non-retracted target and apply belief revision.

        Selection: subject_id + predicate + optional object_value / valid_from
        disambiguators. Must match EXACTLY one non-retracted fact (fail loud).
        """
        q = (
            f"SELECT {_FACT_COLS} FROM facts "
            "WHERE subject_id=? AND predicate=? AND retracted_at IS NULL"
        )
        args: list = [subject_id, predicate]
        if object_value is not None:
            q += " AND object_value=?"
            args.append(object_value)
        if valid_from is not None:
            q += " AND valid_from=?"
            args.append(valid_from)
        rows = self.conn.execute(q, args).fetchall()
        if len(rows) != 1:
            raise ValueError(
                f"correct_fact.select matched {len(rows)} facts for "
                f"{subject_id}/{predicate} (need exactly 1)"
            )
        target = _row_to_fact(rows[0])
        changeset = core.plan_correct_fact(
            target, new_object_entity_id, new_object_value, asserted_at
        )
        self._apply_transactional(changeset)  # C-3: atomic

    def purge_observation(self, source_event_id: str, now: int) -> None:
        row = self.conn.execute(
            "SELECT id FROM observations WHERE source_event_id=?",
            (source_event_id,),
        ).fetchone()
        if not row:
            return
        obs_id = row["id"]
        # facts whose ONLY provenance is this observation, non-user
        fact_rows = self.conn.execute(
            f"SELECT {_FACT_COLS} FROM facts f WHERE f.id IN ("
            "  SELECT fact_id FROM fact_provenance WHERE observation_id=?"
            ") AND f.extraction != 'user' AND ("
            "  SELECT COUNT(*) FROM fact_provenance p WHERE p.fact_id = f.id "
            "  AND p.observation_id != ?"
            ") = 0",
            (obs_id, obs_id),
        ).fetchall()
        orphans = [_row_to_fact(r) for r in fact_rows]
        vector_ids = self._observation_vector_ids(obs_id)
        changeset = core.plan_purge_observation(
            source_event_id, orphans, now, vector_item_ids=vector_ids
        )
        self._apply_transactional(changeset)  # C-3: atomic

    # ======================================================================
    # ChangeSet application (transactional; list order)
    # ======================================================================

    def _apply(self, changeset: ChangeSet) -> None:
        for op in changeset.ops:
            self._apply_op(op)

    def _apply_transactional(
        self, changeset: ChangeSet, new_state: Optional[IngestState] = None
    ) -> None:
        """C-3: apply a ChangeSet (+ optional state write) as ONE atomic transaction.

        BEGIN IMMEDIATE / COMMIT; on ANY exception ROLLBACK and re-raise (fail loud —
        never a bare-except swallow). A partial ChangeSet must leave zero rows so the
        C2 idempotency pre-check cannot later skip a never-fully-ingested event.
        """
        # Close any implicit transaction sqlite3 may have opened from prior reads so
        # our explicit BEGIN is the sole outer transaction. The lock (reentrant — the
        # observe/flush callers already hold it) serializes every write path across the
        # main and ingest threads that share this connection.
        with self._lock:
            if self.conn.in_transaction:
                self.conn.commit()
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._apply(changeset)
                if new_state is not None:
                    self._save_state(new_state)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _obs_id(self, source_event_id: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM observations WHERE source_event_id=?",
            (source_event_id,),
        ).fetchone()
        return row["id"] if row else None

    def _observation_vector_ids(self, obs_id: int) -> list[str]:
        """M-4: the vectors.item_id values that PurgeObservation will delete for this
        observation (its single 'observation' vector + any 'obs_chunk' vectors), so the
        purge can tombstone each one (§1.7 kind='vector')."""
        rows = self.conn.execute(
            "SELECT item_id FROM vectors "
            "WHERE (item_kind='observation' AND item_id=?) "
            "OR (item_kind='obs_chunk' AND item_id LIKE ?) "
            "ORDER BY item_id ASC",
            (str(obs_id), f"{obs_id}#%"),
        ).fetchall()
        return [r["item_id"] for r in rows]

    def _apply_op(self, op) -> None:
        if isinstance(op, InsertObservation):
            obs = op.obs
            # PLAIN INSERT (never OR IGNORE) — a bypassed C2 pre-check fails loud
            self.conn.execute(
                "INSERT INTO observations(source, source_event_id, kind, observed_at, "
                "session_id, app, window_title, url, content, content_hash, media_ref, meta) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    obs.source,
                    obs.source_event_id,
                    obs.kind,
                    obs.observed_at,
                    op.session_id,
                    obs.app,
                    obs.window_title,
                    obs.url,
                    obs.content,
                    _content_hash(obs.content),
                    obs.media_ref,
                    json.dumps(_merge_late(obs.meta, op.late)),
                ),
            )

        elif isinstance(op, InsertEntity):
            e = op.entity
            self.conn.execute(
                "INSERT OR IGNORE INTO entities(id, type, canonical_name, created_at, meta) "
                "VALUES(?,?,?,?,?)",
                (e.id, e.type, e.canonical_name, e.created_at, json.dumps(e.meta)),
            )

        elif isinstance(op, InsertAlias):
            self.conn.execute(
                "INSERT OR IGNORE INTO entity_aliases(entity_id, alias, source, created_at) "
                "VALUES(?,?,?,?)",
                (op.entity_id, op.alias, op.source, op.created_at),
            )

        elif isinstance(op, InsertFact):
            f = op.fact
            self.conn.execute(
                "INSERT INTO facts(id, subject_id, predicate, object_entity_id, object_value, "
                "valid_from, valid_to, asserted_at, retracted_at, superseded_by, confidence, "
                "extraction, meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f.id,
                    f.subject_id,
                    f.predicate,
                    f.object_entity_id,
                    f.object_value,
                    f.valid_from,
                    f.valid_to,
                    f.asserted_at,
                    f.retracted_at,
                    f.superseded_by,
                    f.confidence,
                    f.extraction,
                    json.dumps(f.meta),
                ),
            )

        elif isinstance(op, AddProvenance):
            obs_id = self._obs_id(op.source_event_id)
            if obs_id is None:
                raise ForbiddenMutation(
                    f"AddProvenance references unknown observation {op.source_event_id}"
                )
            self.conn.execute(
                "INSERT OR IGNORE INTO fact_provenance(fact_id, observation_id) VALUES(?,?)",
                (op.fact_id, obs_id),
            )

        elif isinstance(op, CloseValidity):
            self._guard_close_validity(op.fact_id, op.valid_to)
            self.conn.execute(
                "UPDATE facts SET valid_to=? WHERE id=? AND valid_to IS NULL",
                (op.valid_to, op.fact_id),
            )

        elif isinstance(op, UpdateConfidence):
            self._guard_confidence(op.fact_id, op.confidence)
            self.conn.execute(
                "UPDATE facts SET confidence=? WHERE id=?",
                (op.confidence, op.fact_id),
            )

        elif isinstance(op, RetractFact):
            self._guard_retract(op.fact_id)
            self.conn.execute(
                "UPDATE facts SET retracted_at=?, superseded_by=? "
                "WHERE id=? AND retracted_at IS NULL",
                (op.retracted_at, op.superseded_by, op.fact_id),
            )

        elif isinstance(op, InsertCorrection):
            obs_id = self._obs_id(op.source_event_id)
            self.conn.execute(
                "INSERT INTO corrections(original, corrected, detected_at, source, "
                "observation_id, applied) VALUES(?,?,?,?,?,0)",
                (op.original, op.corrected, op.detected_at, op.source, obs_id),
            )

        elif isinstance(op, PurgeObservation):
            obs_id = self._obs_id(op.source_event_id)
            if obs_id is None:
                return
            self.conn.execute("DELETE FROM fact_provenance WHERE observation_id=?", (obs_id,))
            self.conn.execute(
                "DELETE FROM vectors WHERE item_kind='observation' AND item_id=?",
                (str(obs_id),),
            )
            self.conn.execute(
                "DELETE FROM vectors WHERE item_kind='obs_chunk' AND item_id LIKE ?",
                (f"{obs_id}#%",),
            )
            self.conn.execute("DELETE FROM observations WHERE id=?", (obs_id,))

        elif isinstance(op, PurgeFact):
            self.conn.execute("DELETE FROM fact_provenance WHERE fact_id=?", (op.fact_id,))
            self.conn.execute("DELETE FROM facts WHERE id=?", (op.fact_id,))

        elif isinstance(op, AppendPurgeLog):
            self.conn.execute(
                "INSERT INTO purge_log(item_kind, item_id, purged_at) VALUES(?,?,?)",
                (op.item_kind, op.item_id, op.purged_at),
            )

        elif isinstance(op, SetState):
            self._save_state(op.state)

        else:  # pragma: no cover
            raise ForbiddenMutation(f"unknown ChangeSet op: {op!r}")

    # -- mutation guards (AT-6) ---------------------------------------------

    def _guard_close_validity(self, fact_id: str, valid_to: int) -> None:
        row = self.conn.execute(
            "SELECT valid_to, valid_from FROM facts WHERE id=?", (fact_id,)
        ).fetchone()
        if row is None:
            raise ForbiddenMutation(f"CloseValidity on missing fact {fact_id}")
        if row["valid_to"] is not None:
            raise ForbiddenMutation(
                f"CloseValidity: valid_to already set on {fact_id} (only NULL->value)"
            )
        if valid_to <= row["valid_from"]:
            raise ForbiddenMutation(
                f"CloseValidity: valid_to {valid_to} <= valid_from on {fact_id}"
            )

    def _guard_confidence(self, fact_id: str, new_conf: float) -> None:
        row = self.conn.execute("SELECT confidence FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            raise ForbiddenMutation(f"UpdateConfidence on missing fact {fact_id}")
        if new_conf + 1e-9 < row["confidence"]:
            raise ForbiddenMutation(
                f"confidence decrease {row['confidence']} -> {new_conf} on {fact_id}"
            )

    def _guard_retract(self, fact_id: str) -> None:
        row = self.conn.execute("SELECT retracted_at FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is None:
            raise ForbiddenMutation(f"RetractFact on missing fact {fact_id}")
        if row["retracted_at"] is not None:
            raise ForbiddenMutation(
                f"RetractFact: retracted_at already set on {fact_id} (only NULL->value)"
            )

    # ======================================================================
    # QUERIES (ordering + precedence delegated to cp_core semantics)
    # ======================================================================

    def _resolve_subject(self, subject: str) -> Optional[str]:
        """subject may be a canonical entity id OR a surface form."""
        row = self.conn.execute("SELECT id FROM entities WHERE id=?", (subject,)).fetchone()
        if row:
            return subject
        # alias table (any type)
        row = self.conn.execute(
            "SELECT entity_id FROM entity_aliases WHERE alias=?", (subject.casefold(),)
        ).fetchone()
        if row:
            return row["entity_id"]
        # project registry surface resolution
        resolved = core.resolve_entity("project", subject, {}, self.config)
        if resolved.entity_id:
            r = self.conn.execute(
                "SELECT id FROM entities WHERE id=?", (resolved.entity_id,)
            ).fetchone()
            if r:
                return resolved.entity_id
        return None

    def facts_about(
        self,
        subject: str,
        *,
        predicate: Optional[str] = None,
        at: Optional[int] = None,
        as_of: Optional[int] = None,
        min_confidence: float = 0.0,
        include_retracted: bool = False,
    ) -> list[Fact]:
        subject_id = self._resolve_subject(subject)
        if subject_id is None:
            return []
        q = f"SELECT {_FACT_COLS} FROM facts WHERE subject_id=?"
        args: list = [subject_id]
        if predicate is not None:
            q += " AND predicate=?"
            args.append(predicate)
        # M-3: `as_of` is an assertion-time filter that applies REGARDLESS of
        # `include_retracted` (§1.2 believed-as-of is asserted_at <= t). `include_retracted`
        # only lifts the retracted-exclusion clause — it must not leak facts asserted
        # AFTER as_of (a time-travel leak in the audit view).
        if as_of is not None:
            q += " AND asserted_at<=?"
            args.append(as_of)
        if not include_retracted:
            if as_of is None:
                q += " AND retracted_at IS NULL"
            else:
                q += " AND (retracted_at IS NULL OR retracted_at>?)"
                args.append(as_of)
        if at is not None:
            q += " AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
            args.extend([at, at])
        if min_confidence > 0.0:
            q += " AND confidence>=?"
            args.append(min_confidence)
        rows = self.conn.execute(q, args).fetchall()
        facts = [_row_to_fact(r) for r in rows]
        # Order: valid_from ASC, predicate ASC, id ASC
        facts.sort(key=lambda f: (f.valid_from, f.predicate, f.id))
        return facts

    def context_at(self, t: int, *, window_ms: int = 900_000, as_of: Optional[int] = None):
        preds = tuple(core.CONTEXT_PREDICATES)
        placeholders = ",".join("?" * len(preds))
        q = (
            f"SELECT {_FACT_COLS} FROM facts WHERE predicate IN ({placeholders}) "
            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
        )
        args: list = list(preds) + [t, t]
        if as_of is None:
            q += " AND retracted_at IS NULL"
        else:
            q += " AND asserted_at<=? AND (retracted_at IS NULL OR retracted_at>?)"
            args.extend([as_of, as_of])
        rows = self.conn.execute(q, args).fetchall()
        facts = [_row_to_fact(r) for r in rows]
        facts.sort(key=lambda f: (f.valid_from, f.predicate, f.id))

        session_fact = next((f for f in facts if f.predicate == "session.occurred"), None)
        session_id = session_fact.subject_id if session_fact else None

        apps = [f for f in facts if f.predicate == "session.used_app"]
        urls = []  # M7 deferral: always []

        # project: single winner by precedence (M2)
        project_candidates = [f for f in facts if f.predicate == "session.active_project"]
        project = _pick_project(project_candidates)

        # observation_ids: provenance of these facts with observed_at in [t-window, t+window]
        fact_ids = [f.id for f in facts]
        obs_ids: list[int] = []
        if fact_ids:
            ph = ",".join("?" * len(fact_ids))
            rows = self.conn.execute(
                f"SELECT DISTINCT o.id FROM observations o "
                f"JOIN fact_provenance p ON p.observation_id=o.id "
                f"WHERE p.fact_id IN ({ph}) AND o.observed_at>=? AND o.observed_at<=? "
                f"ORDER BY o.id ASC",
                fact_ids + [t - window_ms, t + window_ms],
            ).fetchall()
            obs_ids = [r["id"] for r in rows]

        return {
            "t": t,
            "session_id": session_id,
            "project": project,
            "apps": apps,
            "urls": urls,
            "facts": facts,
            "observation_ids": obs_ids,
        }

    def timeline(
        self,
        subject: str,
        *,
        predicate: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        as_of: Optional[int] = None,
    ) -> list[Fact]:
        subject_id = self._resolve_subject(subject)
        if subject_id is None:
            return []
        q = f"SELECT {_FACT_COLS} FROM facts WHERE subject_id=?"
        args: list = [subject_id]
        if predicate is not None:
            q += " AND predicate=?"
            args.append(predicate)
        if as_of is None:
            q += " AND retracted_at IS NULL"
        else:
            q += " AND asserted_at<=? AND (retracted_at IS NULL OR retracted_at>?)"
            args.extend([as_of, as_of])
        rows = self.conn.execute(q, args).fetchall()
        facts = [_row_to_fact(r) for r in rows]

        # overlap filter [since, until)
        def overlaps(f: Fact) -> bool:
            if until is not None and f.valid_from >= until:
                return False
            if since is not None and (f.valid_to is not None and f.valid_to <= since):
                return False
            return True

        facts = [f for f in facts if overlaps(f)]
        facts.sort(key=lambda f: (f.valid_from, f.id))
        return facts

    def search(
        self,
        query: str,
        *,
        k: int = 20,
        since: Optional[int] = None,
        until: Optional[int] = None,
        mode: Literal["hybrid", "fts", "vector"] = "hybrid",
        query_embedding: Optional[Sequence[float]] = None,
    ) -> list[dict]:
        fts_hits: list[core.RankedHit] = []
        vec_hits: list[core.ScoredHit] = []
        obs_meta: dict[str, dict] = {}  # source_event_id -> {observed_at, snippet, id}
        observed_at_by_item: dict[str, int] = {}

        # time filter clause on observations
        def _time_clause(alias: str) -> tuple[str, list]:
            clause = ""
            a: list = []
            if since is not None:
                clause += f" AND {alias}.observed_at>=?"
                a.append(since)
            if until is not None:
                clause += f" AND {alias}.observed_at<?"
                a.append(until)
            return clause, a

        if mode in ("hybrid", "fts"):
            tc, ta = _time_clause("o")
            rows = self.conn.execute(
                "SELECT o.id, o.source_event_id, o.observed_at, o.content "
                "FROM obs_fts f JOIN observations o ON o.id = f.rowid "
                "WHERE obs_fts MATCH ?"
                + tc
                # M-5: secondary key o.id makes bm25 ties a deterministic function of the
                # data (not FTS scan order) before the top-50 rank assignment.
                + " ORDER BY bm25(obs_fts) ASC, o.id ASC LIMIT 50",
                [query] + ta,
            ).fetchall()
            for rank, r in enumerate(rows, start=1):
                sid = r["source_event_id"]
                fts_hits.append(core.RankedHit(item_id=sid, rank=rank))
                obs_meta[sid] = {
                    "observed_at": r["observed_at"],
                    "snippet": (r["content"] or "")[:200],
                    "id": r["id"],
                }
                observed_at_by_item[sid] = r["observed_at"]

        if mode in ("hybrid", "vector") and query_embedding is not None:
            rows = self.conn.execute(
                "SELECT v.item_kind, v.item_id, v.embedding, o.source_event_id, "
                "o.observed_at, o.content, o.id AS oid "
                "FROM vectors v JOIN observations o ON "
                "  (v.item_kind='observation' AND o.id = CAST(v.item_id AS INTEGER)) OR "
                "  (v.item_kind='obs_chunk' AND o.id = CAST(substr(v.item_id,1,instr(v.item_id,'#')-1) AS INTEGER)) "
                "WHERE v.item_kind IN ('observation','obs_chunk')"
            ).fetchall()
            # collapse chunks to parent observation, keeping max score
            best: dict[str, core.ScoredHit] = {}
            for r in rows:
                if since is not None and r["observed_at"] < since:
                    continue
                if until is not None and r["observed_at"] >= until:
                    continue
                emb = _unpack_f32(r["embedding"])
                sim = self._cosine(query_embedding, emb)
                if sim < 0.25:
                    continue
                sid = r["source_event_id"]
                prev = best.get(sid)
                if prev is None or sim > prev.score:
                    best[sid] = core.ScoredHit(item_id=sid, score=sim, observed_at=r["observed_at"])
                obs_meta.setdefault(
                    sid,
                    {
                        "observed_at": r["observed_at"],
                        "snippet": (r["content"] or "")[:200],
                        "id": r["oid"],
                    },
                )
                observed_at_by_item[sid] = r["observed_at"]
            # M-1 + M-5: apply the spec's TOP-50 candidate cut (§1.8) BEFORE RRF, ordering
            # deterministically by (-score, observed_at DESC, item_id) so the cut and the
            # subsequent rank assignment are a pure function of the data.
            vec_hits = sorted(
                best.values(),
                key=lambda h: (-h.score, -h.observed_at, h.item_id),
            )[:50]

        if mode == "fts":
            vec_hits = []
        elif mode == "vector":
            fts_hits = []

        merged = core.rank_hybrid(fts_hits, vec_hits, k=60, observed_at_by_item=observed_at_by_item)
        out: list[dict] = []
        for hit in merged[:k]:
            meta = obs_meta.get(hit.item_id, {})
            out.append(
                {
                    "item_kind": "observation",
                    "item_id": meta.get("id"),
                    "source_event_id": hit.item_id,
                    "observed_at": meta.get("observed_at", hit.observed_at),
                    "score": hit.score,
                    "snippet": meta.get("snippet", ""),
                    "why": {"fts_rank": hit.fts_rank, "vec_rank": hit.vec_rank},
                }
            )
        return out

    # -- cosine fast path ---------------------------------------------------

    def _cosine(self, a: Sequence[float], b: Sequence[float]) -> float:
        if _np is not None and len(a) == len(b) and len(a) > 0:
            va = _np.asarray(a, dtype=_np.float64)
            vb = _np.asarray(b, dtype=_np.float64)
            na = float(_np.linalg.norm(va))
            nb = float(_np.linalg.norm(vb))
            if na == 0.0 or nb == 0.0:
                return 0.0
            return float(va.dot(vb) / (na * nb))
        return core.cosine(a, b)

    # ======================================================================
    # embeddings (obs only, M6) — used by harness injection + real path
    # ======================================================================

    def inject_observation_vector(self, source_event_id: str, embedding: Sequence[float]) -> None:
        """Test/adapter injection: store a verbatim vector, bypassing the
        embed_min_chars gate AND the chunker (format note 6)."""
        obs_id = self._obs_id(source_event_id)
        if obs_id is None:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO vectors(item_kind, item_id, model_id, dim, embedding, created_at) "
            "VALUES('observation', ?, ?, ?, ?, ?)",
            (str(obs_id), "injected", len(embedding), _pack_f32(embedding), 0),
        )
        self.conn.commit()

    def _enqueue_embeddings(self, obs: Observation, now: int) -> None:
        """Real embedding path stub: cp_core owns chunking; the model call is
        the adapter's I/O and is best-effort. Phase-1 conformance uses injected
        vectors, so this is a no-op unless a real engine is wired. Left as an
        explicit hook (never raises into ingest)."""
        return


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------


def _merge_late(meta: Optional[dict], late: bool) -> dict:
    d = dict(meta or {})
    if late:
        d["late"] = True
    return d


_PARTITION_RANK = {"user": 0}


def _partition_rank(extraction: str) -> int:
    if extraction == "user":
        return 0
    if extraction.startswith("llm:"):
        return 1
    return 2  # deterministic


def _pick_project(candidates: list[Fact]) -> Optional[Fact]:
    """M2 precedence: min of (partition_rank, -confidence, -valid_from, id)."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda f: (_partition_rank(f.extraction), -f.confidence, -f.valid_from, f.id),
    )


# ---------------------------------------------------------------------------
# config construction from vector JSON
# ---------------------------------------------------------------------------


def config_from_json(d: dict) -> IngestConfig:
    projects = tuple(
        ProjectDef(name=p["name"], aliases=tuple(p.get("aliases", [])))
        for p in d.get("projects", [])
    )
    return IngestConfig(
        session_gap_ms=d.get("session_gap_ms", 900_000),
        corroboration_factor=d.get("corroboration_factor", 0.25),
        confidence_cap=d.get("confidence_cap", 0.99),
        projects=projects,
        embed_min_chars=d.get("embed_min_chars", 40),
        chunk_chars=d.get("chunk_chars", 700),
        chunk_overlap=d.get("chunk_overlap", 80),
    )
