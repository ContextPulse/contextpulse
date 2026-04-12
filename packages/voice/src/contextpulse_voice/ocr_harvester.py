# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""OCR term harvester — extracts technical vocabulary from screen OCR events.

Batch-processes recent OCR events to find frequently-occurring CamelCase
and dot-separated terms (e.g., "Next.js") that should be added to the
context vocabulary for improved voice recognition.
"""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from contextpulse_voice.config import CONTEXT_VOCAB_FILE, VOICE_DATA_DIR
from contextpulse_voice.context_vocab import _COMMON_PHRASES, _split_camel_to_phrase

logger = logging.getLogger(__name__)

# CamelCase regex: at least two parts, each starting with uppercase
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")

# Dot-separated names (e.g., "Next.js", "Vue.js", "Node.js")
_DOT_NAME_RE = re.compile(r"\b([A-Z][a-z]+\.[a-z]{1,4})\b")


def harvest_ocr_terms(
    db_path: Path | None = None,
    hours: int = 24,
    min_occurrences: int = 3,
    min_confidence: float = 0.75,
    dry_run: bool = True,
) -> list[dict]:
    """Extract technical terms from recent OCR events.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        hours: Look-back window in hours.
        min_occurrences: Minimum frequency to include a term.
        min_confidence: Minimum OCR confidence threshold.
        dry_run: If True, return findings without writing to vocabulary.

    Returns:
        List of dicts: {term, phrase, count, source: 'ocr'}
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
        "WHERE modality = 'sight' AND event_type = 'ocr_result' "
        "AND timestamp > ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # Count term frequency
    freq: dict[str, int] = {}

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue

        if payload.get("ocr_confidence", 0) < min_confidence:
            continue

        ocr_text = payload.get("ocr_text", "")
        if not ocr_text:
            continue

        # CamelCase terms
        for match in _CAMEL_RE.finditer(ocr_text):
            word = match.group(1)
            if len(word) >= 6:
                freq[word] = freq.get(word, 0) + 1

        # Dot-separated names
        for match in _DOT_NAME_RE.finditer(ocr_text):
            word = match.group(1)
            freq[word] = freq.get(word, 0) + 1

    # Filter by minimum occurrences and common phrases
    results = []
    for term, count in freq.items():
        if count < min_occurrences:
            continue
        phrase = _split_camel_to_phrase(term)
        if phrase and phrase in _COMMON_PHRASES:
            continue
        results.append({
            "term": term,
            "phrase": phrase or term.lower(),
            "count": count,
            "source": "ocr",
        })

    if not dry_run and results:
        _merge_to_context_vocab(results)

    logger.info(
        "OCR harvest: scanned %d events, found %d terms (%s)",
        len(rows), len(results), "dry_run" if dry_run else "applied",
    )
    return results


def _merge_to_context_vocab(terms: list[dict]) -> None:
    """Merge new terms into vocabulary_context.json (additive only)."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if CONTEXT_VOCAB_FILE.exists():
        try:
            existing = json.loads(CONTEXT_VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    added = 0
    for item in terms:
        key = item["phrase"]
        if key not in existing:
            existing[key] = item["term"]
            added += 1

    if added:
        CONTEXT_VOCAB_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("OCR harvest: added %d new context vocab entries", added)
