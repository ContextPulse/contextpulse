# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Filesystem paths for the Sight package.

This module used to be the second of two config systems. It declared 22
constants, read them from ``CONTEXTPULSE_*`` environment variables at import,
and never looked at ``config.json`` -- while ``contextpulse_core.config``
declared most of the same keys, was what the Settings dialog wrote, and was
read by almost nothing in the capture path. Every tunable the dialog offered
was therefore a control over a value the daemon did not use
(cp-settings-dialog-disconnected-from-daemon).

``contextpulse_core.config`` is now the one declaration site. What is left
here is paths, and nothing else:

* ``OUTPUT_DIR`` and ``ACTIVITY_DB_PATH`` are re-exported from core so the two
  packages cannot resolve them differently. They are startup-bound and
  env-only there by design -- a path is chosen once, when the process opens
  its files.
* the four derived paths below hang off ``OUTPUT_DIR``.

There are deliberately NO wrapper constants for tunables. A module-level
``JPEG_QUALITY = cfg_get("jpeg_quality")`` would look like a fix and be the
original defect: a value frozen at import, unable to change while the daemon
runs. Readers call ``contextpulse_core.config.get`` at the point of use.
"""

from contextpulse_core.config import ACTIVITY_DB_PATH, OUTPUT_DIR

__all__ = [
    "ACTIVITY_DB_PATH",
    "BUFFER_DIR",
    "FILE_ALL",
    "FILE_LATEST",
    "FILE_REGION",
    "OUTPUT_DIR",
]

# Rolling buffer directory
BUFFER_DIR = OUTPUT_DIR / "buffer"

# File paths (stable, overwritten each capture)
FILE_LATEST = OUTPUT_DIR / "screen_latest.jpg"
FILE_ALL = OUTPUT_DIR / "screen_all.png"
FILE_REGION = OUTPUT_DIR / "screen_region.png"
