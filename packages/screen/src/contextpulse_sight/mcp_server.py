"""MCP stdio server exposing screenshot tools to Claude Code.

Tools:
  get_screenshot       — capture current screen (active, all, specific monitor, or region)
  get_recent           — last N frames from rolling buffer
  get_screen_text      — OCR the current screen (full-res, on-demand)
  get_buffer_status    — check daemon/buffer health + token cost estimates
  get_activity_summary — app usage distribution over last N hours
  search_history       — full-text search across window titles and OCR text
  get_context_at       — retrieve frame + metadata from N minutes ago
  get_agent_stats      — MCP client usage statistics
"""

import functools
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import mss as mss_lib
from PIL import Image

from mcp.server.fastmcp import FastMCP, Image as MCPImage

from contextpulse_sight import capture
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import (
    RollingBuffer,
    estimate_image_tokens,
    estimate_text_tokens,
    parse_frame_path,
)
from contextpulse_sight.classifier import classify_and_extract
from contextpulse_sight.config import FILE_LATEST, OUTPUT_DIR
from contextpulse_sight.privacy import is_blocked, is_title_blocked

from contextpulse_core.license import get_license_tier
from contextpulse_core.spine import EventBus

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
_event_bus: EventBus | None = None


def _get_event_bus() -> EventBus:
    """Lazy-init EventBus (reads the same activity.db as the daemon)."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(_activity_db.db_path)
    return _event_bus


def _track_call(func):
    """Decorator to log MCP tool calls for agent awareness tracking."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _activity_db.record_mcp_call(tool_name=func.__name__)
        return func(*args, **kwargs)
    return wrapper


def _require_pro(func):
    """Decorator that gates a tool behind a Pro license tier."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tier = get_license_tier()
        if tier != "pro":
            return (
                f"This tool requires a ContextPulse Pro license. "
                f"Current tier: {'free' if not tier else tier}. "
                f"Upgrade at https://contextpulse.ai/pricing"
            )
        return func(*args, **kwargs)
    return wrapper


@mcp_app.tool()
@_track_call
def get_screenshot(mode: str = "active", monitor_index: int | None = None) -> Any:
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
@_track_call
def get_recent(count: int = 3, seconds: int = 60, min_diff: float = 0.0) -> list:
    """Get recent screenshots from the rolling buffer.

    The daemon auto-captures every few seconds. This returns the most recent
    frames from the buffer (up to `count` frames from the last `seconds` seconds).

    Args:
        count: Max number of frames to return (default 3).
        seconds: Look back this many seconds (default 60).
        min_diff: Minimum visual diff score (0-100) to include. Use to filter
                  out minor changes. E.g. min_diff=50 returns only frames where
                  the screen changed significantly (app switch, new page).

    Returns images inline so Claude can see them directly.
    """
    frames = _buffer.get_recent(seconds)
    if not frames:
        return []

    # Filter by diff score if requested — look up from activity DB
    if min_diff > 0:
        filtered = []
        for f in frames:
            parsed = parse_frame_path(f)
            if parsed:
                # Search activity DB for this frame's diff score
                results = _activity_db.search_by_frame(str(f))
                if results and results.get("diff_score", 0) >= min_diff:
                    filtered.append(f)
                elif not results:
                    filtered.append(f)  # no record, include by default
        frames = filtered

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
@_track_call
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
@_track_call
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

    # Count unique monitors and estimate token costs
    monitor_indices = set()
    total_image_tokens = 0
    total_text_tokens = 0
    text_frames = 0
    image_frames = 0
    for f in frames:
        parsed = parse_frame_path(f)
        if parsed:
            monitor_indices.add(parsed[1])
        txt_path = f.with_suffix(".txt") if f.suffix == ".jpg" else f
        if f.suffix == ".jpg":
            image_frames += 1
            try:
                img = Image.open(f)
                total_image_tokens += estimate_image_tokens(img.width, img.height)
            except Exception:
                pass
        if txt_path.exists():
            try:
                import json
                meta = json.loads(txt_path.read_text(encoding="utf-8"))
                text = meta.get("text", "")
                if text:
                    text_frames += 1
                    total_text_tokens += estimate_text_tokens(text)
            except Exception:
                pass

    lines = [
        f"Buffer: {count} frames across {len(monitor_indices)} monitor(s)",
        f"Monitors: {sorted(monitor_indices)}",
        f"Span: {newest_ts - oldest_ts:.0f}s of history",
        f"Latest frame: {age:.0f}s ago",
    ]

    if image_frames:
        avg_img = total_image_tokens // image_frames
        lines.append(f"Token cost (images): ~{total_image_tokens:,} total, ~{avg_img:,} avg/frame")
    if text_frames:
        avg_txt = total_text_tokens // text_frames
        lines.append(f"Token cost (text):   ~{total_text_tokens:,} total, ~{avg_txt:,} avg/frame")
    if image_frames and text_frames:
        savings = (1 - total_text_tokens / max(1, total_image_tokens)) * 100
        lines.append(f"Text vs image savings: {savings:.0f}% fewer tokens using text")

    lines.append(f"Directory: {OUTPUT_DIR / 'buffer'}")
    return "\n".join(lines)


@mcp_app.tool()
@_track_call
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
@_track_call
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
@_track_call
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


@mcp_app.tool()
@_track_call
def get_clipboard_history(count: int = 10) -> str:
    """Get recent clipboard contents captured by ContextPulse.

    Returns the last N clipboard entries (text that was copied). Useful for
    retrieving error messages, URLs, code snippets, or stack traces that
    were copied but may have been overwritten since.

    Args:
        count: Number of recent entries to return (default 10).
    """
    entries = _activity_db.get_clipboard_history(count)
    if not entries:
        return "No clipboard history recorded. Is the daemon running?"

    lines = [f"=== Clipboard History ({len(entries)} entries) ===\n"]
    for entry in entries:
        ts_str = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
        text = entry["text"]
        # Show first 200 chars with line count
        line_count = text.count("\n") + 1
        preview = text[:200].replace("\n", " \\n ")
        if len(text) > 200:
            preview += "..."
        lines.append(f"[{ts_str}] ({len(text)} chars, {line_count} lines)")
        lines.append(f"  {preview}")
        lines.append("")

    return "\n".join(lines)


@mcp_app.tool()
@_track_call
def search_clipboard(query: str, minutes_ago: int = 60) -> str:
    """Search clipboard history for specific text.

    Searches through captured clipboard contents. Useful for finding a
    specific error message, URL, or code snippet that was copied earlier.

    Args:
        query: Text to search for in clipboard history.
        minutes_ago: How far back to search (default 60 minutes).
    """
    results = _activity_db.search_clipboard(query, minutes_ago)
    if not results:
        return f"No clipboard entries matching '{query}' in the last {minutes_ago} minutes."

    lines = [f"=== Clipboard Search: '{query}' ({len(results)} results) ===\n"]
    for entry in results:
        ts_str = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
        text = entry["text"]
        preview = text[:300].replace("\n", " \\n ")
        if len(text) > 300:
            preview += "..."
        lines.append(f"[{ts_str}] ({len(text)} chars)")
        lines.append(f"  {preview}")
        lines.append("")

    return "\n".join(lines)


@mcp_app.tool()
@_track_call
def get_agent_stats(hours: float = 24.0) -> str:
    """Show which MCP clients have called ContextPulse tools and how often.

    Tracks tool usage per client. Useful for understanding which AI agents
    are actively consuming context from ContextPulse Sight.

    Args:
        hours: How many hours back to report (default 24).
    """
    stats = _activity_db.get_agent_stats(hours)
    if not stats["total_calls"]:
        return f"No MCP tool calls recorded in the last {hours:.0f} hours."

    lines = [f"=== Agent Stats (last {hours:.0f}h) ===\n"]
    lines.append(f"Total tool calls: {stats['total_calls']}")

    start, end = stats["time_range"]
    if start and end:
        lines.append(
            f"Time range: {datetime.fromtimestamp(start).strftime('%H:%M')} - "
            f"{datetime.fromtimestamp(end).strftime('%H:%M')}\n"
        )

    for client_id, tools in stats["clients"].items():
        total_for_client = sum(tools.values())
        lines.append(f"{client_id}: {total_for_client} calls")
        for tool_name, count in sorted(tools.items(), key=lambda x: -x[1]):
            lines.append(f"  {tool_name}: {count}")
        lines.append("")

    return "\n".join(lines)


@mcp_app.tool()
@_track_call
@_require_pro
def search_all_events(query: str, minutes_ago: int = 60, modality: str | None = None) -> str:
    """Search across ALL event types (screen, voice, clipboard, keys, flow) using full-text search.

    Cross-modal search powered by the ContextPulse spine. Finds when specific
    content appeared on screen, was spoken, typed, or copied — all in one query.

    Args:
        query: Search terms (searches window titles, app names, OCR text, transcripts, clipboard).
        minutes_ago: How far back to search (default 60 minutes).
        modality: Optional filter: "sight", "voice", "clipboard", "keys", "flow", "system".
    """
    bus = _get_event_bus()
    results = bus.search(query, minutes_ago=minutes_ago, modality=modality)

    if not results:
        scope = f" in {modality}" if modality else ""
        return f"No results for '{query}'{scope} in the last {minutes_ago} minutes."

    # Filter blocked titles
    filtered = [r for r in results if not is_title_blocked(r.get("window_title", ""))]
    skipped = len(results) - len(filtered)

    lines = [f"=== Cross-Modal Search: '{query}' ({len(filtered)} results) ===\n"]
    if skipped:
        lines.append(f"({skipped} result(s) hidden — privacy blocklist)\n")

    for r in filtered[:25]:
        ts_str = datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        mod = r.get("modality", "?")
        evt = r.get("event_type", "?")
        app = r.get("app_name", "")
        title = r.get("window_title", "")[:60]

        lines.append(f"[{ts_str}] [{mod}/{evt}] {app} — {title}")

        # Extract searchable text from payload
        try:
            import json as _json
            payload = _json.loads(r["payload"]) if isinstance(r["payload"], str) else r.get("payload", {})
            text = payload.get("ocr_text") or payload.get("transcript") or payload.get("text") or ""
            if text:
                snippet = text[:150].replace("\n", " ")
                lines.append(f"  {snippet}{'...' if len(text) > 150 else ''}")
        except Exception:
            pass
        lines.append("")

    return "\n".join(lines)


@mcp_app.tool()
@_track_call
@_require_pro
def get_event_timeline(minutes_ago: float = 5.0, modality: str | None = None) -> str:
    """Get a timeline of ALL events across modalities for the last N minutes.

    Shows what was happening across screen, voice, keyboard, mouse, and clipboard
    at a specific time window. Useful for understanding context around an event
    or reconstructing what the user was doing.

    Args:
        minutes_ago: How many minutes back to look (default 5).
        modality: Optional filter: "sight", "voice", "clipboard", "keys", "flow", "system".
    """
    bus = _get_event_bus()
    seconds = minutes_ago * 60
    events = bus.query_recent(seconds=seconds, modality=modality, limit=100)

    if not events:
        scope = f" ({modality})" if modality else ""
        return f"No events{scope} in the last {minutes_ago:.0f} minutes."

    # Filter blocked titles
    filtered = [e for e in events if not is_title_blocked(e.window_title)]
    skipped = len(events) - len(filtered)

    # Reverse to chronological order
    filtered = list(reversed(filtered))

    lines = [f"=== Event Timeline (last {minutes_ago:.0f}m, {len(filtered)} events) ===\n"]
    if skipped:
        lines.append(f"({skipped} event(s) hidden — privacy blocklist)\n")

    # Group by modality for summary
    modality_counts: dict[str, int] = {}
    for e in filtered:
        mod = e.modality.value if hasattr(e.modality, 'value') else str(e.modality)
        modality_counts[mod] = modality_counts.get(mod, 0) + 1

    lines.append("Modalities: " + ", ".join(f"{k}={v}" for k, v in sorted(modality_counts.items())))
    lines.append("")

    for e in filtered[:50]:
        ts_str = datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
        mod = e.modality.value if hasattr(e.modality, 'value') else str(e.modality)
        evt = e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type)
        app = e.app_name or ""
        title = e.window_title[:50] if e.window_title else ""

        line = f"[{ts_str}] {mod:>9}/{evt:<20} {app}"
        if title:
            line += f" — {title}"
        lines.append(line)

        # Show key payload info
        payload = e.payload if isinstance(e.payload, dict) else {}
        extras = []
        if payload.get("diff_score"):
            extras.append(f"diff={payload['diff_score']:.1%}")
        if payload.get("ocr_confidence"):
            extras.append(f"ocr_conf={payload['ocr_confidence']:.0%}")
        if payload.get("wpm"):
            extras.append(f"wpm={payload['wpm']}")
        if payload.get("word_count"):
            extras.append(f"words={payload['word_count']}")
        if extras:
            lines.append(f"  {'  '.join(extras)}")

    return "\n".join(lines)


def main():
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
