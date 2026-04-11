# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MeetingModule — Orchestrates meeting capture, transcription, and summarization.

Coordinates Sight and Voice modules during an active meeting to produce
real-time transcripts, action items, and post-meeting summaries.

Architecture:
    - Detects meeting start/end (calendar integration or manual trigger)
    - Captures screen at intervals during the meeting (via Sight)
    - Transcribes audio continuously (via Voice)
    - Correlates slides/screen content with transcript timestamps
    - Generates rolling summaries and action items via LLM
    - Emits MeetingEvents through the EventBus spine
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from contextpulse_core.spine import ContextEvent, EventType, Modality, ModalityModule

logger = logging.getLogger(__name__)


class MeetingState(Enum):
    """Meeting lifecycle states."""

    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDING = "ending"
    ENDED = "ended"


@dataclass
class MeetingSession:
    """Tracks a single meeting from start to end."""

    meeting_id: str = ""
    title: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    state: MeetingState = MeetingState.IDLE
    participants: list[str] = field(default_factory=list)
    transcript_segments: list[dict[str, Any]] = field(default_factory=list)
    screen_captures: list[dict[str, Any]] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    summary: str = ""


class MeetingModule(ModalityModule):
    """Orchestrates meeting capture by coordinating Sight + Voice.

    This module does NOT do its own screen capture or audio recording.
    Instead, it listens to events from SightModule and VoiceModule,
    correlates them with meeting context, and produces meeting-specific
    outputs (timeline, summary, action items).

    Usage:
        module = MeetingModule()
        module.register(event_bus.emit)
        module.start()

        # Manual meeting control:
        module.start_meeting("Weekly Standup")
        # ... meeting happens, Sight + Voice events flow through ...
        module.end_meeting()
        summary = module.get_summary()
    """

    def __init__(self) -> None:
        self._callback: Callable[[ContextEvent], None] | None = None
        self._running = False
        self._events_emitted = 0
        self._last_timestamp: float | None = None
        self._error: str | None = None
        self._current_session: MeetingSession | None = None
        self._sessions: list[MeetingSession] = []

    # ── ModalityModule Interface ─────────────────────────────────────

    def get_modality(self) -> Modality:
        # TODO: Add MEETING to Modality enum in spine/events.py
        # For now, reuse SYSTEM as the modality
        return Modality.SYSTEM

    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        self._callback = event_callback

    def start(self) -> None:
        self._running = True
        logger.info("MeetingModule started — waiting for meeting triggers")

    def stop(self) -> None:
        if self._current_session and self._current_session.state == MeetingState.ACTIVE:
            self.end_meeting()
        self._running = False
        logger.info("MeetingModule stopped")

    def is_alive(self) -> bool:
        return self._running

    def get_status(self) -> dict[str, Any]:
        return {
            "modality": "meeting",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_timestamp,
            "error": self._error,
            "meeting_active": (
                self._current_session is not None
                and self._current_session.state == MeetingState.ACTIVE
            ),
            "total_sessions": len(self._sessions),
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "capture_interval_seconds": {
                "type": "int",
                "default": 30,
                "description": "Screen capture interval during meetings",
            },
            "auto_detect_meetings": {
                "type": "bool",
                "default": True,
                "description": "Auto-detect meeting apps (Zoom, Teams, Meet, etc.)",
            },
            "summarize_on_end": {
                "type": "bool",
                "default": True,
                "description": "Generate AI summary when meeting ends",
            },
            "extract_action_items": {
                "type": "bool",
                "default": True,
                "description": "Extract action items from transcript",
            },
            "meeting_apps": {
                "type": "list",
                "default": ["zoom", "teams", "meet", "webex", "slack"],
                "description": "App names that indicate an active meeting",
            },
        }

    # ── Meeting Lifecycle ────────────────────────────────────────────

    def start_meeting(self, title: str = "", participants: list[str] | None = None) -> str:
        """Begin a new meeting session. Returns meeting_id."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def end_meeting(self) -> MeetingSession | None:
        """End the current meeting and trigger summarization."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def pause_meeting(self) -> None:
        """Pause capture (e.g., during a break)."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def resume_meeting(self) -> None:
        """Resume capture after a pause."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    # ── Event Handlers (called by EventBus) ──────────────────────────

    def on_screen_capture(self, event: ContextEvent) -> None:
        """Handle a Sight screen capture during an active meeting."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def on_transcription(self, event: ContextEvent) -> None:
        """Handle a Voice transcription during an active meeting."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    # ── Outputs ──────────────────────────────────────────────────────

    def get_summary(self) -> str:
        """Get the AI-generated summary of the current/last meeting."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def get_action_items(self) -> list[str]:
        """Get extracted action items from the current/last meeting."""
        raise NotImplementedError("Pending rebuild from spec — see README")

    def get_timeline(self) -> list[dict[str, Any]]:
        """Get the correlated timeline (transcript + screen captures)."""
        raise NotImplementedError("Pending rebuild from spec — see README")
