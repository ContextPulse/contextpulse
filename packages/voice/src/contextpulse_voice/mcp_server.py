# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MCP server for ContextPulse Voice — exposes transcription history and vocabulary tools.

Entry point: contextpulse-voice-mcp (see pyproject.toml).
"""

import json
import logging
import sqlite3
import time

from contextpulse_core.config import ACTIVITY_DB_PATH
from mcp.server.fastmcp import FastMCP

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
    minutes = max(1, min(minutes, 10080))  # cap at 1 week
    limit = max(1, min(limit, 200))

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
    hours = max(0.1, min(hours, 168.0))  # cap at 1 week

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


@mcp_app.tool()
def learn_from_session(hours: int = 24, dry_run: bool = True) -> str:
    """Analyze recent dictation history and learn vocabulary corrections.

    Compares raw Whisper output with cleaned transcripts to find patterns
    where the LLM or existing vocabulary consistently corrected the same term.
    Patterns appearing 2+ times are learned automatically.

    Args:
        hours: How many hours of history to analyze. Default 24.
        dry_run: If True (default), show what would be learned without writing.
                Set to False to actually save corrections to vocabulary.
    """
    try:
        from contextpulse_voice.session_learner import learn_from_transcription_history
        results = learn_from_transcription_history(hours=hours, dry_run=dry_run)

        if not results:
            return "No learnable patterns found in recent transcription history."

        mode = "DRY RUN" if dry_run else "APPLIED"
        lines = [f"=== Session Learning ({mode}) — {len(results)} patterns ===\n"]
        for r in sorted(results, key=lambda x: -x["count"]):
            lines.append(
                f"  {r['original']!r:30s} -> {r['corrected']!r:20s} "
                f"(seen {r['count']}x, confidence {r['confidence']:.0%})"
            )

        if dry_run:
            lines.append("\nRun with dry_run=False to apply these corrections.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error analyzing transcription history: {e}"


@mcp_app.tool()
def rebuild_context_vocabulary() -> str:
    """Rebuild the context vocabulary from PROJECT_CONTEXT.md files.

    Scans ~/Projects/ for CamelCase project names and domain-specific terms
    that Whisper commonly splits. Writes to vocabulary_context.json which is
    hot-reloaded by the vocabulary system.
    """
    try:
        from contextpulse_voice.context_vocab import (
            get_context_entries,
        )
        from contextpulse_voice.context_vocab import (
            rebuild_context_vocabulary as rebuild,
        )
        count = rebuild()
        entries = get_context_entries()
        lines = [f"Rebuilt context vocabulary: {count} entries\n"]
        for misheard, correct in sorted(entries.items()):
            lines.append(f"  {misheard!r:30s} -> {correct!r}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error rebuilding context vocabulary: {e}"


@mcp_app.tool()
def consolidate_learning(dry_run: bool = True) -> str:
    """Run the full cross-modal vocabulary consolidation pipeline.

    Orchestrates all learning modules: session learning (transcript patterns),
    cross-modal correction mining (sight+voice+touch temporal correlations),
    OCR term harvesting, clipboard term harvesting, correction escalation,
    context vocabulary rebuild, and deduplication.

    This is the core learning loop that makes ContextPulse smarter over time.

    Args:
        dry_run: If True (default), analyze and report without writing changes.
                 Set to False to apply all learned corrections.
    """
    try:
        from contextpulse_voice.consolidator import consolidate_vocabulary

        summary = consolidate_vocabulary(dry_run=dry_run)
        mode = "DRY RUN" if dry_run else "APPLIED"
        lines = [f"=== Vocabulary Consolidation ({mode}) ===\n"]
        lines.append(f"  Session patterns learned:  {summary.get('session_learned', 0)}")
        lines.append(f"  Cross-modal corrections:   {summary.get('cross_modal', 0)}")
        lines.append(f"  OCR terms harvested:       {summary.get('ocr_harvested', 0)}")
        lines.append(f"  Clipboard terms harvested: {summary.get('clipboard_harvested', 0)}")
        lines.append(f"  Corrections escalated:     {summary.get('escalated', 0)}")
        lines.append(f"  Context vocab rebuilt:      {summary.get('context_rebuilt', 0)}")
        lines.append(f"  Duplicates removed:        {summary.get('deduped', 0)}")

        if dry_run:
            lines.append("\nRun with dry_run=False to apply all changes.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error running consolidation: {e}"


@mcp_app.tool()
def check_corrections(hours: int = 72, threshold: int = 3, dry_run: bool = True) -> str:
    """Check for repeated voice corrections that should become permanent vocabulary.

    Monitors touch-module correction events (paste-then-edit patterns) and
    promotes corrections that occur frequently to the learned vocabulary.

    Args:
        hours: How many hours of history to scan. Default 72 (3 days).
        threshold: Minimum correction count to trigger promotion. Default 3.
        dry_run: If True (default), report without writing. False to apply.
    """
    try:
        from contextpulse_voice.escalation import check_repeated_corrections

        results = check_repeated_corrections(
            hours=hours, threshold=threshold, dry_run=dry_run,
        )

        if not results:
            return f"No repeated corrections found (threshold: {threshold}x in {hours}h)."

        mode = "DRY RUN" if dry_run else "APPLIED"
        lines = [f"=== Correction Escalation ({mode}) — {len(results)} patterns ===\n"]
        for r in sorted(results, key=lambda x: -x.get("count", 0)):
            lines.append(
                f"  {r['original']!r:30s} -> {r['corrected']!r:20s} "
                f"(seen {r.get('count', '?')}x, action: {r.get('action', '?')})"
            )

        if dry_run:
            lines.append("\nRun with dry_run=False to promote these to vocabulary.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error checking corrections: {e}"


def main():
    """Entry point for MCP stdio server."""
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
