# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse Touch — keyboard and mouse input capture for AI agents.

Captures typing bursts (privacy-safe), mouse events, and detects
corrections to Voice dictation output for self-improving transcription.
"""

__version__ = "0.1.0"

__all__ = [
    "burst_tracker",
    "config",
    "correction_detector",
    "listeners",
    "mcp_server",
    "touch_module",
]
