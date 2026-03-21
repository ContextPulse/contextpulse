"""MCP stdio server exposing screenshot tools to Claude Code.

Tools:
  get_screenshot       — capture current screen (active, all, specific monitor, or region)
  get_recent           — last N frames from rolling buffer
  get_screen_text      — OCR the current screen (full-res, on-demand)
  get_buffer_status    — check daemon/buffer health
  get_activity_summary — app usage distribution over last N hours
  search_history       — full-text search across window titles and OCR text
  get_context_at       — retrieve frame + metadata from N minutes ago
"""

import logging
import time
from datetime import datetime
from pathlib import Path

import mss as mss_lib
from PIL import Image

from mcp.server.fastmcp import FastMCP, Image as MCPImage

from contextpulse_sight import capture
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer, parse_frame_path
from contextpulse_sight.classifier import classify_and_extract
from contextpulse_sight.config import FILE_LATEST, OUTPUT_DIR
from contextpulse_sight.privacy import is_blocked, is_title_blocked

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.sight.mcp")

mcp_app = FastMCP("ContextPulse Sight")

# Shared instances (reads data written by the daemon)
_buffer = RollingBuffer()
_activity_db = ActivityDB()


@mcp_app.tool()
def get_screenshot(mode: str = "active", monitor_index: int | None = None) -> list | MCPImage:
    """Capture the current screen and return as an image.

    Args:
        mode: "active" (monitor with cursor), "all" (all monitors as separate images),
              "monitor" (specific monitor by index), or "region" (800x600 around cursor).
        monitor_index: Which monitor to capture (0-based). Only used with mode="monitor".

    Returns the screenshot as an inline image that Claude can see directly.
    When mode="all", returns a list of images (one per monitor).
    """
    if is_blocked():
        return "Capture blocked: active window matches privacy blocklist."

    if mode == "all":
        monitors = capture.capture_all_monitors()
        results = []
        for idx, img in monitors:
            capture.save_image(img, OUTPUT_DIR / f"screen_monitor_{idx}.png")
            logger.info("get_screenshot(all): monitor %d %dx%d", idx, img.width, img.height)
            results.append(
                f"--- Monitor {idx} ---"
            )
            results.append(
                MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg")
            )
        return results

    if mode == "monitor":
        idx = monitor_index if monitor_index is not None else 0
        img = capture.capture_single_monitor(idx)
        logger.info("get_screenshot(monitor=%d): %dx%d", idx, img.width, img.height)
        return MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg")

    if mode == "region":
        img = capture.capture_region()
        logger.info("get_screenshot(region): %dx%d", img.width, img.height)
        return MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg")

    # Default: active monitor
    idx, img = capture.capture_active_monitor()
    capture.save_image(img, FILE_LATEST)
    logger.info("get_screenshot(active): monitor %d %dx%d", idx, img.width, img.height)
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
        parsed = parse_frame_path(frame_path)
        monitor_label = f"m{parsed[1]}" if parsed else "m?"
        img = Image.open(frame_path)
        results.append(f"[{monitor_label}]")
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
    if is_blocked():
        return "Capture blocked: active window matches privacy blocklist."

    with mss_lib.mss() as sct:
        _, mon = capture.find_monitor_at_cursor(sct)
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
        return "Buffer is empty. Is the ContextPulse Sight daemon running?"

    oldest_parsed = parse_frame_path(frames[0])
    newest_parsed = parse_frame_path(frames[-1])
    if not oldest_parsed or not newest_parsed:
        return f"Buffer: {count} frames (could not parse timestamps)"

    oldest_ts = oldest_parsed[0] / 1000.0
    newest_ts = newest_parsed[0] / 1000.0
    age = time.time() - newest_ts

    # Count unique monitors in buffer
    monitor_indices = set()
    for f in frames:
        parsed = parse_frame_path(f)
        if parsed:
            monitor_indices.add(parsed[1])

    return (
        f"Buffer: {count} frames across {len(monitor_indices)} monitor(s)\n"
        f"Monitors: {sorted(monitor_indices)}\n"
        f"Span: {newest_ts - oldest_ts:.0f}s of history\n"
        f"Latest frame: {age:.0f}s ago\n"
        f"Directory: {OUTPUT_DIR / 'buffer'}"
    )


@mcp_app.tool()
def get_activity_summary(hours: float = 8.0) -> str:
    """Summarize which apps and websites were used over the last N hours.

    Returns app usage time distribution, most visited window titles,
    and total capture count. Useful for understanding daily patterns
    and recommending automations.

    Args:
        hours: How many hours back to look (default 8).
    """
    summary = _activity_db.get_summary(hours)
    if not summary["total_captures"]:
        return f"No activity recorded in the last {hours:.0f} hours."

    lines = [f"=== Activity Summary (last {hours:.0f}h) ===\n"]
    lines.append(f"Total captures: {summary['total_captures']}")

    start, end = summary["time_range"]
    lines.append(
        f"Time range: {datetime.fromtimestamp(start).strftime('%H:%M')} - "
        f"{datetime.fromtimestamp(end).strftime('%H:%M')}\n"
    )

    if summary["apps"]:
        lines.append("Apps (by frequency):")
        for app, count in list(summary["apps"].items())[:15]:
            pct = count / summary["total_captures"] * 100
            lines.append(f"  {app}: {count} captures ({pct:.0f}%)")

    if summary["titles"]:
        lines.append("\nRecent window titles:")
        for title in summary["titles"][:10]:
            if is_title_blocked(title):
                lines.append(f"  - [BLOCKED — matches privacy blocklist]")
            else:
                lines.append(f"  - {title[:80]}")

    return "\n".join(lines)


@mcp_app.tool()
def search_history(query: str, minutes_ago: int = 60) -> str:
    """Search screen history via OCR text and window titles.

    Uses full-text search to find when specific content was on screen.
    Useful for finding errors, specific pages, or conversations.

    Args:
        query: Search terms (searches window titles, app names, and OCR text).
        minutes_ago: How far back to search (default 60 minutes).
    """
    results = _activity_db.search(query, minutes_ago)
    if not results:
        return f"No results for '{query}' in the last {minutes_ago} minutes."

    # Filter out results from blocked windows
    filtered = [r for r in results if not is_title_blocked(r.get("window_title", ""))]
    skipped = len(results) - len(filtered)

    lines = [f"=== Search: '{query}' ({len(filtered)} results) ===\n"]
    if skipped:
        lines.append(f"({skipped} result(s) hidden — matched privacy blocklist)\n")
    for r in filtered:
        ts_str = datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        lines.append(f"[{ts_str}] {r['app_name']} — {r['window_title'][:80]}")
        if r.get("ocr_text"):
            snippet = r["ocr_text"][:200].replace("\n", " ")
            lines.append(f"  OCR: {snippet}...")
        lines.append(f"  Monitor: {r['monitor_index']}, Frame: {r.get('frame_path', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


@mcp_app.tool()
def get_context_at(minutes_ago: float = 5.0) -> list:
    """Get the frame + window title + OCR text from approximately N minutes ago.

    Useful when Claude needs to reference what was on screen earlier,
    like an error message that scrolled away.

    Args:
        minutes_ago: How many minutes ago to look (default 5).

    Returns the frame as an image with metadata text.
    """
    record = _activity_db.get_context_at(minutes_ago)
    if not record:
        return f"No frame found from approximately {minutes_ago:.0f} minutes ago."

    if is_title_blocked(record.get("window_title", "")):
        return f"Frame from {minutes_ago:.0f} minutes ago is blocked by privacy blocklist."

    ts_str = datetime.fromtimestamp(record["timestamp"]).strftime("%H:%M:%S")

    meta = (
        f"[Context at {ts_str}]\n"
        f"App: {record['app_name']}\n"
        f"Window: {record['window_title']}\n"
        f"Monitor: {record['monitor_index']}"
    )

    results = [meta]

    # Try to include the frame image
    frame_path = record.get("frame_path")
    if frame_path:
        fp = Path(frame_path)
        if fp.exists():
            img = Image.open(fp)
            results.append(
                MCPImage(data=capture.capture_to_bytes(img, "JPEG"), format="jpeg")
            )

    if record.get("ocr_text"):
        results.append(f"\nOCR Text:\n{record['ocr_text'][:500]}")

    return results


def main():
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
