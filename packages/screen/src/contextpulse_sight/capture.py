# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Screen capture engine with DXcam (fast, Windows) and mss (cross-platform fallback).

Handles multi-monitor, cursor tracking, region crop. DXcam uses the Desktop
Duplication API for 3-5x faster capture on Windows. Falls back to mss when
DXcam is unavailable (Linux, macOS, RDP, import failure).
"""

import logging
import sys
from io import BytesIO
from pathlib import Path

import mss
import numpy as np
from contextpulse_core.platform import get_platform_provider
from PIL import Image

from contextpulse_sight.config import JPEG_QUALITY, MAX_HEIGHT, MAX_WIDTH

logger = logging.getLogger(__name__)

# --- Backend abstraction ---

_backend: str | None = None  # "dxcam" or "mss", set on first use
_dxcam_cameras: dict[int, object] = {}  # output_idx -> dxcam camera instance


def _get_backend() -> str:
    """Detect and cache the best available capture backend.

    Returns "dxcam" on Windows when dxcam is installed, "mss" otherwise.
    """
    global _backend
    if _backend is not None:
        return _backend

    if sys.platform == "win32":
        try:
            import dxcam as _dxcam  # noqa: F401
            _backend = "dxcam"
            logger.info("Using DXcam capture backend (Desktop Duplication API)")
            return _backend
        except (ImportError, OSError):
            pass

    _backend = "mss"
    logger.info("Using mss capture backend (GDI)")
    return _backend


def _get_dxcam_camera(output_idx: int = 0) -> object:
    """Get or create a DXcam camera for the given monitor output index."""
    if output_idx not in _dxcam_cameras:
        import dxcam
        camera = dxcam.create(output_idx=output_idx, output_color="BGR")
        _dxcam_cameras[output_idx] = camera
    return _dxcam_cameras[output_idx]


def _dxcam_to_pil(frame: np.ndarray) -> Image.Image:
    """Convert a DXcam BGR/BGRA numpy array to a PIL RGB Image."""
    if frame.ndim == 3 and frame.shape[2] == 4:
        # BGRA -> RGB
        return Image.fromarray(frame[:, :, 2::-1])
    # BGR -> RGB
    return Image.fromarray(frame[:, :, ::-1])


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
    Uses DXcam on Windows for speed, falls back to mss.
    """
    # Always use mss to find which monitor the cursor is on
    with mss.mss() as sct:
        idx, mon = find_monitor_at_cursor(sct)

    if _get_backend() == "dxcam":
        try:
            camera = _get_dxcam_camera(idx)
            frame = camera.grab()
            if frame is not None:
                return idx, _downscale(_dxcam_to_pil(frame))
            logger.debug("DXcam returned None for monitor %d, falling back to mss", idx)
        except Exception as exc:
            logger.warning("DXcam capture failed for monitor %d: %s — falling back to mss", idx, exc)

    # Fallback to mss
    with mss.mss() as sct:
        _, mon = find_monitor_at_cursor(sct)
        sct_img = sct.grab(mon)
        img = mss_to_pil(sct_img)
    return idx, _downscale(img)


def capture_single_monitor(index: int) -> Image.Image:
    """Capture a specific monitor by index (0-based), downscaled."""
    # Validate index via mss (it knows how many monitors exist)
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        if index < 0 or index >= len(physical):
            raise ValueError(
                f"Monitor index {index} out of range (0-{len(physical) - 1})"
            )

    if _get_backend() == "dxcam":
        try:
            camera = _get_dxcam_camera(index)
            frame = camera.grab()
            if frame is not None:
                return _downscale(_dxcam_to_pil(frame))
            logger.debug("DXcam returned None for monitor %d, falling back to mss", index)
        except Exception as exc:
            logger.warning("DXcam capture failed for monitor %d: %s — falling back to mss", index, exc)

    # Fallback to mss
    with mss.mss() as sct:
        sct_img = sct.grab(sct.monitors[1:][index])
        img = mss_to_pil(sct_img)
    return _downscale(img)


def capture_all_monitors() -> list[tuple[int, Image.Image]]:
    """Capture each monitor individually, downscaled.

    Returns list of (monitor_index, image) pairs.
    Uses DXcam per-monitor when available, falls back to mss per-monitor.
    Handles errors per-monitor so a single failure doesn't prevent capturing others.
    """
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        monitor_count = len(physical)

    use_dxcam = _get_backend() == "dxcam"
    results = []

    for i in range(monitor_count):
        try:
            if use_dxcam:
                try:
                    camera = _get_dxcam_camera(i)
                    frame = camera.grab()
                    if frame is not None:
                        results.append((i, _downscale(_dxcam_to_pil(frame))))
                        continue
                    logger.debug("DXcam returned None for monitor %d, trying mss", i)
                except Exception as exc:
                    logger.warning("DXcam failed for monitor %d: %s — trying mss", i, exc)

            # mss fallback for this monitor
            with mss.mss() as sct:
                mon = sct.monitors[1:][i]
                sct_img = sct.grab(mon)
                results.append((i, _downscale(mss_to_pil(sct_img))))
        except MemoryError:
            logger.error("MemoryError capturing monitor %d — skipping", i)
            import gc
            gc.collect()
        except Exception as exc:
            logger.error("Failed to capture monitor %d: %s — skipping", i, exc)

    return results


def get_active_window_rect() -> tuple[int, int, int, int] | None:
    """Get the visible bounds of the foreground window in physical pixels.

    Returns (left, top, width, height) or None if no foreground window.
    Uses DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) on Windows
    for accurate bounds without invisible resize borders.
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi

        hwnd = user32.GetForegroundWindow()
        if not hwnd or not user32.IsWindow(hwnd):
            return None

        # Use DWM for visible bounds (no invisible borders)
        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hr != 0:
            # DWM failed — fall back to GetWindowRect
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            rect.left, rect.top, rect.right, rect.bottom = r.left, r.top, r.right, r.bottom

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None

        return (rect.left, rect.top, w, h)
    except Exception as exc:
        logger.debug("get_active_window_rect failed: %s", exc)
        return None


_REGION_PADDING = 50  # pixels around the active window


def capture_region(width: int = 0, height: int = 0) -> Image.Image:
    """Capture a focused region of the screen, downscaled.

    If width and height are 0 (default), auto-sizes to the active window
    bounds plus padding. Falls back to 800x600 cursor-centered if no
    active window is detected.

    Args:
        width: Explicit region width (0 = auto-detect from active window).
        height: Explicit region height (0 = auto-detect from active window).
    """
    # Auto-detect from active window if no explicit size given
    if width == 0 and height == 0:
        win_rect = get_active_window_rect()
        if win_rect is not None:
            wl, wt, ww, wh = win_rect
            # Add padding around the window
            pad = _REGION_PADDING
            with mss.mss() as sct:
                desktop = sct.monitors[0]
                left = max(desktop["left"], wl - pad)
                top = max(desktop["top"], wt - pad)
                right = min(desktop["left"] + desktop["width"], wl + ww + pad)
                bottom = min(desktop["top"] + desktop["height"], wt + wh + pad)
                region = {
                    "left": left, "top": top,
                    "width": right - left, "height": bottom - top,
                }
                sct_img = sct.grab(region)
                img = mss_to_pil(sct_img)
            return _downscale(img)
        # No active window — fall back to default
        width, height = 800, 600

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
