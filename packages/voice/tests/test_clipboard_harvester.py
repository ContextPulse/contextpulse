# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for clipboard term harvester."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from contextpulse_voice.clipboard_harvester import harvest_clipboard_terms


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
                evt.get("modality", "clipboard"),
                evt.get("event_type", "clipboard_change"),
                "", "", 0,
                json.dumps(evt.get("payload", {})),
                None, 0.0, 0.0,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestClipboardHarvester:
    def test_extracts_camel_case(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "Copied ContextPulse reference"}},
            {"payload": {"text": "Also ContextPulse and PhotoEditor"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        terms = [r["term"] for r in results]
        assert "ContextPulse" in terms

    def test_long_text_skipped(self, tmp_path):
        long_text = "x" * 201 + " ContextPulse"
        db = _make_db(tmp_path, [
            {"payload": {"text": long_text}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        assert len(results) == 0

    def test_urls_skipped(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "https://github.com/ContextPulse"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        assert len(results) == 0

    def test_file_paths_skipped(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "C:\\Users\\ContextPulse\\file.py"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        assert len(results) == 0

    def test_min_length_filter(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "MyApp is short, ContextPulse is not"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1, min_length=6)
        terms = [r["term"] for r in results]
        assert "MyApp" not in terms
        assert "ContextPulse" in terms

    def test_dry_run_default(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "ContextPulse test"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1, dry_run=True)
        assert len(results) > 0

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path, [])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        assert results == []

    def test_source_field(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "ContextPulse reference"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        assert all(r["source"] == "clipboard" for r in results)

    def test_common_phrases_filtered(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"text": "ScreenContext again"}},
        ])
        results = harvest_clipboard_terms(db_path=db, hours=1)
        terms = [r["term"] for r in results]
        assert "ScreenContext" not in terms
