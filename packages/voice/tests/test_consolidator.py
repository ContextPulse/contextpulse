# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for vocabulary consolidator and cross-modal mining."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

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

    def test_no_errors_on_clean_run(self, tmp_path):
        db = _make_db(tmp_path)
        summary = consolidate_vocabulary(db_path=db, dry_run=True)
        assert summary["errors"] == {}

    def test_step_failure_recorded_in_errors_not_swallowed(self, tmp_path):
        """Regression: a raising step used to be indistinguishable from a
        step that legitimately found nothing (cp-consolidator-silent-step-failures).

        A caller reading only the count fields cannot tell "0 results" apart
        from "this step blew up" — summary["errors"] is what makes that
        distinguishable, and it must name the failing step.
        """
        db = _make_db(tmp_path)
        with patch(
            "contextpulse_voice.session_learner.learn_from_transcription_history",
            side_effect=RuntimeError("boom"),
        ):
            summary = consolidate_vocabulary(db_path=db, dry_run=True)

        assert summary["session_learned"] == 0  # unchanged default, not a lie
        assert "session_learning" in summary["errors"]
        assert "boom" in summary["errors"]["session_learning"]

    def test_one_step_failure_does_not_block_other_steps(self, tmp_path):
        """Resilience must survive the fix: one bad step still lets the rest run."""
        t = time.time()
        events = [
            {
                "modality": "voice", "event_type": "transcription", "timestamp": t,
                "payload": {"transcript": "test", "raw_transcript": "test"},
            },
        ]
        db = _make_db(tmp_path, events)
        with patch(
            "contextpulse_voice.session_learner.learn_from_transcription_history",
            side_effect=RuntimeError("boom"),
        ):
            summary = consolidate_vocabulary(db_path=db, dry_run=True)

        assert "session_learning" in summary["errors"]
        # cross_modal step still executed and is absent from errors.
        assert "cross_modal" not in summary["errors"]

    def test_multiple_step_failures_all_recorded(self, tmp_path):
        db = _make_db(tmp_path)
        with (
            patch(
                "contextpulse_voice.session_learner.learn_from_transcription_history",
                side_effect=RuntimeError("session boom"),
            ),
            patch(
                "contextpulse_voice.ocr_harvester.harvest_ocr_terms",
                side_effect=RuntimeError("ocr boom"),
            ),
        ):
            summary = consolidate_vocabulary(db_path=db, dry_run=True)

        assert set(summary["errors"]) == {"session_learning", "ocr_harvesting"}

    def test_rebuild_runs_before_harvesters(self, tmp_path):
        """Regression: the rebuild used to run last and clobber harvested terms.

        The rebuild and both harvesters all write vocabulary_context.json, so
        ordering is load-bearing, not cosmetic.
        """
        db = _make_db(tmp_path)
        calls: list[str] = []

        with (
            patch(
                "contextpulse_voice.context_vocab.rebuild_context_vocabulary",
                side_effect=lambda *a, **k: calls.append("rebuild") or 0,
            ),
            patch(
                "contextpulse_voice.ocr_harvester.harvest_ocr_terms",
                side_effect=lambda *a, **k: calls.append("ocr") or [],
            ),
            patch(
                "contextpulse_voice.clipboard_harvester.harvest_clipboard_terms",
                side_effect=lambda *a, **k: calls.append("clipboard") or [],
            ),
            patch("contextpulse_voice.consolidator._backup_vocab_files"),
            patch(
                "contextpulse_voice.consolidator._deduplicate_vocab_layers",
                return_value=0,
            ),
        ):
            consolidate_vocabulary(db_path=db, dry_run=False)

        assert calls.index("rebuild") < calls.index("ocr")
        assert calls.index("rebuild") < calls.index("clipboard")

    def test_dedup_failure_recorded_and_does_not_crash_run(self, tmp_path):
        db = _make_db(tmp_path)
        with (
            patch("contextpulse_voice.consolidator._backup_vocab_files"),
            patch(
                "contextpulse_voice.consolidator._deduplicate_vocab_layers",
                side_effect=RuntimeError("dedup boom"),
            ),
            patch(
                "contextpulse_voice.context_vocab.rebuild_context_vocabulary",
                return_value=0,
            ),
        ):
            summary = consolidate_vocabulary(db_path=db, dry_run=False)

        assert summary["deduped"] == 0
        assert "deduplication" in summary["errors"]
        assert "dedup boom" in summary["errors"]["deduplication"]


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
        learned.write_text('{"photo editor": "PhotoEditor"}')
        context.write_text('{"context pulse": "ContextPulse", "photo editor": "PhotoEditor", "weather app": "WeatherApp"}')

        with patch("contextpulse_voice.consolidator.VOCAB_FILE", vocab), \
             patch("contextpulse_voice.consolidator.LEARNED_VOCAB_FILE", learned), \
             patch("contextpulse_voice.consolidator.CONTEXT_VOCAB_FILE", context):
            removed = _deduplicate_vocab_layers()

        assert removed == 2
        remaining = json.loads(context.read_text())
        assert "weather app" in remaining
        assert "context pulse" not in remaining
        assert "photo editor" not in remaining

    def test_no_dupes(self, tmp_path):
        context = tmp_path / "vocabulary_context.json"
        context.write_text('{"unique term": "UniqueTerm"}')

        with patch("contextpulse_voice.consolidator.VOCAB_FILE", tmp_path / "v.json"), \
             patch("contextpulse_voice.consolidator.LEARNED_VOCAB_FILE", tmp_path / "l.json"), \
             patch("contextpulse_voice.consolidator.CONTEXT_VOCAB_FILE", context):
            removed = _deduplicate_vocab_layers()

        assert removed == 0
