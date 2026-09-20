# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Privacy controls: window title blocklist and session lock detection.

The blocklist is read LIVE from ``contextpulse_core.config`` on every call,
not bound at import. Before this, ``contextpulse_sight.config`` built the list
from ``CONTEXTPULSE_BLOCKLIST`` alone, so the 14 default patterns the Settings
dialog showed -- and anything a user typed into it -- reached neither the
daemon nor the MCP server: both read an empty list for the life of the
process (cp-settings-dialog-disconnected-from-daemon). Reading through
``config.get`` costs one ``stat()`` per call (the parsed JSON layer is cached
on mtime), which is what makes a per-capture and per-search-result read
affordable, and it is the only mechanism that works across the two processes
that need it -- an in-process callback from the Settings dialog cannot reach
the separate MCP server process.

Matching anchors each pattern to a WORD START, not to a bare substring. Two of
the defaults are short enough to be dangerous as substrings: "Sign in" is
inside "Design in Figma" and "Log in" is inside "Blog index". That never bit
while the live list was empty; it would have started the moment the defaults
became real, silently suppressing ordinary windows.

Only the LEADING boundary is enforced (``(?<![A-Za-z0-9])pattern``),
deliberately -- the trailing one is not. A full ``\\b...\\b`` match would satisfy the spec's
word-boundary row and break its own blocklist row in the same table: "Bank"
would stop matching "Online Banking", and "Password" would stop matching
"Passwords". For a privacy control that is a REGRESSION against today's
substring behaviour -- a user who typed "Bank" would silently lose the
blocking they already had. Anchoring the start kills every mid-word false
match (the entire documented hazard) while keeping every suffix match.

The compiled patterns are cached per distinct list so the regex work happens
once per list, not once per call.
"""

import logging
import re

from contextpulse_core.config import _DEFAULTS
from contextpulse_core.config import get as cfg_get
from contextpulse_core.platform import get_platform_provider

logger = logging.getLogger("contextpulse.sight.privacy")


# -- Window title blocklist ------------------------------------------------

# (patterns tuple, compiled tuple) for the most recent list seen. Rebound as a
# whole tuple, never mutated, so a reader on another daemon thread can only
# ever observe a consistent pair.
_COMPILED: tuple[tuple[str, ...], tuple[re.Pattern, ...]] | None = None

# Word-START anchor. Deliberately NOT `(?<!\w)`: `\w` includes `_`, so that
# spelling silently dropped the suffix matches the module docstring above
# promises to keep -- "Bank" stopped blocking `my_bank_statement.pdf` and
# "1Password" stopped blocking `screenshot_1password_login.png`, both of
# which the old substring behaviour blocked. Underscore-joined filenames are
# exactly the case a user typing a blocklist pattern is protecting. Only
# letters and digits count as "inside a word" here.
_LEADING_ANCHOR = r"(?<![A-Za-z0-9])"


def _blocklist_patterns() -> tuple[str, ...]:
    """The blocklist as configured right now (defaults + config.json + env)."""
    patterns = cfg_get("blocklist_patterns", _DEFAULTS["blocklist_patterns"])
    if not isinstance(patterns, (list, tuple)):  # a hand-edited config.json can be anything
        logger.warning("blocklist_patterns is %r, not a list -- treating as empty", type(patterns).__name__)
        return ()
    return tuple(str(p) for p in patterns if str(p).strip())


def _compiled_for(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    """Compile (and cache) word-start-anchored regexes for one blocklist."""
    global _COMPILED
    cached = _COMPILED
    if cached is not None and cached[0] == patterns:
        return cached[1]
    compiled = tuple(
        re.compile(_LEADING_ANCHOR + re.escape(p), re.IGNORECASE)
        for p in patterns
    )
    _COMPILED = (patterns, compiled)
    return compiled


def get_foreground_window_title() -> str:
    """Get the title of the currently active window."""
    return get_platform_provider().get_foreground_window_title()


def get_foreground_process_name() -> str:
    """Get the executable name of the foreground window's process."""
    return get_platform_provider().get_foreground_process_name()


def is_blocked() -> bool:
    """Return True if the foreground window matches any blocklist pattern."""
    return is_title_blocked(get_foreground_window_title())


def is_title_blocked(title: str) -> bool:
    """Return True if a given window title matches any blocklist pattern.

    Used by the capture loop before every capture and by the MCP tools to
    filter stored history before returning results.
    """
    if not title:
        return False
    patterns = _blocklist_patterns()
    if not patterns:
        return False
    return any(rx.search(title) for rx in _compiled_for(patterns))


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
