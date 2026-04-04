# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Factory function that returns the right PlatformProvider for the current OS."""

import sys

from contextpulse_core.platform.base import PlatformProvider

# Module-level singleton — initialized on first call
_instance: PlatformProvider | None = None


def get_platform_provider() -> PlatformProvider:
    """Return the PlatformProvider for the current operating system.

    The provider is created once and cached as a module-level singleton.
    """
    global _instance
    if _instance is not None:
        return _instance

    if sys.platform == "win32":
        from contextpulse_core.platform.windows import WindowsPlatformProvider
        _instance = WindowsPlatformProvider()
    elif sys.platform == "darwin":
        from contextpulse_core.platform.macos import MacPlatformProvider
        _instance = MacPlatformProvider()
    elif sys.platform.startswith("linux"):
        from contextpulse_core.platform.linux import LinuxPlatformProvider
        _instance = LinuxPlatformProvider()
    else:
        raise RuntimeError(
            f"Unsupported platform: {sys.platform}. "
            "ContextPulse supports win32, darwin, and linux."
        )
    return _instance


def reset_platform_provider() -> None:
    """Reset the singleton (for testing only)."""
    global _instance
    _instance = None
