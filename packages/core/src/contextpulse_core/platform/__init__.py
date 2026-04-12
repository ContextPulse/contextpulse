# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Platform abstraction layer for cross-platform support.

Provides a unified API for OS-specific operations (clipboard, window info,
cursor position, session lock detection, single-instance guards, caret position).

Usage:
    from contextpulse_core.platform import get_platform_provider
    platform = get_platform_provider()
    title = platform.get_foreground_window_title()
"""

from contextpulse_core.platform.base import PlatformProvider
from contextpulse_core.platform.factory import get_platform_provider

__all__ = ["PlatformProvider", "get_platform_provider"]
