"""Tests for MCP server tools — transcription history and vocabulary queries."""

import json
import sqlite3
import time
from unittest.mock import patch

import pytest


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
