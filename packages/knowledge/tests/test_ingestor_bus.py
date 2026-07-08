# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Compressed AT-4: events emitted on a real EventBus flow through the
KnowledgeIngestor's LIVE thread into knowledge.db, and are readable from a
SEPARATE connection concurrently (the WAL reader/writer path the daemon +
MCP server use). Exercises the exact wiring daemon._init_knowledge sets up:
ingestor.attach(bus) -> bus.emit -> live-thread ingest.
"""

from __future__ import annotations

import sqlite3
import time

from contextpulse_core.spine import ContextEvent, EventBus, EventType, Modality
from contextpulse_knowledge import bridge
from contextpulse_knowledge.store_sqlite import KnowledgeStore


def _count_observations(db_path) -> int:
    """Count via a fresh connection — mirrors the MCP tool reading while the
    daemon's ingest thread writes (validates WAL concurrent access)."""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    finally:
        conn.close()


def test_bus_emit_flows_to_observation_via_live_thread(tmp_path):
    activity_db = tmp_path / "activity.db"
    knowledge_db = tmp_path / "knowledge.db"

    bus = EventBus(str(activity_db))
    store = KnowledgeStore(str(knowledge_db))
    store.conn.execute("PRAGMA journal_mode=WAL")
    ingestor = bridge.KnowledgeIngestor(store, activity_db=str(activity_db))
    ingestor.attach(bus)
    ingestor.start()  # REAL background thread (not a synchronous drain)

    n = 15
    try:
        for i in range(n):
            bus.emit(ContextEvent(
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                app_name="Code.exe",
                payload={"ocr_text": f"quokka onboarding note {i}"},
            ))

        # Poll a SEPARATE connection until the live thread has ingested them.
        deadline = time.time() + 10
        obs = 0
        while time.time() < deadline:
            obs = _count_observations(knowledge_db)
            if obs >= n:
                break
            time.sleep(0.1)

        assert obs >= n, f"live-thread ingest only landed {obs}/{n} observations"
    finally:
        ingestor.stop()
        store.close()
        bus.close()


def test_stop_is_clean_and_idempotent(tmp_path):
    bus = EventBus(str(tmp_path / "activity.db"))
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    ingestor = bridge.KnowledgeIngestor(store, activity_db=str(tmp_path / "activity.db"))
    ingestor.attach(bus)
    ingestor.start()
    ingestor.stop()
    ingestor.stop()  # second stop must be a harmless no-op
    store.close()
    bus.close()
