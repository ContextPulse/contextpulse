# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Session-based vocabulary learning — extracts corrections from Claude Code sessions.

When the user dictates into Claude Code, Claude's responses often contain
properly-spelled versions of dictated terms. This module analyzes recent
transcription events and screen OCR to find these passive corrections.

Can be called:
1. At end-of-session via the /end-session skill
2. Periodically as part of nightly maintenance
3. Manually via the MCP tool `learn_from_session`
"""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from contextpulse_voice.config import LEARNED_VOCAB_FILE, VOICE_DATA_DIR

logger = logging.getLogger(__name__)

# Common English words that should never be added as vocabulary entries.
_COMMON_WORDS: set[str] = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
    "how", "its", "may", "new", "now", "old", "see", "way", "who", "did",
    "let", "say", "she", "too", "use", "about", "after", "again", "could",
    "every", "first", "found", "great", "house", "large", "later", "never",
    "other", "place", "right", "small", "still", "think", "three", "under",
    "water", "where", "which", "world", "would", "write", "stock", "trade",
    "model", "island", "personal", "screen", "context",
}


def learn_from_transcription_history(
    db_path: Path | None = None,
    hours: int = 24,
    min_occurrences: int = 2,
    dry_run: bool = True,
) -> list[dict]:
    """Analyze recent transcription history to find learnable patterns.

    Compares raw_transcript vs transcript (cleaned) across recent events
    to find systematic corrections the LLM or vocabulary made.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        hours: How many hours of history to analyze.
        min_occurrences: Minimum times a pattern must appear to be learned.
        dry_run: If True, return findings without writing to vocabulary.

    Returns:
        List of correction dicts: {original, corrected, count, confidence}
    """
    if db_path is None:
        from contextpulse_core.config import ACTIVITY_DB_PATH
        db_path = ACTIVITY_DB_PATH

    if not db_path.exists():
        logger.warning("activity.db not found at %s", db_path)
        return []

    conn = sqlite3.connect(str(db_path), timeout=2)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - (hours * 3600)

    rows = conn.execute(
        "SELECT payload FROM events "
        "WHERE modality = 'voice' AND event_type = 'transcription' "
        "AND timestamp > ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        logger.info("No transcription events found in last %d hours", hours)
        return []

    # Extract word-level diffs between raw and cleaned transcripts
    corrections: dict[str, dict] = {}  # key: "original -> corrected"

    for row in rows:
        payload = json.loads(row["payload"])
        raw = payload.get("raw_transcript", "")
        cleaned = payload.get("transcript", "")
        if not raw or not cleaned or raw == cleaned:
            continue

        # Find CamelCase words in cleaned that appear space-separated in raw
        camel_words = re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", cleaned)
        for word in camel_words:
            # Split CamelCase into potential Whisper output
            phrase = re.sub(r"([a-z])([A-Z])", r"\1 \2", word)
            phrase = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", phrase)
            phrase_lower = phrase.lower()

            if phrase_lower in raw.lower() and len(phrase_lower) >= 6:
                key = f"{phrase_lower} -> {word}"
                if key not in corrections:
                    corrections[key] = {
                        "original": phrase_lower,
                        "corrected": word,
                        "count": 0,
                    }
                corrections[key]["count"] += 1

    # Filter by minimum occurrences and safety
    results = []
    for item in corrections.values():
        if item["count"] < min_occurrences:
            continue
        # Safety: don't learn common words
        if item["original"] in _COMMON_WORDS:
            continue
        if any(w in _COMMON_WORDS for w in item["original"].split()):
            # Skip if ALL words are common (but allow if some are domain-specific)
            if all(w in _COMMON_WORDS for w in item["original"].split()):
                continue
        item["confidence"] = min(0.95, 0.5 + item["count"] * 0.1)
        results.append(item)

    if not dry_run and results:
        _write_learned(results)

    logger.info(
        "Session learning: analyzed %d events, found %d patterns (%s)",
        len(rows), len(results), "dry_run" if dry_run else "applied",
    )
    return results


def _write_learned(corrections: list[dict]) -> None:
    """Write corrections to vocabulary_learned.json with backup."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing learned vocabulary
    existing: dict[str, str] = {}
    if LEARNED_VOCAB_FILE.exists():
        try:
            existing = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Backup before writing
    if existing:
        backup = LEARNED_VOCAB_FILE.with_suffix(".json.bak")
        backup.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Merge new corrections (don't overwrite existing)
    added = 0
    for item in corrections:
        key = item["original"]
        if key not in existing:
            existing[key] = item["corrected"]
            added += 1
            logger.info("Learned: %r -> %r (count=%d)", key, item["corrected"], item["count"])

    if added:
        LEARNED_VOCAB_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Wrote %d new learned corrections (total: %d)", added, len(existing))
