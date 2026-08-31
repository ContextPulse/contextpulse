"""Tests for MCP server tools — transcription history and vocabulary queries."""

import sqlite3
from unittest.mock import patch


class TestGetRecentTranscriptions:
    def test_returns_transcriptions(self, activity_db):
        from contextpulse_voice.mcp_server import get_recent_transcriptions
        with patch("contextpulse_voice.mcp_server._DB_PATH", activity_db):
            result = get_recent_transcriptions(minutes=60, limit=10)
        assert "Recent Transcriptions" in result
        assert "cleaned text" in result

    def test_no_db(self, tmp_path):
        from contextpulse_voice.mcp_server import get_recent_transcriptions
        with patch("contextpulse_voice.mcp_server._DB_PATH", tmp_path / "nope.db"):
            result = get_recent_transcriptions()
        assert "No activity database" in result

    def test_no_recent(self, tmp_path):
        # Create empty DB
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, timestamp REAL, modality TEXT,
                event_type TEXT, app_name TEXT, window_title TEXT,
                monitor_index INTEGER, payload TEXT, correlation_id TEXT,
                attention_score REAL
            )
        """)
        conn.commit()
        conn.close()

        from contextpulse_voice.mcp_server import get_recent_transcriptions
        with patch("contextpulse_voice.mcp_server._DB_PATH", db_path):
            result = get_recent_transcriptions(minutes=1)
        assert "No transcriptions" in result


class TestGetVoiceStats:
    def test_returns_stats(self, activity_db):
        from contextpulse_voice.mcp_server import get_voice_stats
        with patch("contextpulse_voice.mcp_server._DB_PATH", activity_db):
            result = get_voice_stats(hours=24)
        assert "Voice Stats" in result
        assert "Total dictations: 5" in result

    def test_no_db(self, tmp_path):
        from contextpulse_voice.mcp_server import get_voice_stats
        with patch("contextpulse_voice.mcp_server._DB_PATH", tmp_path / "nope.db"):
            result = get_voice_stats()
        assert "No activity database" in result

    def test_zero_rows_names_the_local_capture_scope(self, tmp_path):
        """A zero-row result must say WHY it might be zero (local-only capture),
        not just report a bare count that reads identically to "nothing to learn
        from" when the real cause is "this channel is not instrumented" --
        see voice-learning-blind-to-mobile-dictation."""
        import sqlite3

        from contextpulse_voice.mcp_server import get_voice_stats

        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, timestamp REAL, modality TEXT,
                event_type TEXT, app_name TEXT, window_title TEXT,
                monitor_index INTEGER, payload TEXT, correlation_id TEXT,
                attention_score REAL
            )
        """)
        conn.commit()
        conn.close()

        with patch("contextpulse_voice.mcp_server._DB_PATH", db_path):
            result = get_voice_stats(hours=24)

        assert "No dictations" in result
        assert "local ContextPulse voice capture only" in result
        assert "not instrumented" in result

    def test_zero_llm_cleanups_names_missing_api_key(self, tmp_path):
        """A 0/N LLM-cleanup result must say WHY when the cause is 'never
        configured', not read as 'nothing needed correction' -- see
        cp-dictation-cleanup-stage-inert. Real 2026-08-31 data: 20/20 recent
        dictations had cleanup_applied=False because no API key was ever set,
        and the tool gave no signal that anything was unconfigured."""
        import json
        import sqlite3
        import time

        from contextpulse_voice.mcp_server import get_voice_stats

        db_path = tmp_path / "activity.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, timestamp REAL, modality TEXT,
                event_type TEXT, app_name TEXT, window_title TEXT,
                monitor_index INTEGER, payload TEXT, correlation_id TEXT,
                attention_score REAL
            )
        """)
        now = time.time()
        conn.execute(
            "INSERT INTO events VALUES ('e0', ?, 'voice', 'transcription', '', '', 0, ?, NULL, 0.0)",
            (now, json.dumps({
                "transcript": "trigger a sell", "raw_transcript": "trigger a sell",
                "duration_seconds": 2.0, "cleanup_applied": False,
            })),
        )
        conn.commit()
        conn.close()

        with patch("contextpulse_voice.mcp_server._DB_PATH", db_path), \
             patch("contextpulse_voice.mcp_server.has_api_key", return_value=False):
            result = get_voice_stats(hours=24)

        assert "LLM cleanups: 0" in result
        assert "NOT CONFIGURED" in result
        assert "API key" in result

    def test_zero_llm_cleanups_names_disabled_toggle(self, tmp_path):
        """Same zero, different cause: a key IS set but always_use_llm is off."""
        import json
        import sqlite3
        import time

        from contextpulse_voice.mcp_server import get_voice_stats

        db_path = tmp_path / "activity.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, timestamp REAL, modality TEXT,
                event_type TEXT, app_name TEXT, window_title TEXT,
                monitor_index INTEGER, payload TEXT, correlation_id TEXT,
                attention_score REAL
            )
        """)
        now = time.time()
        conn.execute(
            "INSERT INTO events VALUES ('e0', ?, 'voice', 'transcription', '', '', 0, ?, NULL, 0.0)",
            (now, json.dumps({
                "transcript": "x", "raw_transcript": "x",
                "duration_seconds": 2.0, "cleanup_applied": False,
            })),
        )
        conn.commit()
        conn.close()

        with patch("contextpulse_voice.mcp_server._DB_PATH", db_path), \
             patch("contextpulse_voice.mcp_server.has_api_key", return_value=True), \
             patch(
                 "contextpulse_voice.mcp_server.get_voice_config",
                 return_value={"always_use_llm": False},
             ):
            result = get_voice_stats(hours=24)

        assert "DISABLED" in result
        assert "voice_always_use_llm" in result

    def test_nonzero_llm_cleanups_adds_no_gap_note(self, activity_db):
        """When LLM cleanup DID run for some rows, no diagnostic note is
        appended -- the note is only for the genuinely-zero case."""
        from contextpulse_voice.mcp_server import get_voice_stats

        with patch("contextpulse_voice.mcp_server._DB_PATH", activity_db):
            result = get_voice_stats(hours=24)

        assert "LLM cleanups: 3" in result
        assert "NOT CONFIGURED" not in result
        assert "DISABLED" not in result


class TestGetVocabulary:
    def test_returns_all(self, tmp_path):
        from contextpulse_voice.mcp_server import get_vocabulary
        with patch("contextpulse_voice.vocabulary.get_all_entries", return_value={"a": "A", "b": "B"}):
            result = get_vocabulary(learned_only=False)
        assert "All" in result
        assert "2 entries" in result

    def test_returns_learned_only(self):
        from contextpulse_voice.mcp_server import get_vocabulary
        with patch("contextpulse_voice.vocabulary.get_learned_entries", return_value={"x": "Y"}):
            result = get_vocabulary(learned_only=True)
        assert "Auto-Learned" in result
        assert "1 entries" in result

    def test_empty_vocabulary(self):
        from contextpulse_voice.mcp_server import get_vocabulary
        with patch("contextpulse_voice.vocabulary.get_all_entries", return_value={}):
            result = get_vocabulary(learned_only=False)
        assert "No" in result
