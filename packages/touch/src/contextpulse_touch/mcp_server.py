"""MCP server for ContextPulse Touch — exposes input activity and correction history.

Entry point: contextpulse-touch-mcp (see pyproject.toml).
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from contextpulse_core.config import ACTIVITY_DB_PATH

logger = logging.getLogger(__name__)

mcp_app = FastMCP("ContextPulse Touch")

_DB_PATH = ACTIVITY_DB_PATH


def _get_db() -> sqlite3.Connection | None:
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


@mcp_app.tool()
def get_recent_touch_events(seconds: int = 300, event_types: str = "all") -> str:
    """Get recent keyboard and mouse activity events.

    Returns typing bursts (word counts, WPM), clicks, scrolls, and drags.
    Privacy-safe: shows activity patterns, not keystrokes.

    Args:
        seconds: How many seconds back to look (default 300 = 5 min).
        event_types: Filter — "all", "keyboard", "mouse", or "corrections".
    """
    seconds = max(1, min(seconds, 86400))  # cap at 24h
    valid_types = {"all", "keyboard", "mouse", "corrections"}
    if event_types not in valid_types:
        return f"Invalid event_types '{event_types}'. Must be one of: {', '.join(sorted(valid_types))}"

    conn = _get_db()
    if not conn:
        return "No activity database found. Touch module may not have been started yet."

    try:
        cutoff = time.time() - seconds
        modality_filter = ""
        if event_types == "keyboard":
            modality_filter = "AND modality = 'keys'"
        elif event_types == "mouse":
            modality_filter = "AND modality = 'flow'"
        elif event_types == "corrections":
            modality_filter = "AND event_type = 'correction_detected'"

        rows = conn.execute(
            f"SELECT timestamp, modality, event_type, app_name, payload FROM events "
            f"WHERE (modality IN ('keys', 'flow')) {modality_filter} "
            f"AND timestamp > ? ORDER BY timestamp DESC LIMIT 50",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No touch events in the last {seconds} seconds."

        lines = [f"=== Recent Touch Events (last {seconds}s) ===\n"]
        for row in rows:
            payload = json.loads(row["payload"])
            ts = time.strftime("%H:%M:%S", time.localtime(row["timestamp"]))
            et = row["event_type"]
            app = row["app_name"] or ""

            if et == "typing_burst":
                chars = payload.get("char_count", 0)
                wpm = payload.get("wpm", 0)
                bs = payload.get("backspace_count", 0)
                lines.append(f"[{ts}] BURST {chars} chars, {wpm} WPM, {bs} backspaces")
            elif et == "click":
                btn = payload.get("button", "?")
                x, y = payload.get("x", 0), payload.get("y", 0)
                lines.append(f"[{ts}] CLICK {btn} ({x},{y}) in {app}")
            elif et == "scroll":
                dy = payload.get("dy", 0)
                lines.append(f"[{ts}] SCROLL dy={dy} in {app}")
            elif et == "drag":
                lines.append(f"[{ts}] DRAG in {app}")
            elif et == "correction_detected":
                orig = payload.get("original_text", "?")
                corr = payload.get("corrected_text", "?")
                conf = payload.get("confidence", 0)
                lines.append(f"[{ts}] CORRECTION: {orig!r} -> {corr!r} (conf={conf:.0%})")
            else:
                lines.append(f"[{ts}] {et} in {app}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading touch events: {e}"


@mcp_app.tool()
def get_touch_stats(hours: float = 8.0) -> str:
    """Get typing and mouse usage statistics over the last N hours.

    Returns keystroke count, average WPM, click/scroll counts, and correction count.

    Args:
        hours: How many hours back to analyze (default 8).
    """
    hours = max(0.1, min(hours, 168.0))  # cap at 1 week

    conn = _get_db()
    if not conn:
        return "No activity database found."

    try:
        cutoff = time.time() - (hours * 3600)

        # Keyboard stats
        key_rows = conn.execute(
            "SELECT payload FROM events WHERE modality = 'keys' "
            "AND event_type = 'typing_burst' AND timestamp > ?",
            (cutoff,),
        ).fetchall()

        total_chars = 0
        total_bursts = len(key_rows)
        wpms = []
        total_backspaces = 0
        for row in key_rows:
            p = json.loads(row["payload"])
            total_chars += p.get("char_count", 0)
            total_backspaces += p.get("backspace_count", 0)
            wpm = p.get("wpm", 0)
            if wpm > 0:
                wpms.append(wpm)

        avg_wpm = sum(wpms) / len(wpms) if wpms else 0

        # Mouse stats
        clicks = conn.execute(
            "SELECT COUNT(*) as c FROM events WHERE modality = 'flow' "
            "AND event_type = 'click' AND timestamp > ?",
            (cutoff,),
        ).fetchone()["c"]

        scrolls = conn.execute(
            "SELECT COUNT(*) as c FROM events WHERE modality = 'flow' "
            "AND event_type = 'scroll' AND timestamp > ?",
            (cutoff,),
        ).fetchone()["c"]

        # Corrections
        corrections = conn.execute(
            "SELECT COUNT(*) as c FROM events WHERE modality = 'keys' "
            "AND event_type = 'correction_detected' AND timestamp > ?",
            (cutoff,),
        ).fetchone()["c"]

        conn.close()

        lines = [
            f"=== Touch Stats (last {hours:.0f} hours) ===\n",
            f"Typing bursts: {total_bursts}",
            f"Total characters: {total_chars}",
            f"Average WPM: {avg_wpm:.0f}",
            f"Total backspaces: {total_backspaces}",
            f"Mouse clicks: {clicks}",
            f"Mouse scrolls: {scrolls}",
            f"Corrections detected: {corrections}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error calculating stats: {e}"


@mcp_app.tool()
def get_correction_history(limit: int = 20) -> str:
    """Get recent Voice correction detections.

    Shows what words/phrases the user corrected after voice dictation,
    along with confidence scores and timestamps. These corrections are
    automatically fed back to Voice's vocabulary for self-improving dictation.

    Args:
        limit: Maximum number of corrections to return (default 20).
    """
    limit = max(1, min(limit, 200))

    conn = _get_db()
    if not conn:
        return "No activity database found."

    try:
        rows = conn.execute(
            "SELECT timestamp, payload FROM events "
            "WHERE modality = 'keys' AND event_type = 'correction_detected' "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()

        if not rows:
            return "No corrections detected yet. Corrections are captured when you edit Voice-dictated text."

        lines = [f"=== Correction History (last {len(rows)}) ===\n"]
        for row in rows:
            p = json.loads(row["payload"])
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["timestamp"]))
            orig = p.get("original_text", "?")
            corr = p.get("corrected_text", "?")
            conf = p.get("confidence", 0)
            ctype = p.get("correction_type", "?")
            secs = p.get("seconds_after_paste", 0)

            lines.append(f"[{ts}] {orig!r} -> {corr!r}")
            lines.append(f"  Type: {ctype}, Confidence: {conf:.0%}, {secs:.1f}s after paste")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading corrections: {e}"


def main():
    """Entry point for MCP stdio server."""
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
