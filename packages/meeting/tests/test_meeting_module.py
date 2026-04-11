# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for MeetingModule — validates spine contract and lifecycle."""

import pytest

from contextpulse_meeting.meeting_module import MeetingModule, MeetingSession, MeetingState


class TestMeetingModuleContract:
    """MeetingModule must satisfy the ModalityModule interface."""

    def test_implements_modality_module(self):
        from contextpulse_core.spine import ModalityModule

        assert issubclass(MeetingModule, ModalityModule)

    def test_instantiation(self):
        m = MeetingModule()
        assert m.is_alive() is False

    def test_start_stop(self):
        m = MeetingModule()
        m.register(lambda e: None)
        m.start()
        assert m.is_alive() is True
        m.stop()
        assert m.is_alive() is False

    def test_get_status_keys(self):
        m = MeetingModule()
        status = m.get_status()
        required_keys = {
            "modality", "running", "events_emitted",
            "last_event_timestamp", "error",
        }
        assert required_keys.issubset(status.keys())

    def test_config_schema_not_empty(self):
        m = MeetingModule()
        schema = m.get_config_schema()
        assert len(schema) > 0
        assert "capture_interval_seconds" in schema
        assert "auto_detect_meetings" in schema


class TestMeetingSession:
    """MeetingSession data class tests."""

    def test_default_state(self):
        s = MeetingSession()
        assert s.state == MeetingState.IDLE
        assert s.participants == []
        assert s.action_items == []

    def test_state_enum_values(self):
        assert MeetingState.IDLE.value == "idle"
        assert MeetingState.ACTIVE.value == "active"
        assert MeetingState.ENDED.value == "ended"


class TestDetector:
    """MeetingDetector tests."""

    def test_import(self):
        from contextpulse_meeting.detector import MeetingDetector
        d = MeetingDetector()
        assert d.is_meeting_active() is False

    def test_default_patterns(self):
        from contextpulse_meeting.detector import MEETING_APP_PATTERNS
        assert "zoom" in MEETING_APP_PATTERNS
        assert "teams" in MEETING_APP_PATTERNS
        assert "meet" in MEETING_APP_PATTERNS
        assert "chime" in MEETING_APP_PATTERNS


class TestSummarizer:
    """MeetingSummarizer tests."""

    def test_import(self):
        from contextpulse_meeting.summarizer import MeetingSummarizer
        s = MeetingSummarizer()
        assert s._model is not None

    def test_summary_dataclass(self):
        from contextpulse_meeting.summarizer import MeetingSummary
        ms = MeetingSummary(executive_summary="Test", duration_minutes=30.0)
        assert ms.executive_summary == "Test"
        assert ms.duration_minutes == 30.0


class TestTimeline:
    """MeetingTimeline tests."""

    def test_import(self):
        from contextpulse_meeting.timeline import MeetingTimeline
        t = MeetingTimeline()
        assert t._entries == []

    def test_entry_dataclass(self):
        from contextpulse_meeting.timeline import TimelineEntry
        e = TimelineEntry(timestamp=1.0, entry_type="transcript", content="Hello")
        assert e.content == "Hello"
        assert e.metadata == {}
