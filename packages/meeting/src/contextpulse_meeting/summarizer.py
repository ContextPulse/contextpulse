# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Meeting summarization — generates summaries and action items from meeting data.

Takes correlated transcript + screen capture data and produces:
    1. Executive summary (2-3 paragraphs)
    2. Key decisions made
    3. Action items with assignees (if detectable)
    4. Topics discussed (with timestamps)
    5. Follow-up questions

Uses Claude API for summarization. Supports both real-time rolling
summaries during the meeting and comprehensive post-meeting summaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MeetingSummary:
    """Structured meeting summary output."""

    executive_summary: str = ""
    key_decisions: list[str] | None = None
    action_items: list[dict[str, str]] | None = None  # [{task, assignee, deadline}]
    topics: list[dict[str, Any]] | None = None  # [{topic, start_time, end_time}]
    follow_ups: list[str] | None = None
    raw_transcript: str = ""
    duration_minutes: float = 0.0


class MeetingSummarizer:
    """Generates AI-powered meeting summaries from transcript + screen data.

    Supports two modes:
        - Rolling: Summarize every N minutes during the meeting
        - Final: Comprehensive summary when meeting ends
    """

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-5-20250514") -> None:
        self._api_key = api_key
        self._model = model

    def summarize_rolling(
        self, transcript_chunk: str, screen_context: list[str] | None = None
    ) -> str:
        """Generate a rolling summary of the last N minutes."""
        raise NotImplementedError("Pending rebuild from spec")

    def summarize_final(
        self,
        full_transcript: str,
        screen_captures: list[dict[str, Any]] | None = None,
        meeting_title: str = "",
    ) -> MeetingSummary:
        """Generate a comprehensive post-meeting summary."""
        raise NotImplementedError("Pending rebuild from spec")

    def extract_action_items(self, transcript: str) -> list[dict[str, str]]:
        """Extract action items with assignees from transcript."""
        raise NotImplementedError("Pending rebuild from spec")
