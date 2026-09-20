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

Which boundaries a pattern gets is decided by ``anchored_pattern()`` below,
and it is not the same for every pattern. A single alphabetic word gets the
LEADING boundary only: a full ``\\b...\\b`` match would satisfy the spec's
word-boundary row and break its own blocklist row in the same table --
"Bank" would stop matching "Online Banking", and "Password" would stop
matching "Passwords". For a privacy control that is a REGRESSION against
today's substring behaviour. Every other pattern (one carrying a space,
hyphen or digit -- "Sign in", "2FA", "Two-Factor") gets a trailing boundary
too, because the leading one alone let "2FA" block any git SHA starting
`2fa`. The full rule and the measurements behind it are in
``anchored_pattern``'s docstring.

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
_TRAILING_ANCHOR = r"(?![A-Za-z0-9])"


def anchored_pattern(pattern: str) -> str:
    """Return the regex source for one blocklist pattern.

    ONE rule, two outcomes, because the two kinds of pattern fail in opposite
    directions:

    * A **single alphabetic word** -- "Bank", "Password", "Authenticator",
      "Bitwarden" -- gets the LEADING anchor only. The trailing one is
      omitted so "Bank" keeps blocking "Banking" and "Password" keeps
      blocking "Passwords". A user who typed the short form is relying on
      that today, and quietly narrowing a privacy control is the wrong
      direction to fail in.

    * **Anything else** -- a pattern carrying a space, a hyphen, a digit or
      any other non-letter ("Sign in", "Log in", "Two-Factor", "2FA",
      "1Password", "Password Manager") -- ALSO gets a trailing boundary.
      These are the patterns whose tail is a short ambiguous token, where
      word-start matching alone was measurably not enough. Against the
      shipped defaults, before this rule:

          build 2fa8c1d - Terminal     BLOCKED by "2FA"
          feat/x @ 2fa3b1 - wezterm    BLOCKED by "2FA"
          Log integration - Grafana    BLOCKED by "Log in"
          Sign indicator spec          BLOCKED by "Sign in"

      ANY short git SHA beginning `2fa` at a word start, in a terminal or
      editor title, silently stopped being captured. That is capture loss,
      not a privacy failure -- it fails closed -- but it is loss the user
      never sees.

    The trailing boundary costs these patterns nothing, because their tail is
    not a stem anyone extends: "Sign in to GitHub", "2FA code",
    "Log in - Bank of America" and "1Password - Login" all still block.

    Known residual, pinned by a test rather than left to be rediscovered:
    `v1.2fa release notes` still blocks, because there `2fa` really is at a
    word start and really is followed by a space. Separating that from
    "2FA code" needs something cleverer than a boundary, and for a privacy
    control this is the right way round to be wrong.
    """
    body = _LEADING_ANCHOR + re.escape(pattern)
    return body if pattern.isalpha() else body + _TRAILING_ANCHOR


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
        re.compile(anchored_pattern(p), re.IGNORECASE)
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
