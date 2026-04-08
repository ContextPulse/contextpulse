# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for OCR term harvester."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from contextpulse_voice.ocr_harvester import harvest_ocr_terms


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
                evt.get("modality", "sight"),
                evt.get("event_type", "ocr_result"),
                "", "", 0,
                json.dumps(evt.get("payload", {})),
                None, 0.0, 0.0,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestOcrHarvester:
    def test_extracts_camel_case(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "ContextPulse StockTrader SwingPulse", "ocr_confidence": 0.9}}
            for _ in range(3)
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=3)
        terms = [r["term"] for r in results]
        assert "ContextPulse" in terms
        assert "StockTrader" in terms

    def test_min_occurrences_filter(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "ContextPulse only once", "ocr_confidence": 0.9}},
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=3)
        assert len(results) == 0

    def test_common_phrases_filtered(self, tmp_path):
        # "ScreenContext" splits to "screen context" which is in _COMMON_PHRASES
        events = [
            {"payload": {"ocr_text": "ScreenContext everywhere", "ocr_confidence": 0.9}}
            for _ in range(5)
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=1)
        terms = [r["term"] for r in results]
        assert "ScreenContext" not in terms

    def test_dry_run_never_writes(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "ContextPulse", "ocr_confidence": 0.9}}
            for _ in range(5)
        ]
        db = _make_db(tmp_path, events)
        vocab_file = tmp_path / "voice" / "vocabulary_context.json"
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=1, dry_run=True)
        assert len(results) > 0
        assert not vocab_file.exists()

    def test_low_confidence_excluded(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "LowConfidence ContextPulse", "ocr_confidence": 0.3}}
            for _ in range(5)
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=1, min_confidence=0.75)
        assert len(results) == 0

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path, [])
        results = harvest_ocr_terms(db_path=db, hours=1)
        assert results == []

    def test_missing_db(self, tmp_path):
        results = harvest_ocr_terms(db_path=tmp_path / "missing.db", hours=1)
        assert results == []

    def test_dot_separated_names(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "Using Next.js and Vue.js", "ocr_confidence": 0.9}}
            for _ in range(4)
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=3)
        terms = [r["term"] for r in results]
        assert "Next.js" in terms

    def test_source_field(self, tmp_path):
        events = [
            {"payload": {"ocr_text": "ContextPulse", "ocr_confidence": 0.9}}
            for _ in range(3)
        ]
        db = _make_db(tmp_path, events)
        results = harvest_ocr_terms(db_path=db, hours=1, min_occurrences=1)
        assert all(r["source"] == "ocr" for r in results)
