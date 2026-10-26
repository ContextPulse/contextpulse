# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""facts_about falls back to the raw capture log when no fact names the subject.

Action cp-facts-about-raw-fallback-word-match (usefulness review, 2026-10-24):
"Ananth" had 0 facts and 53 raw events, "brief mode" 0 facts and 172 events, and
3 of 6 real-question misses had raw matches. A miss told the caller the subject
was absent when the capture held it.

The fallback must not reopen the query oracle closed in search_filter.py: every
match and every count is decided on REDACTED text, so probing for a secret
token returns nothing. The raw rows are labelled as raw capture, never as facts,
and do not count as hits in tool_usage (the recall-hits figure measures facts).
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from contextpulse_core import probe, probe_mcp
from contextpulse_core.spine.bus import EventBus
from contextpulse_core.spine.events import ContextEvent, EventType, Modality


def _activity_db(path, rows: list[tuple[str, str, float]]):
    """Build a real activity.db (EventBus schema + FTS triggers) from (title, ocr, age_s)."""
    bus = EventBus(path)
    now = time.time()
    for title, text, age in rows:
        bus.emit(
            ContextEvent(
                timestamp=now - age,
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                app_name="chrome.exe",
                window_title=title,
                payload={"ocr_text": text},
            )
        )
    bus.close()
    return path


class TestQueryRawMentions:
    def test_finds_a_subject_only_the_raw_log_holds(self, tmp_path):
        db = _activity_db(tmp_path / "activity.db", [("Mail", "Meeting with Ananth about the roadmap", 60)])
        result = probe.query_raw_mentions(db, "Ananth")
        assert result is not None
        assert result.count == 1
        assert "Ananth" in result.samples[0]["snippet"]

    def test_multi_word_subject_matches_as_a_phrase(self, tmp_path):
        db = _activity_db(
            tmp_path / "activity.db",
            [("Doc", "turn on brief mode for the agent", 60), ("Doc", "brief notes, mode switch later", 30)],
        )
        result = probe.query_raw_mentions(db, "brief mode")
        assert result is not None and result.count == 1

    def test_whole_word_only(self, tmp_path):
        db = _activity_db(tmp_path / "activity.db", [("Bank", "transaction cleared overnight", 60)])
        result = probe.query_raw_mentions(db, "CLEAR")
        assert result is not None and result.count == 0

    def test_secret_token_is_not_an_oracle(self, tmp_path):
        key = "AKIAZQRAWFALLBACK012"
        db = _activity_db(tmp_path / "activity.db", [("Console", f"access key {key} shown", 60)])
        result = probe.query_raw_mentions(db, key)
        assert result is not None and result.count == 0

    def test_secret_in_window_title_is_not_an_oracle(self, tmp_path):
        key = "AKIAZQRAWFALLBACK012"
        db = _activity_db(tmp_path / "activity.db", [(f"key {key}", "nothing", 60)])
        result = probe.query_raw_mentions(db, key)
        assert result is not None and result.count == 0

    def test_snippet_is_redacted(self, tmp_path):
        key = "AKIAZQRAWFALLBACK012"
        db = _activity_db(tmp_path / "activity.db", [("Console", f"Ananth pasted {key} here", 60)])
        result = probe.query_raw_mentions(db, "Ananth")
        assert result is not None and result.count == 1
        assert key not in result.samples[0]["snippet"]

    def test_newest_first_and_samples_capped(self, tmp_path):
        rows = [("T", f"Ananth note {i}", 1000 - i) for i in range(8)]
        db = _activity_db(tmp_path / "activity.db", rows)
        result = probe.query_raw_mentions(db, "Ananth", samples=3)
        assert result is not None and result.count == 8
        assert len(result.samples) == 3
        assert "note 7" in result.samples[0]["snippet"]

    def test_missing_db_means_not_searched(self, tmp_path):
        assert probe.query_raw_mentions(tmp_path / "absent.db", "Ananth") is None

    def test_empty_or_stopword_query_returns_nothing(self, tmp_path):
        db = _activity_db(tmp_path / "activity.db", [("T", "anything", 60)])
        result = probe.query_raw_mentions(db, "  ")
        assert result is not None and result.count == 0

    def test_quote_in_subject_does_not_break_fts(self, tmp_path):
        db = _activity_db(tmp_path / "activity.db", [("T", 'he said "Ananth" twice', 60)])
        result = probe.query_raw_mentions(db, 'Ananth"')
        assert result is not None and result.count == 1

    def test_opens_read_only(self, tmp_path):
        db = _activity_db(tmp_path / "activity.db", [("T", "Ananth", 60)])
        before = db.read_bytes()
        probe.query_raw_mentions(db, "Ananth")
        assert db.read_bytes() == before


class TestFactsAboutMissFallsBackToRaw:
    @pytest.fixture
    def dbs(self, tmp_path, monkeypatch):
        pdb = tmp_path / "probe.db"
        adb = tmp_path / "activity.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(pdb))
        monkeypatch.setenv("CONTEXTPULSE_ACTIVITY_DB", str(adb))
        conn = probe.connect_probe(pdb)
        probe.write_facts(conn, [{"entity": "Other", "fact": "unrelated", "valid_from": 1.0, "source_event_ids": []}])
        conn.close()
        return pdb, adb

    def test_miss_with_raw_mentions_is_labelled_and_not_counted_as_a_hit(self, dbs):
        pdb, adb = dbs
        _activity_db(adb, [("Mail", "Meeting with Ananth about the roadmap", 60)])
        out = probe_mcp.facts_about("Ananth")
        assert out.startswith("No probe facts about 'Ananth'")
        assert "raw capture" in out.lower()
        assert "not distilled facts" in out
        assert "Meeting with Ananth" in out
        conn = sqlite3.connect(str(pdb))
        assert conn.execute("SELECT hit_count FROM tool_usage").fetchall() == [(0,)]
        conn.close()

    def test_miss_with_no_raw_mentions_says_so(self, dbs):
        _, adb = dbs
        _activity_db(adb, [("Mail", "nothing relevant", 60)])
        out = probe_mcp.facts_about("Ananth")
        assert "raw capture has no mention" in out.lower()

    def test_fact_hit_does_not_add_raw_section(self, dbs):
        _, adb = dbs
        _activity_db(adb, [("Mail", "Other again", 60)])
        out = probe_mcp.facts_about("Other")
        assert "raw capture" not in out.lower()
