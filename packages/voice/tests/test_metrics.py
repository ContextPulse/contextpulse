# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for voice accuracy metrics module."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from contextpulse_voice.metrics import (
    compute_accuracy_scorecard,
    generate_weekly_report,
    record_metrics,
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


class TestComputeAccuracyScorecard:
    def test_counts_transcriptions(self, tmp_path):
        events = [
            {"modality": "voice", "event_type": "transcription", "payload": {"transcript": "hello"}},
            {"modality": "voice", "event_type": "transcription", "payload": {"transcript": "world"}},
        ]
        db = _make_db(tmp_path, events)
        sc = compute_accuracy_scorecard(db_path=db, hours=1)
        assert sc["total_transcriptions"] == 2

    def test_counts_corrections(self, tmp_path):
        events = [
            {"modality": "voice", "event_type": "transcription", "payload": {"transcript": "hello"}},
            {"modality": "keys", "event_type": "correction_detected", "payload": {"original_word": "helo", "corrected_word": "hello"}},
        ]
        db = _make_db(tmp_path, events)
        sc = compute_accuracy_scorecard(db_path=db, hours=1)
        assert sc["corrections_detected"] == 1
        assert sc["correction_rate"] == 1.0

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path)
        sc = compute_accuracy_scorecard(db_path=db, hours=1)
        assert sc["total_transcriptions"] == 0
        assert sc["correction_rate"] == 0.0

    def test_missing_db(self, tmp_path):
        sc = compute_accuracy_scorecard(db_path=tmp_path / "missing.db", hours=1)
        assert sc["total_transcriptions"] == 0

    def test_top_corrected_words(self, tmp_path):
        events = [
            {"modality": "keys", "event_type": "correction_detected", "payload": {"original_word": "contxt", "corrected_word": "context"}},
            {"modality": "keys", "event_type": "correction_detected", "payload": {"original_word": "contxt", "corrected_word": "context"}},
            {"modality": "keys", "event_type": "correction_detected", "payload": {"original_word": "puls", "corrected_word": "pulse"}},
        ]
        db = _make_db(tmp_path, events)
        sc = compute_accuracy_scorecard(db_path=db, hours=1)
        assert len(sc["top_corrected_words"]) == 2
        assert sc["top_corrected_words"][0]["word"] == "contxt"
        assert sc["top_corrected_words"][0]["count"] == 2


class TestRecordMetrics:
    def test_appends_jsonl(self, tmp_path):
        metrics_file = tmp_path / "metrics_history.jsonl"
        with patch("contextpulse_voice.metrics.METRICS_FILE", metrics_file), \
             patch("contextpulse_voice.metrics.VOICE_DATA_DIR", tmp_path):
            record_metrics({"test": 1})
            record_metrics({"test": 2})

        lines = metrics_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["test"] == 1
        assert json.loads(lines[1])["test"] == 2


class TestGenerateWeeklyReport:
    def test_insufficient_data(self, tmp_path):
        with patch("contextpulse_voice.metrics.METRICS_FILE", tmp_path / "missing.jsonl"):
            report = generate_weekly_report()
        assert report["insufficient_data"] is True

    def test_single_entry_insufficient(self, tmp_path):
        metrics_file = tmp_path / "metrics.jsonl"
        metrics_file.write_text(json.dumps({"correction_rate": 0.5}) + "\n")
        with patch("contextpulse_voice.metrics.METRICS_FILE", metrics_file):
            report = generate_weekly_report()
        assert report["insufficient_data"] is True

    def test_trend_with_data(self, tmp_path):
        metrics_file = tmp_path / "metrics.jsonl"
        entries = [
            {"date": "2026-04-01", "correction_rate": 0.5, "vocab_user_size": 10, "vocab_learned_size": 5, "vocab_context_size": 20, "top_corrected_words": []},
            {"date": "2026-04-02", "correction_rate": 0.3, "vocab_user_size": 10, "vocab_learned_size": 8, "vocab_context_size": 22, "top_corrected_words": [{"word": "test", "count": 3}]},
            {"date": "2026-04-03", "correction_rate": 0.2, "vocab_user_size": 10, "vocab_learned_size": 12, "vocab_context_size": 25, "top_corrected_words": []},
        ]
        metrics_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        with patch("contextpulse_voice.metrics.METRICS_FILE", metrics_file):
            report = generate_weekly_report()

        assert report["insufficient_data"] is False
        assert report["entries"] == 3
        assert report["vocab_growth"]["learned"] == 7  # 12 - 5

    def test_corrupt_lines_skipped(self, tmp_path):
        metrics_file = tmp_path / "metrics.jsonl"
        metrics_file.write_text(
            json.dumps({"date": "a", "correction_rate": 0.5, "vocab_user_size": 0, "vocab_learned_size": 0, "vocab_context_size": 0, "top_corrected_words": []}) + "\n"
            "not-json\n"
            + json.dumps({"date": "b", "correction_rate": 0.3, "vocab_user_size": 0, "vocab_learned_size": 0, "vocab_context_size": 0, "top_corrected_words": []}) + "\n"
        )
        with patch("contextpulse_voice.metrics.METRICS_FILE", metrics_file):
            report = generate_weekly_report()
        assert report["entries"] == 2
