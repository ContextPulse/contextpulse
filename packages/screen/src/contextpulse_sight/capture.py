"""Screen capture engine using mss. Handles multi-monitor, cursor tracking, region crop."""

import ctypes
import ctypes.wintypes
import logging
from io import BytesIO
from pathlib import Path

import mss
from PIL import Image

from contextpulse_sight.config import JPEG_QUALITY, MAX_HEIGHT, MAX_WIDTH

logger = logging.getLogger(__name__)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_cursor_pos() -> tuple[int, int]:
    """Get current cursor position (screen coordinates)."""
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def find_monitor_at_cursor(sct: mss.mss) -> dict:
    """Return the mss monitor dict containing the cursor. Falls back to primary."""
    cx, cy = _get_cursor_pos()
    for mon in sct.monitors[1:]:
        if (mon["left"] <= cx < mon["left"] + mon["width"]
                and mon["top"] <= cy < mon["top"] + mon["height"]):
            return mon
    # monitors[0] is the virtual desktop; monitors[1:] are physical monitors
    if len(sct.monitors) > 1:
        return sct.monitors[1]
    return sct.monitors[0]


def _downscale(img: Image.Image) -> Image.Image:
    """Downscale image to fit within MAX_WIDTH x MAX_HEIGHT, preserving aspect ratio."""
    if img.width <= MAX_WIDTH and img.height <= MAX_HEIGHT:
        return img
    img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
    return img


def mss_to_pil(sct_img) -> Image.Image:
    """Convert mss screenshot to PIL Image (RGB)."""
    return Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb)


def capture_active_monitor() -> Image.Image:
    """Capture the monitor where the cursor currently is, downscaled."""
    with mss.mss() as sct:
        mon = find_monitor_at_cursor(sct)
        sct_img = sct.grab(mon)
        img = mss_to_pil(sct_img)
    return _downscale(img)


def capture_all_monitors() -> Image.Image:
    """Capture all monitors stitched together. Scales so each monitor gets ~MAX_WIDTH pixels."""
    with mss.mss() as sct:
        num_monitors = max(1, len(sct.monitors) - 1)  # monitors[0] is virtual desktop
        sct_img = sct.grab(sct.monitors[0])
        img = mss_to_pil(sct_img)
    # Give each monitor its own MAX_WIDTH allocation so text stays readable
    target_width = MAX_WIDTH * num_monitors
    target_height = MAX_HEIGHT * num_monitors
    if img.width > target_width or img.height > target_height:
        img.thumbnail((target_width, target_height), Image.LANCZOS)
    return img


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
