# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Clipboard term harvester — extracts CamelCase vocabulary from clipboard events.

Batch-processes recent clipboard_change events to find domain-specific terms
the user copies frequently. Filters out code blocks, URLs, and file paths.
"""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# CamelCase regex: at least two parts
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")

# Patterns to skip (noise)
_URL_RE = re.compile(r"https?://")
_PATH_RE = re.compile(r"[A-Z]:\\|/home/|/usr/|/opt/")


def harvest_clipboard_terms(
    db_path: Path | None = None,
    hours: int = 24,
    min_length: int = 6,
    dry_run: bool = True,
) -> list[dict]:
    """Extract CamelCase terms from recent clipboard events.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        hours: Look-back window in hours.
        min_length: Minimum CamelCase word length to include.
        dry_run: If True, return findings without side effects.

    Returns:
        List of dicts: {term, phrase, count, source: 'clipboard'}
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
        "WHERE modality = 'clipboard' AND event_type = 'clipboard_change' "
        "AND timestamp > ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    freq: dict[str, int] = {}

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue

        text = payload.get("text", "")
        if not text:
            continue

        # Skip long entries (likely code blocks)
        if len(text) > 200:
            continue

        # Skip URLs and file paths
        if _URL_RE.search(text) or _PATH_RE.search(text):
            continue

        for match in _CAMEL_RE.finditer(text):
            word = match.group(1)
            if len(word) >= min_length:
                freq[word] = freq.get(word, 0) + 1

    results = []
    from contextpulse_voice.context_vocab import _COMMON_PHRASES, _split_camel_to_phrase

    for term, count in freq.items():
        phrase = _split_camel_to_phrase(term)
        if phrase and phrase in _COMMON_PHRASES:
            continue
        results.append({
            "term": term,
            "phrase": phrase or term.lower(),
            "count": count,
            "source": "clipboard",
        })

    logger.info(
        "Clipboard harvest: scanned %d events, found %d terms (%s)",
        len(rows), len(results), "dry_run" if dry_run else "applied",
    )
    return results
