# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Privacy controls: window title blocklist and session lock detection."""

import logging

from contextpulse_core.platform import get_platform_provider

from contextpulse_sight.config import BLOCKLIST_PATTERNS

logger = logging.getLogger("contextpulse.sight.privacy")


# -- Window title blocklist ------------------------------------------------

def get_foreground_window_title() -> str:
    """Get the title of the currently active window."""
    return get_platform_provider().get_foreground_window_title()


def get_foreground_process_name() -> str:
    """Get the executable name of the foreground window's process."""
    return get_platform_provider().get_foreground_process_name()


def is_blocked() -> bool:
    """Return True if the foreground window matches any blocklist pattern."""
    if not BLOCKLIST_PATTERNS:
        return False
    title = get_foreground_window_title().lower()
    return any(p.lower() in title for p in BLOCKLIST_PATTERNS)


def is_title_blocked(title: str) -> bool:
    """Return True if a given window title matches any blocklist pattern.

    Used by MCP tools to filter stored history before returning results.
    """
    if not BLOCKLIST_PATTERNS or not title:
        return False
    title_lower = title.lower()
    return any(p.lower() in title_lower for p in BLOCKLIST_PATTERNS)


# -- Session lock/unlock detection ----------------------------------------

def SessionMonitor(on_lock: callable, on_unlock: callable):
    """Create a platform-appropriate session lock/unlock monitor.

    Returns an object with a start() method that begins monitoring
    in a daemon thread. This is a factory function that delegates
    to the platform provider.
    """
    return get_platform_provider().create_session_monitor(
        on_lock=on_lock, on_unlock=on_unlock
    )
