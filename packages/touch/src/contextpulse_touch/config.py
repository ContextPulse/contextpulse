# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Touch-specific configuration — reads from ContextPulse shared config."""

import os

from contextpulse_core.config import APPDATA_DIR, load_config

# Touch data directory
TOUCH_DATA_DIR = APPDATA_DIR / "touch"

# Default settings
BURST_TIMEOUT = 1.5          # seconds of silence to end a typing burst
CORRECTION_WINDOW = 15.0     # seconds after paste to watch for edits
MIN_BURST_CHARS = 3          # minimum chars for a burst event
MOUSE_DEBOUNCE = 0.1         # seconds between mouse events
CORRECTION_CONFIDENCE_THRESHOLD = 0.7  # min confidence to write correction


def get_touch_config() -> dict:
    """Load touch-specific settings from shared ContextPulse config."""
    cfg = load_config()
    return {
        "burst_timeout": cfg.get(
            "touch_burst_timeout",
            float(os.environ.get("CONTEXTPULSE_TOUCH_BURST_TIMEOUT", str(BURST_TIMEOUT))),
        ),
        "correction_window": cfg.get(
            "touch_correction_window",
            float(os.environ.get("CONTEXTPULSE_TOUCH_CORRECTION_WINDOW", str(CORRECTION_WINDOW))),
        ),
        "min_burst_chars": cfg.get(
            "touch_min_burst_chars",
            int(os.environ.get("CONTEXTPULSE_TOUCH_MIN_BURST_CHARS", str(MIN_BURST_CHARS))),
        ),
        "mouse_debounce": cfg.get(
            "touch_mouse_debounce",
            float(os.environ.get("CONTEXTPULSE_TOUCH_MOUSE_DEBOUNCE", str(MOUSE_DEBOUNCE))),
        ),
    }
