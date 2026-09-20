# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Touch-specific configuration — reads from ContextPulse shared config."""

from contextpulse_core.config import APPDATA_DIR, load_config

# Touch data directory
TOUCH_DATA_DIR = APPDATA_DIR / "touch"

# Not user-facing: no config.json key, no Settings control, no env var. It
# stays a module constant deliberately -- see the spec's section 4, which
# scopes this action to the tunables the dialog claims to set.
CORRECTION_CONFIDENCE_THRESHOLD = 0.7  # min confidence to write correction


def get_touch_config() -> dict:
    """Load touch-specific settings from shared ContextPulse config.

    Four renames out of the merged config and nothing else. The four
    BURST_TIMEOUT/CORRECTION_WINDOW/MIN_BURST_CHARS/MOUSE_DEBOUNCE constants
    and their CONTEXTPULSE_TOUCH_* env reads are gone: the keys now live in
    `contextpulse_core.config._DEFAULTS` and their env vars in `_ENV_MAP`, so
    both layers are applied before this function sees the dict.

    The old shape could not work as written. `cfg.get(key, <env fallback>)`
    only reaches its fallback when the key is ABSENT, and once the keys are
    declared in _DEFAULTS `load_config()` always supplies them -- so every
    CONTEXTPULSE_TOUCH_* variable would have become silently unreadable had
    the fallbacks been left in place. Deleting them is what keeps those four
    variables working.

    The function itself is kept because tests patch it by name.
    """
    cfg = load_config()
    return {
        "burst_timeout": cfg["touch_burst_timeout"],
        "correction_window": cfg["touch_correction_window"],
        "min_burst_chars": cfg["touch_min_burst_chars"],
        "mouse_debounce": cfg["touch_mouse_debounce"],
    }
