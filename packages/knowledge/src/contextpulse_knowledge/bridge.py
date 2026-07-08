# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""events -> observations BRIDGE (Phase 1, dossier v2 §4).

Two ingest paths, ONE deterministic mapping (:func:`event_to_observation`):

  * **Phase A — backfill** (:func:`backfill`): copy ``activity.db`` via the SQLite
    backup API (WAL-safe while the daemon writes), read the qualifying event types
    from the last N days in ``(timestamp, event_id)`` order, map each to an
    ``Observation`` and drive it through ``KnowledgeStore.observe`` (the C2
    idempotency pre-check makes re-runs a no-op), then ``flush(force=True)`` and
    persist a resume watermark.
  * **Phase B — live listener** (:class:`KnowledgeIngestor`): a passive EventBus
    listener that enqueues events and a single ingest thread that drains them in
    batches. Never blocks or raises into the capture emit path.

Plus a one-time **vocabulary-file import** (:func:`import_vocab`) — the PRIMARY
Phase-1 vocab source (Input B, §3.3), because ``correction_detected`` has never
fired live.

**Zero capture-path changes:** the bridge never writes ``activity.db`` and never
touches ``bus.py``; it only reads ``events`` read-only and registers a listener.
This module is the imperative shell — it may do I/O (cp_core stays pure).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Any, Optional

from .cp_core import IngestConfig, Observation, ProjectDef, slugify
from .store_sqlite import KnowledgeStore

logger = logging.getLogger("contextpulse.knowledge.bridge")

# The seven live event_types that qualify for the KG (dossier v2 §4.1.3, grounded
# against the live DB). ``vocab_import`` is minted by :func:`import_vocab`, NOT an
# event_type, so it is deliberately absent here.
QUALIFYING_EVENT_TYPES = frozenset(
    {
        "ocr_result",
        "transcription",
        "clipboard_change",
        "typing_burst",
        "session_lock",
        "session_unlock",
        "correction_detected",
    }
)

# Text payload keys, IN ORDER — mirrors contextpulse_core.spine.events._TEXT_PAYLOAD_KEYS
# so bridge content == ContextEvent.text_content() (§4.1.4). test_bridge asserts this
# stays in lock-step with the live tuple (drift guard) rather than importing a private name.
_TEXT_KEYS = ("ocr_text", "transcript", "text", "burst_text", "correction_text")


def default_activity_db() -> str:
    return os.environ.get("CONTEXTPULSE_ACTIVITY_DB", r"C:\Users\david\screenshots\activity.db")


def default_knowledge_db() -> str:
    env = os.environ.get("CONTEXTPULSE_KNOWLEDGE_DB")
    if env:
        return env
    return os.path.join(os.path.dirname(default_activity_db()), "knowledge.db")


def build_ingest_config(projects_root: Optional[str] = None) -> IngestConfig:
    """Snapshot ProjectRegistry.list_all() into IngestConfig.projects so the §3.1
    project extractor can match window titles. Falls back to no projects (project
    detection simply produces zero facts) if the registry is unavailable."""
    projects: tuple[ProjectDef, ...] = ()
    try:
        from pathlib import Path

        from contextpulse_project.registry import ProjectRegistry  # type: ignore

        reg = ProjectRegistry(Path(projects_root) if projects_root else None)
        projects = tuple(ProjectDef(name=p.name, aliases=tuple(p.aliases)) for p in reg.list_all())
    except Exception:  # pragma: no cover - registry-absent fallback
        logger.warning("project registry unavailable; project detection disabled", exc_info=True)
    return IngestConfig(projects=projects)


def default_vocab_path() -> str:
    """The learned-vocabulary file path (§3.3, m2). Imported lazily so a missing
    voice package never breaks bridge import; tests always pass an explicit path."""
    try:
        from contextpulse_voice.config import LEARNED_VOCAB_FILE  # type: ignore

        return str(LEARNED_VOCAB_FILE)
    except Exception:  # pragma: no cover - real-run fallback only
        return os.path.join(
            os.environ.get("APPDATA", ""), "ContextPulse", "voice", "vocabulary_learned.json"
        )


# ---------------------------------------------------------------------------
# mapping  (event -> Observation) — the single source of truth for both paths
# ---------------------------------------------------------------------------


def _join_text(payload: dict) -> str:
    """5-key text join == ContextEvent.text_content() (falsy values skipped)."""
    parts = [str(payload[k]) for k in _TEXT_KEYS if payload.get(k)]
    return " ".join(parts)


def event_to_observation(
    *,
    source: str,
    event_id: str,
    timestamp: float,
    event_type: str,
    app_name: str = "",
    window_title: str = "",
    monitor_index: int = 0,
    payload: Optional[dict] = None,
    modality: Optional[str] = None,
    correlation_id: Optional[str] = None,
    attention_score: float = 0.0,
    cognitive_load: float = 0.0,
) -> Optional[Observation]:
    """Map one event's fields to an ``Observation``. Returns None for non-qualifying
    types (the caller filters). Timestamps: ``observed_at = round(ts*1000)`` — Python
    ``round`` is half-even (D6); the Rust port MUST use ``round_ties_even``."""
    if event_type not in QUALIFYING_EVENT_TYPES:
        return None
    payload = payload or {}
    content = _join_text(payload)
    meta: dict[str, Any] = {
        "modality": modality,  # carried per §4.1.4 mapping table (provenance/debug)
        "monitor_index": monitor_index,
        "correlation_id": correlation_id,
        "attention_score": attention_score,
        "cognitive_load": cognitive_load,
    }
    # C1: correction_detected carries the vocab pair under the LIVE key names
    # (original_text/corrected_text — verified touch_module.py:174-182). Lift them
    # verbatim so the vocab extractor (which reads meta.original_text/corrected_text)
    # fires. There is NO original/corrected rename layer.
    if event_type == "correction_detected":
        if payload.get("original_text") and payload.get("corrected_text"):
            meta["original_text"] = payload["original_text"]
            meta["corrected_text"] = payload["corrected_text"]
        else:
            # Fail loud on malformed input — never silently drop (bare_except_swallow
            # lesson). The observation still records; it just yields no vocab fact.
            logger.warning(
                "correction_detected %s missing original_text/corrected_text keys; "
                "no vocab fact will derive",
                event_id,
            )
    return Observation(
        source=source,
        source_event_id=event_id,
        kind=event_type,
        observed_at=round(timestamp * 1000),
        app=app_name or "",
        window_title=window_title or "",
        url=None,  # M7: no live event carries $.url
        content=content or None,
        media_ref=None,
        meta=meta,
    )


def observation_from_row(row: Any, source: str) -> Optional[Observation]:
    """Map a sqlite ``events`` row (sqlite3.Row or mapping) to an Observation."""
    payload_raw = row["payload"]
    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    except (TypeError, ValueError):
        # Fail loud, never silent (bare_except_swallow) — the observation still records
        # with empty content so count parity holds, but the corruption is visible.
        logger.warning("event %s has malformed payload JSON; treating as empty", row["event_id"])
        payload = {}
    return event_to_observation(
        source=source,
        event_id=row["event_id"],
        timestamp=row["timestamp"],
        event_type=row["event_type"],
        app_name=row["app_name"] or "",
        window_title=row["window_title"] or "",
        monitor_index=row["monitor_index"] if row["monitor_index"] is not None else 0,
        payload=payload,
        modality=row["modality"],
        correlation_id=row["correlation_id"],
        attention_score=row["attention_score"] or 0.0,
        cognitive_load=row["cognitive_load"] or 0.0,
    )


def observation_from_event(event: Any, source: str) -> Optional[Observation]:
    """Map a live ``ContextEvent`` (from the bus) to an Observation."""
    return event_to_observation(
        source=source,
        event_id=event.event_id,
        timestamp=event.timestamp,
        event_type=getattr(event.event_type, "value", event.event_type),
        app_name=event.app_name,
        window_title=event.window_title,
        monitor_index=event.monitor_index,
        payload=dict(event.payload or {}),
        modality=getattr(event.modality, "value", event.modality),
        correlation_id=event.correlation_id,
        attention_score=event.attention_score,
        cognitive_load=event.cognitive_load,
    )


# ---------------------------------------------------------------------------
# Phase A — backfill against a COPY
# ---------------------------------------------------------------------------

_EVENT_COLS = (
    "event_id, timestamp, event_type, modality, app_name, window_title, monitor_index, "
    "payload, correlation_id, attention_score, cognitive_load"
)


def copy_activity_db(source_path: str, dest_path: str) -> None:
    """WAL-safe snapshot via the SQLite backup API (never a file copy — the daemon
    may be mid-write; a file copy would miss uncommitted -wal rows or tear)."""
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _qualifying_count(conn: sqlite3.Connection, cutoff_ts: float) -> int:
    types = sorted(QUALIFYING_EVENT_TYPES)
    ph = ",".join("?" * len(types))
    row = conn.execute(
        f"SELECT count(*) FROM events WHERE timestamp >= ? AND event_type IN ({ph})",
        [cutoff_ts] + types,
    ).fetchone()
    return row[0]


def backfill(
    source_db: str,
    store: KnowledgeStore,
    *,
    since_days: float = 30.0,
    now_s: Optional[float] = None,
    batch_size: int = 500,
    do_copy: bool = True,
) -> dict:
    """Backfill the KG from ``source_db``'s ``events`` (last ``since_days`` days).

    Copies the source (WAL-safe) unless ``do_copy=False`` (tests pass a stable
    synthetic file). Ingests in ``(timestamp, event_id)`` order through the store's
    C2-guarded ``observe`` (so a re-run ingests 0 new rows — AT-5). Persists a resume
    watermark carrying the REAL seconds verbatim (never reconstructed from ms — m1).

    Returns a stats dict incl. ``qualifying_events`` and ``obs_bridge`` so ``--verify``
    (AT-1) can assert equality.
    """
    now_s = now_s if now_s is not None else time.time()
    cutoff_ts = now_s - since_days * 86400.0

    ingested = skipped = qualifying = 0
    last_ts: Optional[float] = None
    last_eid: Optional[str] = None
    last_observed_ms: Optional[int] = None

    scratch: Optional[str] = None
    if do_copy:
        fd, scratch = tempfile.mkstemp(suffix=".kgbackfill.db")
        os.close(fd)
    try:
        if do_copy:
            copy_activity_db(source_db, scratch)  # may raise; scratch removed in finally
            read_path = scratch
        else:
            read_path = source_db

        conn = sqlite3.connect(f"file:{read_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            qualifying = _qualifying_count(conn, cutoff_ts)
            types = sorted(QUALIFYING_EVENT_TYPES)
            ph = ",".join("?" * len(types))
            cur = conn.execute(
                f"SELECT {_EVENT_COLS} FROM events "
                f"WHERE timestamp >= ? AND event_type IN ({ph}) "
                "ORDER BY timestamp ASC, event_id ASC",
                [cutoff_ts] + types,
            )
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    obs = observation_from_row(row, "bridge:events")
                    if obs is None:
                        continue
                    if store.observe(obs):
                        ingested += 1
                    else:
                        skipped += 1
                    last_ts = row["timestamp"]
                    last_eid = row["event_id"]
                    last_observed_ms = obs.observed_at
            if last_observed_ms is not None:
                store.flush(now=last_observed_ms, force=True)
        finally:
            conn.close()
    finally:
        if scratch and os.path.exists(scratch):
            try:
                os.remove(scratch)
            except OSError:  # pragma: no cover - windows lock, best-effort cleanup
                logger.warning("could not remove scratch copy %s", scratch)

    if last_ts is not None:
        # m1: store the ORIGINAL REAL seconds + event_id — the exact resume predicate,
        # never reconstructed from ms (lossy across the microsecond boundary).
        store.set_ingest_state(
            "bridge_watermark", {"last_timestamp": last_ts, "last_event_id": last_eid}
        )

    obs_bridge = store.conn.execute(
        "SELECT count(*) FROM observations WHERE source='bridge:events'"
    ).fetchone()[0]

    stats = {
        "qualifying_events": qualifying,
        "ingested": ingested,
        "skipped": skipped,
        "obs_bridge": obs_bridge,
        "last_timestamp": last_ts,
        "last_event_id": last_eid,
    }
    logger.info("backfill: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# vocab-file import (Input B, §3.3) — the PRIMARY Phase-1 vocab source
# ---------------------------------------------------------------------------


def import_vocab(store: KnowledgeStore, vocab_path: Optional[str] = None) -> dict:
    """Import ``vocabulary_learned.json`` ({original_lower: corrected}) as synthetic
    ``vocab_import`` observations. Idempotent (source_event_id = 'vocab-import:<slug>'
    + C2 skip). Returns stats incl. ``entries`` and ``vocab_facts`` for AT-8."""
    path = vocab_path or default_vocab_path()
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"vocabulary file {path} is not a JSON object")

    observed_ms = round(os.path.getmtime(path) * 1000)
    ingested = skipped = 0
    for original, corrected in sorted(data.items()):
        if not original or not corrected:
            continue
        obs = Observation(
            source="bridge:vocab_file",
            source_event_id=f"vocab-import:{slugify(original)}",
            kind="vocab_import",
            observed_at=observed_ms,
            content=f"{original} -> {corrected}",
            meta={"original_text": original, "corrected_text": corrected},
        )
        if store.observe(obs):
            ingested += 1
        else:
            skipped += 1

    vocab_facts = store.conn.execute(
        "SELECT count(*) FROM facts WHERE predicate='vocab.corrects_to' AND retracted_at IS NULL"
    ).fetchone()[0]
    stats = {
        "entries": len(data),
        "ingested": ingested,
        "skipped": skipped,
        "vocab_facts": vocab_facts,
    }
    logger.info("import_vocab: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Phase B — live EventBus listener
# ---------------------------------------------------------------------------


class KnowledgeIngestor:
    """Passive EventBus listener + single-thread batch ingester (dossier v2 §4.2).

    The listener body is enqueue-only and never raises into the capture emit path.
    A dedicated thread drains the queue in batches, maps qualifying events, and drives
    them through the store's C2-guarded ``observe``. Startup does a watermark catch-up
    (read-only, own connection) BEFORE draining so no event is missed or double-counted
    (overlap is absorbed by the C2 skip rule; the ``ux_obs_event`` index is the loud
    DB-level backstop). Ingest-thread failures dead-letter the batch and continue —
    capture is never impacted.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        activity_db: Optional[str] = None,
        dead_letter_path: Optional[str] = None,
        batch_max: int = 200,
        tick_s: float = 2.0,
    ) -> None:
        self.store = store
        self.activity_db = activity_db or default_activity_db()
        self.dead_letter_path = dead_letter_path
        self.batch_max = batch_max
        self.tick_s = tick_s
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- listener (runs on the emit thread; enqueue-only) -------------------

    def attach(self, bus: Any) -> None:
        bus.on(self._enqueue)

    def _enqueue(self, event: Any) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:  # pragma: no cover - queue is unbounded; guard anyway
            logger.exception("knowledge ingest enqueue failed (dropping event)")

    # -- thread lifecycle --------------------------------------------------

    def start(self, catch_up: bool = True, now_s: Optional[float] = None) -> None:
        if catch_up:
            try:
                self.catch_up(now_s=now_s)
            except Exception:
                logger.exception("knowledge catch-up failed (continuing to live drain)")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cp-knowledge-ingest", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.drain_once(block_timeout=self.tick_s)

    # -- drain (testable without a thread) ---------------------------------

    def drain_once(self, block_timeout: float = 0.0) -> int:
        """Drain up to ``batch_max`` queued events and ingest them. Returns the number
        ingested. If the queue is idle, run an idle flush so sessions close on time."""
        batch: list = []
        try:
            first = (
                self._queue.get(timeout=block_timeout)
                if block_timeout
                else self._queue.get_nowait()
            )
            batch.append(first)
        except queue.Empty:
            self._idle_flush()
            return 0
        while len(batch) < self.batch_max:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return self._ingest_events(batch, "live:eventbus")

    def _ingest_events(self, events: list, source: str) -> int:
        # sort each batch by (timestamp, event_id) so sessionization is order-correct
        events = sorted(events, key=lambda e: (e.timestamp, e.event_id))
        ingested = 0
        try:
            for event in events:
                obs = observation_from_event(event, source)
                if obs is None:
                    continue
                if self.store.observe(obs):
                    ingested += 1
        except Exception:
            # Do NOT advance the watermark on failure — a restart's catch-up must re-read
            # (idempotent via C2) rather than skip a never-ingested event.
            logger.exception("knowledge ingest batch failed; dead-lettering %d events", len(events))
            self._dead_letter(events)
            return ingested
        # M4: advance the watermark past this drained batch so a restart's catch-up doesn't
        # rescan already-seen events. Monotonic; C2 keeps correctness if it ever regresses.
        if events:
            self._advance_watermark(events[-1].timestamp, events[-1].event_id)
        return ingested

    def _advance_watermark(self, ts: float, eid: str) -> None:
        cur = self.store.get_ingest_state("bridge_watermark")
        if cur and cur.get("last_timestamp") is not None:
            if (cur["last_timestamp"], cur.get("last_event_id") or "") >= (ts, eid or ""):
                return  # never regress the watermark
        self.store.set_ingest_state(
            "bridge_watermark", {"last_timestamp": ts, "last_event_id": eid}
        )

    def _idle_flush(self) -> None:
        state = self.store._load_state()
        if state.open_session_start is None:
            return
        last = state.open_session_last or state.open_session_start
        now_ms = round(time.time() * 1000)
        if now_ms - last >= self.store.config.session_gap_ms:
            try:
                self.store.flush(now=now_ms, force=False)
            except Exception:
                logger.exception("knowledge idle flush failed")

    def _dead_letter(self, events: list) -> None:
        if not self.dead_letter_path:
            return
        try:
            with open(self.dead_letter_path, "a", encoding="utf-8") as fh:
                for e in events:
                    fh.write(
                        json.dumps(
                            {
                                "event_id": e.event_id,
                                "timestamp": e.timestamp,
                                "event_type": getattr(e.event_type, "value", e.event_type),
                            }
                        )
                        + "\n"
                    )
        except Exception:  # pragma: no cover - dead-letter is best-effort
            logger.exception("dead-letter write failed")

    # -- catch-up from watermark (read-only, own connection) ---------------

    def catch_up(self, now_s: Optional[float] = None) -> int:
        wm = self.store.get_ingest_state("bridge_watermark")
        ts = wm.get("last_timestamp") if wm else None
        eid = wm.get("last_event_id") if wm else None
        conn = sqlite3.connect(f"file:{self.activity_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            types = sorted(QUALIFYING_EVENT_TYPES)
            ph = ",".join("?" * len(types))
            if ts is None:
                cur = conn.execute(
                    f"SELECT {_EVENT_COLS} FROM events WHERE event_type IN ({ph}) "
                    "ORDER BY timestamp ASC, event_id ASC",
                    types,
                )
            else:
                # m1 resume predicate: strictly after (ts, eid), no ms reconstruction,
                # no skipped same-second events, no wasteful re-reads.
                cur = conn.execute(
                    f"SELECT {_EVENT_COLS} FROM events "
                    f"WHERE ((timestamp > ?) OR (timestamp = ? AND event_id > ?)) "
                    f"AND event_type IN ({ph}) "
                    "ORDER BY timestamp ASC, event_id ASC",
                    [ts, ts, eid] + types,
                )
            ingested = 0
            last_ts = ts
            last_eid = eid
            for row in cur:
                obs = observation_from_row(row, "live:eventbus")
                if obs is None:
                    continue
                if self.store.observe(obs):
                    ingested += 1
                last_ts = row["timestamp"]
                last_eid = row["event_id"]
        finally:
            conn.close()
        if last_ts is not None:
            self.store.set_ingest_state(
                "bridge_watermark", {"last_timestamp": last_ts, "last_event_id": last_eid}
            )
        logger.info("knowledge catch-up ingested %d events", ingested)
        return ingested


# ---------------------------------------------------------------------------
# spot-check (AT-2) — human-eyeballed provenance printout
# ---------------------------------------------------------------------------


def spot_check(store: KnowledgeStore, at_ms: Optional[int] = None) -> None:
    """Print real, provenance-linked KG answers so David can eyeball them (AT-2, DAH).

    NOTE: ``facts_about`` is SUBJECT-scoped (validated by the conformance vectors) and a
    project entity is only ever a fact OBJECT in Phase 1 — so project recall is via
    ``context_at`` (below), not ``facts_about('project:X')``. A dedicated object-side
    "what sessions used project X" query is a P2 concern.
    """
    print("=== projects detected (session.active_project) + one provenance each ===")
    rows = store.conn.execute(
        "SELECT f.object_entity_id, f.subject_id, f.valid_from, f.confidence, "
        "  (SELECT o.source_event_id FROM fact_provenance p "
        "   JOIN observations o ON o.id=p.observation_id WHERE p.fact_id=f.id LIMIT 1) AS seid "
        "FROM facts f WHERE f.predicate='session.active_project' AND f.retracted_at IS NULL "
        "ORDER BY f.valid_from"
    ).fetchall()
    for r in rows:
        print(
            f"  {r['object_entity_id']} in {r['subject_id']} "
            f"conf={r['confidence']:.4f} prov_event={r['seid']}"
        )

    if at_ms is None:
        at_ms = round(time.time() * 1000) - 86_400_000  # ~yesterday
    print(f"\n=== context_at({at_ms}) ===")
    ctx = store.context_at(at_ms)
    proj = ctx.get("project")
    print(f"  session_id: {ctx.get('session_id')}")
    print(f"  project: {proj.object_entity_id if proj else None}")
    print(f"  apps: {[a.object_entity_id for a in ctx.get('apps', [])]}")
    print(f"  observation_ids: {ctx.get('observation_ids')}")

    print("\n=== vocab.corrects_to facts (from --import-vocab) ===")
    for r in store.conn.execute(
        "SELECT subject_id, object_value FROM facts "
        "WHERE predicate='vocab.corrects_to' AND retracted_at IS NULL ORDER BY subject_id"
    ):
        print(f"  {r['subject_id']} -> {r['object_value']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="events -> observations bridge (Phase 1)")
    ap.add_argument("--source", default=default_activity_db(), help="activity.db to backfill from")
    ap.add_argument("--knowledge-db", default=default_knowledge_db(), help="knowledge.db to write")
    ap.add_argument("--since-days", type=float, default=30.0)
    ap.add_argument(
        "--verify", action="store_true", help="backfill then assert count parity (AT-1)"
    )
    ap.add_argument(
        "--import-vocab", action="store_true", help="import the learned-vocab file (AT-8)"
    )
    ap.add_argument("--vocab-path", default=None)
    ap.add_argument(
        "--spot-check", action="store_true", help="print provenance-linked answers (AT-2)"
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [bridge] %(levelname)s %(message)s")
    store = KnowledgeStore(args.knowledge_db, config=build_ingest_config())
    try:
        rc = 0
        if args.import_vocab:
            # --import-vocab is a distinct one-time operation (Input B, AT-8).
            vstats = import_vocab(store, args.vocab_path)
            print(f"import-vocab: {vstats}")
            if args.verify:
                expected = vstats["ingested"] + vstats["skipped"]  # entries that produced a fact
                ok = vstats["vocab_facts"] >= 1 and vstats["vocab_facts"] >= expected
                print(
                    f"VERIFY: vocab_facts={vstats['vocab_facts']} expected>={expected} "
                    f"-> {'OK' if ok else 'MISMATCH'}"
                )
                if not ok:
                    rc = 1
        else:
            # default operation: events backfill (AT-1/AT-5).
            stats = backfill(args.source, store, since_days=args.since_days)
            print(f"backfill: {stats}")
            if args.verify:
                ok = stats["qualifying_events"] == stats["obs_bridge"]
                print(
                    f"VERIFY: qualifying_events={stats['qualifying_events']} "
                    f"obs_bridge={stats['obs_bridge']} -> {'OK' if ok else 'MISMATCH'}"
                )
                if not ok:
                    rc = 1
        if args.spot_check:
            spot_check(store)
        return rc
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
