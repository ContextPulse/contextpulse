# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for hot-word extraction from screen OCR events."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from contextpulse_voice.hot_words import (
    _COMMON_ACRONYMS,
    build_whisper_prompt,
    extract_hot_words,
)


def _make_db(tmp_path: Path, events: list[dict]) -> Path:
    """Create a temporary activity.db with test events."""
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
                evt.get("event_id", "test"),
                evt.get("timestamp", time.time()),
                evt.get("modality", "sight"),
                evt.get("event_type", "ocr_result"),
                evt.get("app_name", ""),
                evt.get("window_title", ""),
                evt.get("monitor_index", 0),
                json.dumps(evt.get("payload", {})),
                None, 0.0, 0.0,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestExtractHotWords:
    def test_camel_case_extraction(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "Working on ContextPulse and StockTrader today", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert "ContextPulse" in words
        assert "StockTrader" in words

    def test_acronym_extraction(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "Using DTS and CCXT for data feeds", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert "DTS" in words
        assert "CCXT" in words

    def test_common_acronyms_filtered(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "OK AM PM are common, but CCXT is not", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert "OK" not in words
        assert "AM" not in words
        assert "PM" not in words
        assert "CCXT" in words

    def test_frequency_sorting(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "ContextPulse ContextPulse StockTrader", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert words[0] == "ContextPulse"  # appears twice

    def test_max_words_limit(self, tmp_path):
        ocr_text = " ".join(f"Word{chr(65+i)}Name" for i in range(26))
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": ocr_text, "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60, max_words=5)
        assert len(words) <= 5

    def test_min_confidence_filtering(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "LowConfidence ContextPulse", "ocr_confidence": 0.3}},
            {"payload": {"ocr_text": "HighConfidence StockTrader", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60, min_confidence=0.75)
        assert "StockTrader" in words
        # LowConfidence event is below threshold, so ContextPulse shouldn't appear
        # (unless ContextPulse also appeared in the high-confidence event)
        assert "LowConfidence" not in words

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path, [])
        words = extract_hot_words(db_path=db, seconds=60)
        assert words == []

    def test_missing_db(self, tmp_path):
        words = extract_hot_words(db_path=tmp_path / "nonexistent.db", seconds=60)
        assert words == []

    def test_old_events_excluded(self, tmp_path):
        old_time = time.time() - 600  # 10 minutes ago
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "OldEvent ContextPulse", "ocr_confidence": 0.9}, "timestamp": old_time},
        ])
        words = extract_hot_words(db_path=db, seconds=60)  # only look back 60s
        assert words == []

    def test_deduplication(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "ContextPulse is great", "ocr_confidence": 0.9}},
            {"payload": {"ocr_text": "ContextPulse rocks", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert words.count("ContextPulse") == 1

    def test_short_camel_words_filtered(self, tmp_path):
        db = _make_db(tmp_path, [
            {"payload": {"ocr_text": "MyApp is short, ContextPulse is not", "ocr_confidence": 0.9}},
        ])
        words = extract_hot_words(db_path=db, seconds=60)
        assert "MyApp" not in words  # len < 6
        assert "ContextPulse" in words

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
            ("test", time.time(), "sight", "ocr_result", "", "", 0,
             "not-valid-json", None, 0.0, 0.0),
        )
        conn.commit()
        conn.close()
        words = extract_hot_words(db_path=db_path, seconds=60)
        assert words == []


class TestBuildWhisperPrompt:
    def test_basic_prompt(self):
        prompt = build_whisper_prompt(["ContextPulse", "StockTrader"])
        assert "ContextPulse" in prompt
        assert "StockTrader" in prompt

    def test_combines_hot_words_and_nouns(self):
        prompt = build_whisper_prompt(
            ["ContextPulse"], ["SwingPulse", "CryptoTrader"],
        )
        assert "ContextPulse" in prompt
        assert "SwingPulse" in prompt
        assert "CryptoTrader" in prompt

    def test_deduplication_preserves_hot_word_order(self):
        prompt = build_whisper_prompt(
            ["ContextPulse", "StockTrader"],
            ["ContextPulse", "SwingPulse"],
        )
        # ContextPulse should appear once, and hot-word version comes first
        assert prompt.count("ContextPulse") == 1
        assert prompt.index("ContextPulse") < prompt.index("SwingPulse")

    def test_truncation_at_max_length(self):
        long_words = [f"VeryLongWord{i:03d}" for i in range(50)]
        prompt = build_whisper_prompt(long_words, max_length=200)
        assert len(prompt) <= 200
        # Should end at a clean term boundary (no partial words)
        assert not prompt.endswith(",")

    def test_empty_inputs(self):
        assert build_whisper_prompt([]) == ""
        assert build_whisper_prompt([], []) == ""
        assert build_whisper_prompt([], None) == ""

    def test_single_word(self):
        prompt = build_whisper_prompt(["ContextPulse"])
        assert prompt == "ContextPulse"
