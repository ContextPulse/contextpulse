# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Meeting timeline — correlates transcript segments with screen captures.

Builds a unified timeline that maps what was said to what was on screen,
enabling slide-aware transcripts and visual meeting records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TimelineEntry:
    """A single entry in the meeting timeline."""

    timestamp: float = 0.0
    entry_type: str = ""  # "transcript", "screen_change", "action_item", "topic_change"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # For screen changes: screenshot path, OCR text, detected slide number
    # For transcript: speaker (if detectable), confidence
    # For action items: assignee, deadline


class MeetingTimeline:
    """Builds and queries a correlated meeting timeline.

    Merges transcript events and screen captures into a single
    chronological timeline with cross-references.
    """

    def __init__(self) -> None:
        self._entries: list[TimelineEntry] = []

    def add_transcript(self, timestamp: float, text: str, **metadata: Any) -> None:
        """Add a transcript segment to the timeline."""
        raise NotImplementedError("Pending rebuild from spec")

    def add_screen_capture(
        self, timestamp: float, screenshot_path: str, ocr_text: str = "", **metadata: Any
    ) -> None:
        """Add a screen capture to the timeline."""
        raise NotImplementedError("Pending rebuild from spec")

    def get_entries(
        self, start_time: float = 0, end_time: float = 0, entry_type: str = ""
    ) -> list[TimelineEntry]:
        """Query timeline entries with optional filters."""
        raise NotImplementedError("Pending rebuild from spec")

    def get_slide_transcript(self) -> list[dict[str, Any]]:
        """Group transcript segments by detected slide/screen changes."""
        raise NotImplementedError("Pending rebuild from spec")

    def export_markdown(self) -> str:
        """Export the full timeline as a readable markdown document."""
        raise NotImplementedError("Pending rebuild from spec")
