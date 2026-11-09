# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""context_at falls back to the raw capture log when no fact falls in the window.

Measured 2026-11-04: both real context_at calls returned nothing, because facts
are distilled every 6 hours and carry one timestamp each, while the raw capture
held events for those windows. The server instructions send every "what was I
doing around <time>" question to this tool, so a bare miss answered it wrongly.

What the fallback may show: window titles, app names, event counts and dictated
words, all redacted. It never shows clipboard text, which is the likeliest place
for a secret. Raw rows are labelled as raw and do not count as hits in tool_usage.
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from contextpulse_core import probe, probe_mcp
from contextpulse_core.spine.bus import EventBus
from contextpulse_core.spine.events import ContextEvent, EventType, Modality

KEY = "AKIAZQRAWFALLBACK012"
NOW = time.time() - 86400.0


def _emit(bus: EventBus, age: float, modality: Modality, event_type: EventType, title: str, payload: dict,
          app: str = "chrome.exe") -> None:
    bus.emit(
        ContextEvent(
            timestamp=NOW - age,
            modality=modality,
            event_type=event_type,
            app_name=app,
            window_title=title,
            payload=payload,
        )
    )


def _screen(bus: EventBus, age: float, title: str, app: str = "chrome.exe", text: str = "screen words") -> None:
    _emit(bus, age, Modality.SIGHT, EventType.OCR_RESULT, title, {"ocr_text": text}, app)


def _voice(bus: EventBus, age: float, words: str) -> None:
    _emit(bus, age, Modality.VOICE, EventType.TRANSCRIPTION, "Notes", {"transcript": words}, "wezterm-gui.exe")


@pytest.fixture
def activity(tmp_path):
    bus = EventBus(tmp_path / "activity.db")
    yield bus, tmp_path / "activity.db"
    bus.close()


class TestQueryRawWindow:
    def test_counts_titles_and_modalities_inside_the_window_only(self, activity):
        bus, db = activity
        _screen(bus, 60, "Budget - Sheets")
        _screen(bus, 120, "Budget - Sheets")
        _screen(bus, 180, "Inbox", app="outlook.exe")
        _voice(bus, 200, "call the bank tomorrow")
        _screen(bus, 4000, "Outside the window")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.count == 4
        assert result.by_modality == {"sight": 3, "voice": 1}
        assert result.windows[0]["title"] == "Budget - Sheets"
        assert result.windows[0]["count"] == 2
        assert all(w["title"] != "Outside the window" for w in result.windows)

    def test_window_reports_its_first_and_last_seen_times(self, activity):
        bus, db = activity
        _screen(bus, 60, "Budget")
        _screen(bus, 300, "Budget")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.windows[0]["first"] == pytest.approx(NOW - 300)
        assert result.windows[0]["last"] == pytest.approx(NOW - 60)

    def test_window_list_is_capped_and_the_total_is_still_reported(self, activity):
        bus, db = activity
        for i in range(12):
            _screen(bus, 60 + i, f"Title {i}")
        result = probe.query_raw_window(db, NOW, window_s=600, top_windows=5)
        assert result is not None
        assert len(result.windows) == 5
        assert result.distinct_windows == 12

    def test_dictation_is_returned_newest_first_and_capped(self, activity):
        bus, db = activity
        for i in range(5):
            _voice(bus, 100 - i, f"note {i}")
        result = probe.query_raw_window(db, NOW, window_s=600, dictation_samples=2)
        assert result is not None
        assert [d["text"] for d in result.dictation] == ["note 4", "note 3"]

    def test_secret_in_title_is_redacted(self, activity):
        bus, db = activity
        _screen(bus, 60, f"console {KEY}")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert KEY not in repr(result)
        assert result.windows[0]["title"].startswith("console ")
        assert "REDACTED" in result.windows[0]["title"]

    def test_secret_in_dictation_is_redacted(self, activity):
        bus, db = activity
        _voice(bus, 60, f"the key is {KEY}")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert KEY not in repr(result)
        assert result.dictation[0]["text"].startswith("the key is ")
        assert "REDACTED" in result.dictation[0]["text"]

    def test_secret_in_app_name_is_redacted(self, activity):
        bus, db = activity
        _screen(bus, 60, "Console", app=f"tool-{KEY}.exe")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert KEY not in repr(result)
        assert "REDACTED" in result.windows[0]["app"]

    def test_titles_differing_only_by_a_redacted_secret_merge(self, activity):
        # Grouped on raw text they would be two rows with the same printed title,
        # and the row count would reveal that two different secrets were seen.
        bus, db = activity
        _screen(bus, 60, f"console {KEY}")
        _screen(bus, 90, "console AKIAZQRAWFALLBACK999")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.distinct_windows == 1
        assert result.windows[0]["count"] == 2

    def test_clipboard_and_screen_text_are_never_returned(self, activity):
        bus, db = activity
        _emit(bus, 60, Modality.CLIPBOARD, EventType.CLIPBOARD_CHANGE, "Notes", {"text": "pasted-marker-words"})
        _screen(bus, 90, "Notes", text="screen-marker-words")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.by_modality == {"clipboard": 1, "sight": 1}
        assert "pasted-marker-words" not in repr(result)
        assert "screen-marker-words" not in repr(result)

    def test_voice_row_with_no_words_is_counted_and_not_shown(self, activity):
        bus, db = activity
        _voice(bus, 60, "good words")
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO events (event_id, timestamp, modality, event_type, app_name, window_title,"
            " monitor_index, payload, created_at) VALUES ('bad', ?, 'voice', 'transcription', 'a', 't', 0, '[1]', ?)",
            (NOW - 30, NOW),
        )
        conn.commit()
        conn.close()
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.count == 2
        assert [d["text"] for d in result.dictation] == ["good words"]

    def test_empty_window_is_a_zero_not_none(self, activity):
        bus, db = activity
        _screen(bus, 90_000, "Long ago")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.count == 0
        assert result.windows == []

    def test_missing_db_means_not_searched(self, tmp_path):
        assert probe.query_raw_window(tmp_path / "absent.db", NOW, window_s=600) is None

    def test_db_without_events_table_means_not_searched(self, tmp_path):
        db = tmp_path / "activity.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other (x)")
        conn.commit()
        conn.close()
        assert probe.query_raw_window(db, NOW, window_s=600) is None

    def test_opens_read_only(self, activity, monkeypatch):
        bus, db = activity
        _screen(bus, 60, "Budget")
        bus.close()
        before = db.read_bytes()
        opened: list[str] = []
        real_connect = sqlite3.connect

        def spy(target, *args, **kwargs):
            opened.append(str(target))
            return real_connect(target, *args, **kwargs)

        monkeypatch.setattr(probe.sqlite3, "connect", spy)
        probe.query_raw_window(db, NOW, window_s=600)
        assert opened and all("mode=ro" in target for target in opened)
        assert db.read_bytes() == before

    def test_newline_in_a_title_cannot_forge_an_output_line(self, activity):
        bus, db = activity
        _screen(bus, 60, "Page\n- forged line\r\n  more")
        result = probe.query_raw_window(db, NOW, window_s=600)
        assert result is not None
        assert result.windows[0]["title"] == "Page - forged line more"

    def test_voice_rows_with_no_words_do_not_use_up_the_samples(self, activity):
        bus, db = activity
        _voice(bus, 300, "older real words")
        for i in range(3):
            _emit(bus, 60 + i, Modality.VOICE, EventType.TRANSCRIPTION, "Notes", {"raw_transcript": "x", "transcript": ""})
        result = probe.query_raw_window(db, NOW, window_s=600, dictation_samples=2)
        assert result is not None
        assert [d["text"] for d in result.dictation] == ["older real words"]


class TestContextAtTool:
    @pytest.fixture
    def stores(self, tmp_path, monkeypatch):
        probe_db = tmp_path / "probe.db"
        activity_db = tmp_path / "activity.db"
        monkeypatch.setenv("CONTEXTPULSE_PROBE_DB", str(probe_db))
        monkeypatch.setenv("CONTEXTPULSE_ACTIVITY_DB", str(activity_db))
        return probe_db, activity_db

    def _usage(self, probe_db) -> list[tuple[str, int]]:
        conn = sqlite3.connect(probe_db)
        rows = conn.execute("SELECT tool, hit_count FROM tool_usage").fetchall()
        conn.close()
        return rows

    def test_miss_shows_raw_capture_labelled_as_raw(self, stores):
        probe_db, activity_db = stores
        bus = EventBus(activity_db)
        _screen(bus, 60, "Budget - Sheets")
        _voice(bus, 90, "call the bank tomorrow")
        bus.close()
        out = probe_mcp.context_at(str(NOW), window_minutes=30)
        assert "No probe facts" in out
        assert "not distilled facts" in out
        assert "Budget - Sheets" in out
        assert "call the bank tomorrow" in out

    def test_labels_carry_the_date_and_a_second_date_across_midnight(self):
        first = time.mktime((2026, 10, 10, 23, 50, 0, 0, 0, -1))
        assert probe_mcp._fmt_span(first, first + 300) == "2026-10-10 23:50 to 23:55"
        assert probe_mcp._fmt_span(first, first + 1200) == "2026-10-10 23:50 to 2026-10-11 00:10"

    def test_raw_rows_do_not_count_as_hits(self, stores):
        probe_db, activity_db = stores
        bus = EventBus(activity_db)
        _screen(bus, 60, "Budget")
        bus.close()
        probe_mcp.context_at(str(NOW), window_minutes=30)
        assert self._usage(probe_db) == [("context_at", 0)]

    def test_a_fact_in_the_window_answers_without_raw_capture(self, stores):
        probe_db, activity_db = stores
        bus = EventBus(activity_db)
        _screen(bus, 60, "Raw title that must not appear")
        bus.close()
        conn = probe.connect_probe(probe_db)
        conn.execute(
            "INSERT INTO facts (entity, fact, valid_from, source_event_ids, confidence, created_at)"
            " VALUES ('Budget', 'David approved the budget', ?, '[]', 0.9, ?)",
            (NOW - 30, NOW),
        )
        conn.commit()
        conn.close()
        out = probe_mcp.context_at(str(NOW), window_minutes=30)
        assert "David approved the budget" in out
        assert "Raw title that must not appear" not in out
        assert self._usage(probe_db) == [("context_at", 1)]

    def test_miss_with_empty_capture_says_so(self, stores):
        _, activity_db = stores
        EventBus(activity_db).close()
        out = probe_mcp.context_at(str(NOW), window_minutes=30)
        assert "Raw capture holds nothing" in out

    def test_miss_with_no_capture_db_says_not_searched(self, stores):
        out = probe_mcp.context_at(str(NOW), window_minutes=30)
        assert "not searched" in out

    def test_fallback_failure_does_not_lose_the_miss_answer(self, stores, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("capture store exploded")

        monkeypatch.setattr(probe, "query_raw_window", boom)
        out = probe_mcp.context_at(str(NOW), window_minutes=30)
        assert "No probe facts" in out
        assert "not searched" in out

    def test_recent_miss_says_when_facts_were_last_distilled(self, stores):
        probe_db, _ = stores
        conn = probe.connect_probe(probe_db)
        conn.execute(
            "INSERT INTO probe_runs (ran_at, events, facts, elapsed_s) VALUES (?, 1, 1, 1.0)", (time.time() - 3600,)
        )
        conn.commit()
        conn.close()
        out = probe_mcp.context_at("now", window_minutes=30)
        assert "Facts were last distilled at" in out

    def test_a_failed_run_does_not_count_as_distillation(self, stores):
        probe_db, _ = stores
        conn = probe.connect_probe(probe_db)
        conn.execute(
            "INSERT INTO probe_runs (ran_at, events, facts, error, elapsed_s) VALUES (?, 1, 0, 'boom', 1.0)",
            (time.time() - 60,),
        )
        conn.commit()
        conn.close()
        out = probe_mcp.context_at("now", window_minutes=30)
        assert "last distilled" not in out

    def test_old_miss_does_not_mention_the_last_distillation(self, stores):
        probe_db, _ = stores
        conn = probe.connect_probe(probe_db)
        conn.execute(
            "INSERT INTO probe_runs (ran_at, events, facts, elapsed_s) VALUES (?, 1, 1, 1.0)", (time.time() - 3600,)
        )
        conn.commit()
        conn.close()
        out = probe_mcp.context_at(str(time.time() - 30 * 86400), window_minutes=30)
        assert "last distilled" not in out

    def test_recent_miss_with_no_run_recorded_still_answers(self, stores):
        out = probe_mcp.context_at("now", window_minutes=30)
        assert "No probe facts" in out
        assert "last distilled" not in out


class TestToolDescriptions:
    """A model picks tools from these strings, so they must say what the tool answers."""

    @pytest.mark.parametrize("tool", [probe_mcp.facts_about, probe_mcp.context_at])
    def test_no_internal_project_label(self, tool):
        assert "phase 0" not in tool.__doc__.lower()
        assert "probe" not in tool.__doc__.lower()

    def test_facts_about_says_what_it_answers(self):
        doc = probe_mcp.facts_about.__doc__.lower()
        for word in ("decided", "why", "person", "project"):
            assert word in doc

    def test_context_at_says_what_it_answers(self):
        doc = probe_mcp.context_at.__doc__.lower()
        assert "what was happening" in doc
        assert "raw capture" in doc

    def test_tool_descriptions_reach_the_registered_tools(self):
        # The string a client sees comes from the FastMCP registry, not __doc__.
        registered = {t.name: t.description for t in probe_mcp.mcp_app._tool_manager.list_tools()}
        assert "phase 0" not in registered["facts_about"].lower()
        assert "phase 0" not in registered["context_at"].lower()
        assert "what was decided" in registered["facts_about"].lower()
        assert "what was happening" in registered["context_at"].lower()
