"""MCP stdio server exposing screenshot tools to Claude Code.

Tools:
  get_screenshot  — capture current screen (active monitor, all, or region)
  get_recent      — last N frames from rolling buffer
  get_screen_text — OCR the current screen (full-res, on-demand)
  get_buffer_status — check daemon/buffer health
"""

import logging
import time

import mss as mss_lib
from PIL import Image

from mcp.server.fastmcp import FastMCP, Image as MCPImage

from contextpulse_screen import capture
from contextpulse_screen.buffer import RollingBuffer
from contextpulse_screen.classifier import classify_and_extract
from contextpulse_screen.config import FILE_LATEST, OUTPUT_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.screen.mcp")

mcp_app = FastMCP("ContextPulse Screen")

# Shared buffer instance (reads frames written by the daemon)
_buffer = RollingBuffer()


@mcp_app.tool()
def get_screenshot(mode: str = "active") -> MCPImage:
    """Capture the current screen and return as an image.

    Args:
        mode: "active" (monitor with cursor), "all" (all monitors stitched),
              or "region" (800x600 around cursor).

    Returns the screenshot as an inline image that Claude can see directly.
    """
    if mode == "all":
        img = capture.capture_all_monitors()
    elif mode == "region":
        img = capture.capture_region()
    else:
        img = capture.capture_active_monitor()

    capture.save_image(img, FILE_LATEST)

    logger.info("get_screenshot(%s): %dx%d", mode, img.width, img.height)
    return MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg")


@mcp_app.tool()
def get_recent(count: int = 3, seconds: int = 60) -> list:
    """Get recent screenshots from the rolling buffer.

    The daemon auto-captures every few seconds. This returns the most recent
    frames from the buffer (up to `count` frames from the last `seconds` seconds).

    Args:
        count: Max number of frames to return (default 3).
        seconds: Look back this many seconds (default 60).

    Returns images inline so Claude can see them directly.
    """
    frames = _buffer.get_recent(seconds)
    if not frames:
        return []

    frames = frames[-count:]

    results = []
    for frame_path in frames:
        img = Image.open(frame_path)
        results.append(MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg"))

    return results


@mcp_app.tool()
def get_screen_text() -> str:
    """OCR the current screen at full resolution and return extracted text.

    This captures at native resolution (e.g. 3840x2160) for OCR quality,
    then runs OCR. Much cheaper in tokens than sending an image (~200-700
    tokens for text vs ~1,229 for an image).

    Use this when you think the screen contains mostly text (code, terminal,
    docs, chat). Use get_screenshot() for visual content (diagrams, UIs, etc).
    """
    with mss_lib.mss() as sct:
        mon = capture.find_monitor_at_cursor(sct)
        sct_img = sct.grab(mon)
        img = capture.mss_to_pil(sct_img)

    logger.info("get_screen_text: captured %dx%d for OCR", img.width, img.height)

    result = classify_and_extract(img)

    if result["type"] == "text" and result["text"]:
        return (
            f"[OCR: {result['lines']} lines, {result['chars']} chars, "
            f"confidence={result['confidence']:.2f}, time={result['ocr_time']:.1f}s]\n\n"
            f"{result['text']}"
        )
    else:
        return (
            f"Screen appears to be mostly visual (OCR found only {result['chars']} chars "
            f"with {result['confidence']:.2f} confidence). "
            f"Use get_screenshot() instead to see the screen as an image."
        )


@mcp_app.tool()
def get_buffer_status() -> str:
    """Check the status of the rolling screenshot buffer.

    Returns frame count, age range, and buffer directory info.
    Useful for verifying the daemon is running and capturing.
    """
    count = _buffer.frame_count()
    frames = _buffer.list_frames()

    if not frames:
        return "Buffer is empty. Is the ContextPulse Screen daemon running?"

    oldest_ts = int(frames[0].stem) / 1000.0
    newest_ts = int(frames[-1].stem) / 1000.0
    age = time.time() - newest_ts

    return (
        f"Buffer: {count} frames\n"
        f"Span: {newest_ts - oldest_ts:.0f}s of history\n"
        f"Latest frame: {age:.0f}s ago\n"
        f"Directory: {OUTPUT_DIR / 'buffer'}"
    )


def main():
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
