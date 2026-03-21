"""Event-driven capture detection: window focus, monitor boundary, idle-then-active."""

import logging
import math
import threading
import time

from contextpulse_sight.config import (
    EVENT_IDLE_THRESHOLD,
    EVENT_MOVEMENT_THRESHOLD,
    EVENT_POLL_INTERVAL,
)
from contextpulse_sight.privacy import get_foreground_window_title

logger = logging.getLogger("contextpulse.sight.events")


class EventDetector:
    """Detects significant user activity events that warrant a screen capture.

    Polls at ~2Hz for:
    1. Window focus change (foreground title changes)
    2. Monitor boundary cross (cursor moves to different monitor)
    3. Activity after idle (>N seconds quiet, then movement detected)
    """

    def __init__(self, get_cursor_pos=None, find_monitor_index=None):
        """Initialize the event detector.

        Args:
            get_cursor_pos: Callable returning (x, y) cursor position.
                Defaults to capture._get_cursor_pos.
            find_monitor_index: Callable returning monitor index for cursor.
                Defaults to using capture.find_monitor_at_cursor.
        """
        self._get_cursor_pos = get_cursor_pos
        self._find_monitor_index = find_monitor_index

        self._last_title: str = ""
        self._last_cursor: tuple[int, int] = (0, 0)
        self._last_monitor_index: int = 0
        self._last_activity_time: float = time.time()

        self._pending_event: threading.Event = threading.Event()
        self._pending_reason: str = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)

    def start(self):
        """Start the event detection thread."""
        self._thread.start()
        logger.info(
            "EventDetector started (poll=%.1fs, idle=%ds, movement=%dpx)",
            EVENT_POLL_INTERVAL, EVENT_IDLE_THRESHOLD, EVENT_MOVEMENT_THRESHOLD,
        )

    def stop(self):
        """Stop the event detection thread."""
        self._stop.set()

    def has_pending_event(self) -> bool:
        """Check if an event has been triggered since last clear."""
        return self._pending_event.is_set()

    def get_pending_reason(self) -> str:
        """Return the reason for the pending event."""
        return self._pending_reason

    def clear_pending(self):
        """Clear the pending event flag."""
        self._pending_event.clear()
        self._pending_reason = ""

    def _trigger(self, reason: str):
        """Set the event flag with a reason."""
        self._pending_reason = reason
        self._pending_event.set()
        logger.debug("Event triggered: %s", reason)

    def _poll_loop(self):
        """Poll for events at configured interval."""
        while not self._stop.wait(EVENT_POLL_INTERVAL):
            try:
                self._check_window_change()
                self._check_cursor_activity()
            except Exception:
                logger.debug("Event detection poll error", exc_info=True)

    def _check_window_change(self):
        """Detect foreground window title change."""
        title = get_foreground_window_title()
        if title != self._last_title and self._last_title:
            self._trigger(f"window_focus: {title[:60]}")
        self._last_title = title

    def _check_cursor_activity(self):
        """Detect significant cursor movement or idle-then-active."""
        try:
            if self._get_cursor_pos:
                cx, cy = self._get_cursor_pos()
            else:
                from contextpulse_sight.capture import _get_cursor_pos
                cx, cy = _get_cursor_pos()
        except Exception:
            return

        lx, ly = self._last_cursor
        distance = math.sqrt((cx - lx) ** 2 + (cy - ly) ** 2)

        now = time.time()

        if distance > 0:
            # Check idle-then-active
            idle_duration = now - self._last_activity_time
            if idle_duration >= EVENT_IDLE_THRESHOLD:
                self._trigger(f"idle_wake: {idle_duration:.0f}s idle")
            self._last_activity_time = now

        # Check significant movement (monitor boundary cross proxy)
        if distance >= EVENT_MOVEMENT_THRESHOLD:
            # Check if monitor changed
            new_monitor = self._get_monitor_index(cx, cy)
            if new_monitor != self._last_monitor_index:
                self._trigger(f"monitor_cross: m{self._last_monitor_index}→m{new_monitor}")
                self._last_monitor_index = new_monitor

        self._last_cursor = (cx, cy)

    def _get_monitor_index(self, cx: int, cy: int) -> int:
        """Get monitor index for given cursor position."""
        if self._find_monitor_index:
            return self._find_monitor_index(cx, cy)
        try:
            import mss
            with mss.mss() as sct:
                for i, mon in enumerate(sct.monitors[1:]):
                    if (mon["left"] <= cx < mon["left"] + mon["width"]
                            and mon["top"] <= cy < mon["top"] + mon["height"]):
                        return i
            return 0
        except Exception:
            return 0
