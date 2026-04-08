# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for vocabulary consolidator and cross-modal mining."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from contextpulse_voice.consolidator import (
    _backup_vocab_files,
    _cross_modal_correction_mining,
    _deduplicate_vocab_layers,
    consolidate_vocabulary,
)


def _make_db(tmp_path: Path, events: list[dict] | None = None) -> Path:
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
    if events:
        for evt in events:
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "test", evt.get("timestamp", time.time()),
                    evt["modality"], evt["event_type"],
                    "", "", 0,
                    json.dumps(evt.get("payload", {})),
                    None, 0.0, 0.0,
                ),
            )
    conn.commit()
    conn.close()
    return db_path


class TestConsolidateVocabulary:
    def test_dry_run_returns_summary(self, tmp_path):
        db = _make_db(tmp_path)
        summary = consolidate_vocabulary(db_path=db, dry_run=True)
        assert summary["dry_run"] is True
        assert "session_learned" in summary
        assert "cross_modal" in summary
        assert "ocr_harvested" in summary

    def test_dry_run_writes_nothing(self, tmp_path):
        db = _make_db(tmp_path)
        vocab_dir = tmp_path / "voice"
        consolidate_vocabulary(db_path=db, dry_run=True)
        # No backup directory should exist
        assert not (vocab_dir / "backups").exists()

    def test_empty_db_succeeds(self, tmp_path):
        db = _make_db(tmp_path)
        summary = consolidate_vocabulary(db_path=db, dry_run=True)
        assert summary["session_learned"] == 0
        assert summary["cross_modal"] == 0

    def test_missing_db(self, tmp_path):
        summary = consolidate_vocabulary(
            db_path=tmp_path / "missing.db", dry_run=True,
        )
        assert summary["session_learned"] == 0


class TestCrossModalMining:
    def test_screen_verified_correction(self, tmp_path):
        t = time.time()
        events = [
            # Transcription event
            {
                "modality": "voice", "event_type": "transcription",
                "timestamp": t,
                "payload": {"transcript": "Working on ContextPulse", "raw_transcript": "working on context pulse"},
            },
            # OCR showing "ContextPulse" on screen at same time
            {
                "modality": "sight", "event_type": "ocr_result",
                "timestamp": t + 1,
                "payload": {"ocr_text": "class ContextPulse:", "ocr_confidence": 0.9},
            },
            # User corrected "context pulse" to "ContextPulse" within 30s
            {
                "modality": "keys", "event_type": "correction_detected",
                "timestamp": t + 10,
                "payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"},
            },
        ]
        db = _make_db(tmp_path, events)
        results = _cross_modal_correction_mining(db_path=db, hours=1)
        assert len(results) == 1
        assert results[0]["original"] == "context pulse"
        assert results[0]["corrected"] == "ContextPulse"
        assert results[0]["confidence"] == 0.95
        assert results[0]["source"] == "cross_modal_screen_verified"

    def test_no_screen_context_no_match(self, tmp_path):
        t = time.time()
        events = [
            {
                "modality": "voice", "event_type": "transcription",
                "timestamp": t,
                "payload": {"transcript": "test", "raw_transcript": "test"},
            },
            # Correction but no OCR event
            {
                "modality": "keys", "event_type": "correction_detected",
                "timestamp": t + 10,
                "payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"},
            },
        ]
        db = _make_db(tmp_path, events)
        results = _cross_modal_correction_mining(db_path=db, hours=1)
        assert len(results) == 0

    def test_correction_outside_window(self, tmp_path):
        t = time.time()
        events = [
            {
                "modality": "voice", "event_type": "transcription",
                "timestamp": t,
                "payload": {"transcript": "test", "raw_transcript": "test"},
            },
            {
                "modality": "sight", "event_type": "ocr_result",
                "timestamp": t + 1,
                "payload": {"ocr_text": "ContextPulse code", "ocr_confidence": 0.9},
            },
            # Correction 60s later (outside 30s window)
            {
                "modality": "keys", "event_type": "correction_detected",
                "timestamp": t + 60,
                "payload": {"original_word": "context pulse", "corrected_word": "ContextPulse"},
            },
        ]
        db = _make_db(tmp_path, events)
        results = _cross_modal_correction_mining(db_path=db, hours=1)
        assert len(results) == 0

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path)
        results = _cross_modal_correction_mining(db_path=db, hours=1)
        assert results == []


class TestBackupVocabFiles:
    def test_creates_backups(self, tmp_path):
        with patch("contextpulse_voice.consolidator.VOICE_DATA_DIR", tmp_path), \
             patch("contextpulse_voice.consolidator.VOCAB_FILE", tmp_path / "vocabulary.json"), \
             patch("contextpulse_voice.consolidator.LEARNED_VOCAB_FILE", tmp_path / "vocabulary_learned.json"), \
             patch("contextpulse_voice.consolidator.CONTEXT_VOCAB_FILE", tmp_path / "vocabulary_context.json"):
            # Create source files
            (tmp_path / "vocabulary.json").write_text('{"a": "b"}')
            (tmp_path / "vocabulary_learned.json").write_text('{"c": "d"}')

            _backup_vocab_files()

            backup_dir = tmp_path / "backups"
            assert backup_dir.exists()
            backups = list(backup_dir.glob("*.json"))
            assert len(backups) == 2


class TestDeduplicateVocabLayers:
    def test_removes_context_dupes(self, tmp_path):
        vocab = tmp_path / "vocabulary.json"
        learned = tmp_path / "vocabulary_learned.json"
        context = tmp_path / "vocabulary_context.json"

        vocab.write_text('{"context pulse": "ContextPulse"}')
        learned.write_text('{"stock trader": "StockTrader"}')
        context.write_text('{"context pulse": "ContextPulse", "stock trader": "StockTrader", "swing pulse": "SwingPulse"}')

        with patch("contextpulse_voice.consolidator.VOCAB_FILE", vocab), \
             patch("contextpulse_voice.consolidator.LEARNED_VOCAB_FILE", learned), \
             patch("contextpulse_voice.consolidator.CONTEXT_VOCAB_FILE", context):
            removed = _deduplicate_vocab_layers()

        assert removed == 2
        remaining = json.loads(context.read_text())
        assert "swing pulse" in remaining
        assert "context pulse" not in remaining
        assert "stock trader" not in remaining

    def test_no_dupes(self, tmp_path):
        context = tmp_path / "vocabulary_context.json"
        context.write_text('{"unique term": "UniqueTerm"}')

        with patch("contextpulse_voice.consolidator.VOCAB_FILE", tmp_path / "v.json"), \
             patch("contextpulse_voice.consolidator.LEARNED_VOCAB_FILE", tmp_path / "l.json"), \
             patch("contextpulse_voice.consolidator.CONTEXT_VOCAB_FILE", context):
            removed = _deduplicate_vocab_layers()

        assert removed == 0
