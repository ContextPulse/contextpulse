# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Bridge tests (dossier v2 §4 / §6.2): event->observation mapping, backfill count
parity + idempotency (AT-1/AT-5), watermark, vocab-file import (AT-8), and the C1
live-shape correction fixture (failing-direction — rejects the v1 wrong mapping)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from contextpulse_knowledge import bridge
from contextpulse_knowledge.store_sqlite import KnowledgeStore

BASE = 1_750_000_000.0  # a fixed 2025 epoch; keeps the 30-day window deterministic

_EVENTS_DDL = """
CREATE TABLE events (
  event_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, modality TEXT NOT NULL,
  event_type TEXT NOT NULL, app_name TEXT DEFAULT '', window_title TEXT DEFAULT '',
  monitor_index INTEGER DEFAULT 0, payload TEXT NOT NULL, correlation_id TEXT,
  attention_score REAL DEFAULT 0.0, cognitive_load REAL DEFAULT 0.0,
  created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""


def _row(event_id, ts, etype, *, app="", title="", payload=None, modality="system", mon=0):
    return {
        "event_id": event_id,
        "timestamp": ts,
        "modality": modality,
        "event_type": etype,
        "app_name": app,
        "window_title": title,
        "monitor_index": mon,
        "payload": json.dumps(payload or {}),
        "correlation_id": None,
        "attention_score": 0.0,
        "cognitive_load": 0.0,
    }


def _make_events_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(_EVENTS_DDL)
    conn.executemany(
        "INSERT INTO events(event_id,timestamp,modality,event_type,app_name,window_title,"
        "monitor_index,payload,correlation_id,attention_score,cognitive_load) "
        "VALUES(:event_id,:timestamp,:modality,:event_type,:app_name,:window_title,"
        ":monitor_index,:payload,:correlation_id,:attention_score,:cognitive_load)",
        rows,
    )
    conn.commit()
    conn.close()


# ── mapping ─────────────────────────────────────────────────────────


def test_event_to_observation_maps_core_fields():
    obs = bridge.event_to_observation(
        source="bridge:events",
        event_id="e1",
        timestamp=BASE + 0.5,
        event_type="ocr_result",
        app_name="Code.exe",
        window_title="bridge.py",
        payload={"ocr_text": "def backfill"},
    )
    assert obs is not None
    assert obs.source == "bridge:events"
    assert obs.source_event_id == "e1"
    assert obs.kind == "ocr_result"
    assert obs.observed_at == round((BASE + 0.5) * 1000)  # half-even ms
    assert obs.content == "def backfill"
    assert obs.url is None


def test_non_qualifying_event_maps_to_none():
    assert (
        bridge.event_to_observation(
            source="bridge:events", event_id="c1", timestamp=BASE, event_type="click"
        )
        is None
    )


def test_content_join_matches_live_text_content_keys():
    # Drift guard: bridge's 5-key join order must equal ContextEvent.text_content()'s.
    from contextpulse_core.spine.events import _TEXT_PAYLOAD_KEYS

    assert bridge._TEXT_KEYS == _TEXT_PAYLOAD_KEYS
    obs = bridge.event_to_observation(
        source="s",
        event_id="e",
        timestamp=BASE,
        event_type="transcription",
        payload={"transcript": "hello", "text": "world"},
    )
    assert obs.content == "hello world"


def test_observed_at_uses_half_even_rounding():
    # 0.0005s * 1000 = 0.5ms -> half-even rounds to 0 (not 1).
    obs = bridge.event_to_observation(
        source="s", event_id="e", timestamp=0.0005, event_type="ocr_result"
    )
    assert obs.observed_at == 0


# ── backfill: count parity (AT-1) + idempotency (AT-5) ──────────────


def _seed_events_db(path):
    _make_events_db(
        path,
        [
            _row(
                "e-ocr",
                BASE,
                "ocr_result",
                app="Code.exe",
                title="bridge.py",
                payload={"ocr_text": "def backfill"},
            ),
            _row("e-type", BASE + 2, "typing_burst", app="Code.exe", payload={"burst_text": "hi"}),
            _row("e-clip", BASE + 5, "clipboard_change", payload={"text": "copied"}),
            _row("e-click", BASE + 3, "click"),  # non-qualifying -> ignored
            _row("e-lock", BASE + 10, "session_lock"),
        ],
    )


def test_backfill_count_parity_and_idempotent(tmp_path):
    events_db = tmp_path / "activity.db"
    _seed_events_db(events_db)
    store = KnowledgeStore(":memory:")
    try:
        stats = bridge.backfill(str(events_db), store, since_days=1, now_s=BASE + 100)
        # 4 qualifying rows (ocr, typing, clipboard, lock); the click is excluded.
        assert stats["qualifying_events"] == 4
        assert stats["obs_bridge"] == 4
        assert stats["ingested"] == 4
        assert stats["qualifying_events"] == stats["obs_bridge"]  # AT-1

        max_conf = store.conn.execute(
            "SELECT max(confidence) FROM facts WHERE extraction='deterministic' "
            "AND predicate != 'session.occurred'"
        ).fetchone()[0]

        # AT-5: re-run ingests zero, counts + confidence unchanged (C2 skip at scale).
        stats2 = bridge.backfill(str(events_db), store, since_days=1, now_s=BASE + 100)
        assert stats2["ingested"] == 0
        assert stats2["skipped"] == 4
        assert stats2["obs_bridge"] == 4
        max_conf2 = store.conn.execute(
            "SELECT max(confidence) FROM facts WHERE extraction='deterministic' "
            "AND predicate != 'session.occurred'"
        ).fetchone()[0]
        assert max_conf2 == max_conf
    finally:
        store.close()


def test_backfill_writes_watermark(tmp_path):
    events_db = tmp_path / "activity.db"
    _seed_events_db(events_db)
    store = KnowledgeStore(":memory:")
    try:
        bridge.backfill(str(events_db), store, since_days=1, now_s=BASE + 100)
        wm = store.get_ingest_state("bridge_watermark")
        assert wm is not None
        assert wm["last_timestamp"] == BASE + 10  # REAL seconds verbatim (m1)
        assert wm["last_event_id"] == "e-lock"
    finally:
        store.close()


# ── C1 correction fixture (failing-direction, §6.2) ─────────────────

# Payload copied verbatim from touch_module.py:174-182.
_CORRECTION_PAYLOAD = {
    "original_text": "sonet",
    "corrected_text": "Sonnet",
    "correction_text": "sonet -> Sonnet",
    "correction_type": "manual",
    "confidence": 0.9,
    "seconds_after_paste": 2.0,
    "paste_event_id": "abc123",
}


def test_correction_verbatim_payload_emits_vocab_fact(tmp_path):
    events_db = tmp_path / "activity.db"
    _make_events_db(
        events_db,
        [_row("e-corr", BASE, "correction_detected", payload=_CORRECTION_PAYLOAD)],
    )
    store = KnowledgeStore(":memory:")
    try:
        bridge.backfill(str(events_db), store, since_days=1, now_s=BASE + 100)
        rows = store.conn.execute(
            "SELECT object_value FROM facts WHERE predicate='vocab.corrects_to'"
        ).fetchall()
        assert any(r["object_value"] == "Sonnet" for r in rows)  # rejects the v1 mapping
    finally:
        store.close()


def test_correction_wrong_keys_emits_nothing_and_warns(tmp_path, caplog):
    events_db = tmp_path / "activity.db"
    # The v1 shape: {"original":..., "corrected":...} — the keys that silently failed.
    _make_events_db(
        events_db,
        [
            _row(
                "e-bad",
                BASE,
                "correction_detected",
                payload={"original": "sonet", "corrected": "Sonnet"},
            )
        ],
    )
    store = KnowledgeStore(":memory:")
    try:
        with caplog.at_level(logging.WARNING, logger="contextpulse.knowledge.bridge"):
            bridge.backfill(str(events_db), store, since_days=1, now_s=BASE + 100)
        n = store.conn.execute(
            "SELECT count(*) FROM facts WHERE predicate='vocab.corrects_to'"
        ).fetchone()[0]
        assert n == 0
        assert any("missing original_text/corrected_text" in r.message for r in caplog.records)
    finally:
        store.close()


# ── vocab-file import (Input B, AT-8) ───────────────────────────────


def test_import_vocab_from_file_and_idempotent(tmp_path):
    vocab = tmp_path / "vocabulary_learned.json"
    vocab.write_text(json.dumps({"sonet": "Sonnet", "clod": "Claude"}), encoding="utf-8")
    store = KnowledgeStore(":memory:")
    try:
        stats = bridge.import_vocab(store, str(vocab))
        assert stats["entries"] == 2
        assert stats["ingested"] == 2
        assert stats["vocab_facts"] == 2  # AT-8: facts == entries
        objs = {
            r["object_value"]
            for r in store.conn.execute(
                "SELECT object_value FROM facts WHERE predicate='vocab.corrects_to'"
            )
        }
        assert objs == {"Sonnet", "Claude"}
        # provenance resolves to a bridge:vocab_file observation
        prov = store.conn.execute(
            "SELECT o.source FROM observations o JOIN fact_provenance p ON p.observation_id=o.id "
            "JOIN facts f ON f.id=p.fact_id WHERE f.predicate='vocab.corrects_to' LIMIT 1"
        ).fetchone()
        assert prov["source"] == "bridge:vocab_file"

        # idempotent re-import
        stats2 = bridge.import_vocab(store, str(vocab))
        assert stats2["ingested"] == 0
        assert stats2["skipped"] == 2
        assert stats2["vocab_facts"] == 2
    finally:
        store.close()


# ── live listener (Phase B) ─────────────────────────────────────────


def test_ingestor_drain_ingests_qualifying_events():
    from contextpulse_core.spine.events import ContextEvent, EventType, Modality

    store = KnowledgeStore(":memory:")
    try:
        ing = bridge.KnowledgeIngestor(store)
        ing._enqueue(
            ContextEvent(
                event_id="live1",
                timestamp=BASE,
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                app_name="Code.exe",
                window_title="x",
                payload={"ocr_text": "hello"},
            )
        )
        ing._enqueue(
            ContextEvent(
                event_id="live2",
                timestamp=BASE + 1,
                modality=Modality.FLOW,
                event_type=EventType.CLICK,
            )
        )  # non-qualifying
        n = ing.drain_once()
        assert n == 1
        obs = store.conn.execute(
            "SELECT count(*) FROM observations WHERE source='live:eventbus'"
        ).fetchone()[0]
        assert obs == 1
    finally:
        store.close()


def test_ingestor_thread_ingests_across_threads():
    # C1 regression (adversarial-review finding): the store is created on THIS thread but
    # observe() runs on the spawned ingest thread. Before check_same_thread=False + the
    # store lock, this raised sqlite3.ProgrammingError and every live event was silently
    # dead-lettered. drain_once()-only tests never exercised the thread, so the bug hid.
    from contextpulse_core.spine.events import ContextEvent, EventType, Modality

    store = KnowledgeStore(":memory:")
    ing = bridge.KnowledgeIngestor(store, tick_s=0.05)
    try:
        ing._enqueue(
            ContextEvent(
                event_id="t1",
                timestamp=BASE,
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                app_name="Code.exe",
                window_title="x",
                payload={"ocr_text": "hi"},
            )
        )
        ing.start(catch_up=False)
        deadline = time.time() + 5.0
        n = 0
        while time.time() < deadline:
            with store._lock:  # read the connection while the ingest thread writes it
                n = store.conn.execute(
                    "SELECT count(*) FROM observations WHERE source='live:eventbus'"
                ).fetchone()[0]
            if n >= 1:
                break
            time.sleep(0.05)
        assert n == 1  # the ingest thread actually wrote — no ProgrammingError
    finally:
        ing.stop()
        store.close()


def test_mapping_carries_modality():
    obs = bridge.event_to_observation(
        source="s",
        event_id="e",
        timestamp=BASE,
        event_type="ocr_result",
        modality="sight",
        payload={"ocr_text": "x"},
    )
    assert obs.meta["modality"] == "sight"  # M1: modality no longer dropped


def test_live_drain_advances_watermark():
    from contextpulse_core.spine.events import ContextEvent, EventType, Modality

    store = KnowledgeStore(":memory:")
    try:
        ing = bridge.KnowledgeIngestor(store)
        ing._enqueue(
            ContextEvent(
                event_id="w1",
                timestamp=BASE,
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                payload={"ocr_text": "x"},
            )
        )
        ing.drain_once()
        wm = store.get_ingest_state("bridge_watermark")  # M4: live drain advances it
        assert wm is not None
        assert wm["last_timestamp"] == BASE
        assert wm["last_event_id"] == "w1"
    finally:
        store.close()


def test_malformed_payload_warns(caplog):
    # M2: a corrupt payload logs loudly (never a silent swallow) and yields empty content.
    with caplog.at_level(logging.WARNING, logger="contextpulse.knowledge.bridge"):
        obs = bridge.observation_from_row(
            {
                "event_id": "bad",
                "timestamp": BASE,
                "event_type": "ocr_result",
                "app_name": "",
                "window_title": "",
                "monitor_index": 0,
                "payload": "{not json",
                "modality": "sight",
                "correlation_id": None,
                "attention_score": 0.0,
                "cognitive_load": 0.0,
            },
            "bridge:events",
        )
    assert obs is not None and obs.content is None
    assert any("malformed payload" in r.message for r in caplog.records)
