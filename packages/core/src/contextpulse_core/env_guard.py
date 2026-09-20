# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""A snapshot of the REAL process environment, taken before any .env merge.

`contextpulse_core.config` runs `load_dotenv(override=True)` in its module
body, so by the time anything reads `os.environ` a `.env` file sitting in the
working directory has already won over the actual environment. That is fine
for ordinary settings and wrong for a security switch: a `.env` in a checkout
is a persisted setting, and the MCP off switch was made env-only precisely so
it could not become one.

This module must be imported BEFORE `contextpulse_core.config`. It records
whether it managed that, so a caller can tell a trustworthy snapshot from a
polluted one rather than assuming.
"""

import os
import sys

# True when this module was imported before config's load_dotenv() ran.
SNAPSHOT_IS_PRE_DOTENV: bool = "contextpulse_core.config" not in sys.modules

# A copy, not a reference -- os.environ is mutated by load_dotenv(override=True).
PROCESS_ENV: dict[str, str] = dict(os.environ)


def process_env(key: str, default: str = "") -> str:
    """Read a variable as the process was actually launched with it.

    Returns `default` when the snapshot cannot be trusted, so a caller that
    cares about provenance gets "unknown", never a confident wrong answer.
    """
    if not SNAPSHOT_IS_PRE_DOTENV:
        return default
    return PROCESS_ENV.get(key, default)
