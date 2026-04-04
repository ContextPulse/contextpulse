# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Programmatic icon generation for system tray."""

import json
from pathlib import Path

from PIL import Image, ImageDraw

_BRAND_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "brand"
_COLORS = {}
if (_BRAND_DIR / "colors.json").exists():
    _COLORS = json.loads((_BRAND_DIR / "colors.json").read_text(encoding="utf-8"))

# Resolve brand colors with fallbacks
_DARK_SURFACE = _COLORS.get("dark", {}).get("surface", "#161B22")
_ACCENT = _COLORS.get("dark", {}).get("accent", "#00E676")


def create_icon(color: str | None = None, size: int = 64) -> Image.Image:
    """Create a simple camera/eye icon for the system tray.

    Args:
        color: Fill color. None = brand accent (green). Pass warning color for paused.
        size: Icon dimensions (square).
    """
    if color is None:
        color = _ACCENT
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark background circle
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=_DARK_SURFACE,
        outline=color,
        width=max(2, size // 16),
    )

    # Inner "lens" circle
    center = size // 2
    radius = size // 5
    draw.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        fill=color,
    )

    return img
