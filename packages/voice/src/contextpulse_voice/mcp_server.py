"""MCP server for ContextPulse Voice — exposes transcription history and vocabulary tools.

Entry point: contextpulse-voice-mcp (see pyproject.toml).
"""

import functools
import json
import logging
import sqlite3
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from contextpulse_core.config import ACTIVITY_DB_PATH

logger = logging.getLogger(__name__)

mcp_app = FastMCP("ContextPulse Voice")

_DB_PATH = ACTIVITY_DB_PATH


def _get_db() -> sqlite3.Connection | None:
    """Open the activity database (shared with Sight/EventBus)."""
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


@mcp_app.tool()
def get_recent_transcriptions(minutes: int = 30, limit: int = 10) -> str:
    """Get recent voice dictation transcriptions.

    Returns both raw (what Whisper heard) and cleaned (what was pasted) text.
    Useful for understanding dictation accuracy and what the user said recently.

    Args:
        minutes: How many minutes back to look (default 30).
        limit: Maximum number of transcriptions to return (default 10).
    """
    conn = _get_db()
    if not conn:
        return "No activity database found. Voice module may not have been started yet."

    try:
        cutoff = time.time() - (minutes * 60)
        rows = conn.execute(
            "SELECT timestamp, app_name, window_title, payload FROM events "
            "WHERE modality = 'voice' AND event_type = 'transcription' "
            "AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No transcriptions found in the last {minutes} minutes."

        lines = [f"=== Recent Transcriptions (last {minutes} min) ===\n"]
        for row in rows:
            payload = json.loads(row["payload"])
            ts = time.strftime("%H:%M:%S", time.localtime(row["timestamp"]))
            raw = payload.get("raw_transcript", "")
            cleaned = payload.get("transcript", "")
            app = row["app_name"] or "unknown"
            duration = payload.get("duration_seconds", 0)
            fix = " [FIX-LAST]" if payload.get("fix_last") else ""

            lines.append(f"[{ts}] ({app}, {duration:.1f}s){fix}")
            if raw != cleaned:
                lines.append(f"  Raw:     {raw}")
                lines.append(f"  Cleaned: {cleaned}")
            else:
                lines.append(f"  Text: {cleaned}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading transcriptions: {e}"


@mcp_app.tool()
def get_voice_stats(hours: float = 8.0) -> str:
    """Get voice dictation statistics over the last N hours.

    Returns dictation count, total duration, average length, and accuracy info.

    Args:
        hours: How many hours back to analyze (default 8).
    """
    conn = _get_db()
    if not conn:
        return "No activity database found."

    try:
        cutoff = time.time() - (hours * 3600)
        rows = conn.execute(
            "SELECT payload FROM events "
            "WHERE modality = 'voice' AND event_type = 'transcription' "
            "AND timestamp > ?",
            (cutoff,),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No dictations in the last {hours:.0f} hours."

        total = len(rows)
        total_duration = 0.0
        corrections = 0
        llm_cleanups = 0

        for row in rows:
            payload = json.loads(row["payload"])
            total_duration += payload.get("duration_seconds", 0)
            if payload.get("raw_transcript") != payload.get("transcript"):
                corrections += 1
            if payload.get("cleanup_applied"):
                llm_cleanups += 1

        avg_duration = total_duration / total if total else 0
        correction_rate = corrections / total * 100 if total else 0

        lines = [
            f"=== Voice Stats (last {hours:.0f} hours) ===\n",
            f"Total dictations: {total}",
            f"Total audio: {total_duration:.1f}s ({total_duration / 60:.1f} min)",
            f"Average duration: {avg_duration:.1f}s per dictation",
            f"Corrections applied: {corrections}/{total} ({correction_rate:.0f}%)",
            f"LLM cleanups: {llm_cleanups}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error calculating stats: {e}"


@mcp_app.tool()
def get_vocabulary(learned_only: bool = False) -> str:
    """Get current voice vocabulary entries (custom word corrections).

    Returns the vocabulary that maps misheard words to their correct forms.
    These corrections are applied automatically during transcription.

    Args:
        learned_only: If true, return only auto-learned entries (not user-defined).
    """
    try:
        from contextpulse_voice.vocabulary import get_all_entries, get_learned_entries

        if learned_only:
            entries = get_learned_entries()
            label = "Auto-Learned"
        else:
            entries = get_all_entries()
            label = "All (user + learned)"

        if not entries:
            return f"No {label.lower()} vocabulary entries found."

        lines = [f"=== {label} Vocabulary ({len(entries)} entries) ===\n"]
        for misheard, correct in sorted(entries.items()):
            lines.append(f"  {misheard!r:30s} -> {correct!r}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error reading vocabulary: {e}"


def main():
    """Entry point for MCP stdio server."""
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
