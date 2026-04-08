# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for correction escalation module."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_voice.escalation import check_repeated_corrections


def _make_db(tmp_path: Path, events: list[dict]) -> Path:
    db_path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE events ("
        "  event_id TEXT, timestamp REAL, modality TEXT, event_type TEXT,"
        "  app_name TEXT, window_title TEXT, monitor_index INTEGER,"
        "  payload TEXT, correlation_id TEXT, attention_score REAL,"
        "  cognitive_load REAL"
        ")"
    )
    for evt in events:
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "test", evt.get("timestamp", time.time()),
                evt.get("modality", "keys"),
                evt.get("event_type", "correction_detected"),
                "", "", 0,
                json.dumps(evt.get("payload", {})),
                None, 0.0, 0.0,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestEscalation:
    def test_detects_repeated_corrections(self, tmp_path):
        events = [
            {"payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"}}
            for _ in range(4)
        ]
        db = _make_db(tmp_path, events)
        results = check_repeated_corrections(db_path=db, hours=1, threshold=3)
        assert len(results) == 1
        assert results[0]["original"] == "context pulse"
        assert results[0]["corrected"] == "ContextPulse"
        assert results[0]["count"] == 4
        assert results[0]["action"] == "dry_run"

    def test_below_threshold_excluded(self, tmp_path):
        events = [
            {"payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"}}
            for _ in range(2)
        ]
        db = _make_db(tmp_path, events)
        results = check_repeated_corrections(db_path=db, hours=1, threshold=3)
        assert len(results) == 0

    def test_dry_run_never_writes(self, tmp_path):
        events = [
            {"payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"}}
            for _ in range(5)
        ]
        db = _make_db(tmp_path, events)
        results = check_repeated_corrections(db_path=db, hours=1, threshold=3, dry_run=True)
        assert len(results) == 1
        assert results[0]["action"] == "dry_run"

    def test_live_mode_calls_bridge(self, tmp_path):
        mock_bridge = MagicMock()
        mock_bridge.add_correction.return_value = True
        mock_bridge_cls = MagicMock(return_value=mock_bridge)

        events = [
            {"payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"}}
            for _ in range(4)
        ]
        db = _make_db(tmp_path, events)

        with patch("contextpulse_touch.correction_detector.VocabularyBridge", mock_bridge_cls):
            results = check_repeated_corrections(db_path=db, hours=1, threshold=3, dry_run=False)

        assert len(results) == 1
        assert results[0]["action"] == "added"
        mock_bridge.add_correction.assert_called_once_with("context pulse", "ContextPulse")

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path, [])
        results = check_repeated_corrections(db_path=db, hours=1)
        assert results == []

    def test_missing_db(self, tmp_path):
        results = check_repeated_corrections(db_path=tmp_path / "missing.db", hours=1)
        assert results == []

    def test_same_original_different_corrected(self, tmp_path):
        # When same word is corrected multiple times, last one wins
        events = [
            {"payload": {"original_word": "stock trader", "corrected_word": "StockTrader"}}
            for _ in range(4)
        ]
        db = _make_db(tmp_path, events)
        results = check_repeated_corrections(db_path=db, hours=1, threshold=3)
        assert len(results) == 1
        assert results[0]["corrected"] == "StockTrader"

    def test_corrupt_payload_handled(self, tmp_path):
        db_path = tmp_path / "activity.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE events ("
            "  event_id TEXT, timestamp REAL, modality TEXT, event_type TEXT,"
            "  app_name TEXT, window_title TEXT, monitor_index INTEGER,"
            "  payload TEXT, correlation_id TEXT, attention_score REAL,"
            "  cognitive_load REAL"
            ")"
        )
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("test", time.time(), "keys", "correction_detected", "", "", 0,
             "not-json", None, 0.0, 0.0),
        )
        conn.commit()
        conn.close()
        results = check_repeated_corrections(db_path=db_path, hours=1)
        assert results == []

    def test_empty_original_skipped(self, tmp_path):
        events = [
            {"payload": {"original_word": "", "corrected_word": "ContextPulse"}}
            for _ in range(5)
        ]
        db = _make_db(tmp_path, events)
        results = check_repeated_corrections(db_path=db, hours=1, threshold=1)
        assert len(results) == 0
