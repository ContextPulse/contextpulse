# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Meeting detection — identifies when the user is in a meeting.

Detection strategies (to be implemented from spec):
    1. App-based: Detect meeting apps in foreground (Zoom, Teams, Meet, etc.)
    2. Calendar-based: Check Google Calendar for current/upcoming meetings
    3. Audio-based: Detect multi-party conversation patterns
    4. Manual: User triggers via hotkey or tray menu

The detector emits MEETING_START / MEETING_END events through the spine.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Meeting app detection patterns — window titles / process names
# These will be refined based on the work-machine spec
MEETING_APP_PATTERNS: dict[str, list[str]] = {
    "zoom": ["zoom meeting", "zoom", "zoom.us"],
    "teams": ["microsoft teams", "teams"],
    "meet": ["google meet", "meet.google.com"],
    "webex": ["webex", "cisco webex"],
    "slack": ["slack huddle", "slack call"],
    "chime": ["amazon chime"],
}


class MeetingDetector:
    """Detects meeting start/end from window titles and app names.

    Monitors Sight events for meeting app patterns and emits
    meeting lifecycle events.
    """

    def __init__(self, app_patterns: dict[str, list[str]] | None = None) -> None:
        self._patterns = app_patterns or MEETING_APP_PATTERNS
        self._active_meeting_app: str | None = None
        self._meeting_start_time: float | None = None

    def check_window(self, app_name: str, window_title: str) -> dict[str, Any] | None:
        """Check if a window indicates an active meeting.

        Returns a detection dict if meeting detected, None otherwise.
        """
        raise NotImplementedError("Pending rebuild from spec")

    def is_meeting_active(self) -> bool:
        """Whether a meeting is currently detected."""
        return self._active_meeting_app is not None
