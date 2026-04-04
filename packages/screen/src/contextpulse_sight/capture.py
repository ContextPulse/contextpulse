# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Screen capture engine using mss. Handles multi-monitor, cursor tracking, region crop."""

import logging
from io import BytesIO
from pathlib import Path

import mss
from contextpulse_core.platform import get_platform_provider
from PIL import Image

from contextpulse_sight.config import JPEG_QUALITY, MAX_HEIGHT, MAX_WIDTH

logger = logging.getLogger(__name__)


def _get_cursor_pos() -> tuple[int, int]:
    """Get current cursor position (screen coordinates) via platform provider."""
    return get_platform_provider().get_cursor_pos()


def find_monitor_at_cursor(sct: mss.mss) -> tuple[int, dict]:
    """Return (index, monitor_dict) for the monitor containing the cursor.

    Index is 0-based across physical monitors (sct.monitors[1:]).
    Falls back to primary monitor (index 0).
    """
    cx, cy = _get_cursor_pos()
    for i, mon in enumerate(sct.monitors[1:]):
        if (mon["left"] <= cx < mon["left"] + mon["width"]
                and mon["top"] <= cy < mon["top"] + mon["height"]):
            return i, mon
    # monitors[0] is the virtual desktop; monitors[1:] are physical monitors
    if len(sct.monitors) > 1:
        return 0, sct.monitors[1]
    return 0, sct.monitors[0]


def _downscale(img: Image.Image) -> Image.Image:
    """Downscale image to fit within MAX_WIDTH x MAX_HEIGHT, preserving aspect ratio."""
    if img.width <= MAX_WIDTH and img.height <= MAX_HEIGHT:
        return img
    img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
    return img


def mss_to_pil(sct_img) -> Image.Image:
    """Convert mss screenshot to PIL Image (RGB)."""
    return Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb)


def get_monitor_count() -> int:
    """Return the number of physical monitors."""
    with mss.mss() as sct:
        return max(1, len(sct.monitors) - 1)


def capture_active_monitor() -> tuple[int, Image.Image]:
    """Capture the monitor where the cursor currently is, downscaled.

    Returns (monitor_index, image).
    """
    with mss.mss() as sct:
        idx, mon = find_monitor_at_cursor(sct)
        sct_img = sct.grab(mon)
        img = mss_to_pil(sct_img)
    return idx, _downscale(img)


def capture_single_monitor(index: int) -> Image.Image:
    """Capture a specific monitor by index (0-based), downscaled."""
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        if index < 0 or index >= len(physical):
            raise ValueError(
                f"Monitor index {index} out of range (0-{len(physical) - 1})"
            )
        sct_img = sct.grab(physical[index])
        img = mss_to_pil(sct_img)
    return _downscale(img)


def capture_all_monitors() -> list[tuple[int, Image.Image]]:
    """Capture each monitor individually, downscaled.

    Returns list of (monitor_index, image) pairs.
    Handles MemoryError per-monitor so a single large monitor doesn't
    prevent capturing the others.
    """
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        results = []
        for i, mon in enumerate(physical):
            try:
                sct_img = sct.grab(mon)
                img = _downscale(mss_to_pil(sct_img))
                results.append((i, img))
            except MemoryError:
                logger.error(
                    "MemoryError capturing monitor %d (%dx%d) — skipping",
                    i, mon.get("width", 0), mon.get("height", 0),
                )
                # Free whatever partial allocation happened
                import gc
                gc.collect()
    return results


def capture_region(width: int = 800, height: int = 600) -> Image.Image:
    """Capture a region centered on the cursor, downscaled."""
    cx, cy = _get_cursor_pos()
    with mss.mss() as sct:
        desktop = sct.monitors[0]
        left = max(desktop["left"], cx - width // 2)
        top = max(desktop["top"], cy - height // 2)
        right = min(desktop["left"] + desktop["width"], left + width)
        bottom = min(desktop["top"] + desktop["height"], top + height)

        region = {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
        }
        sct_img = sct.grab(region)
        img = mss_to_pil(sct_img)
    return _downscale(img)


def save_image(img: Image.Image, path: Path, fmt: str = "PNG") -> None:
    """Save image to disk. PNG for lossless, JPEG for smaller size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt.upper() == "JPEG":
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(path, format="JPEG", quality=JPEG_QUALITY)
    else:
        img.save(path, format="PNG")
    logger.info("Saved %s (%dx%d) to %s", fmt, img.width, img.height, path)


def capture_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """Convert image to bytes (for MCP server)."""
    buf = BytesIO()
    if fmt.upper() == "JPEG" and img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=JPEG_QUALITY if fmt.upper() == "JPEG" else None)
    return buf.getvalue()
