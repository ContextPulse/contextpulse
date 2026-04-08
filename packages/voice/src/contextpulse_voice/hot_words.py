# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Hot-word extraction from screen OCR for Whisper prompt biasing.

Extracts technical terms visible on-screen (CamelCase words, acronyms)
and formats them as a Whisper initial_prompt to improve recognition of
domain-specific words during real-time dictation.
"""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Common acronyms that appear everywhere and add no value as hot-words.
_COMMON_ACRONYMS: set[str] = {
    "OK", "AM", "PM", "US", "UK", "EU", "ID", "UI", "UX", "OS",
    "PC", "TV", "AC", "DC", "IT", "AI", "ML", "OR", "AN", "AS",
    "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "MY", "NO",
    "OF", "ON", "SO", "TO", "UP", "WE",
}

# CamelCase regex: at least two parts, each starting with uppercase
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")

# Acronym regex: 2-6 consecutive uppercase letters
_ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})\b")


def extract_hot_words(
    db_path: Path | None = None,
    seconds: int = 300,
    max_words: int = 50,
    min_confidence: float = 0.75,
) -> list[str]:
    """Extract technical terms from recent screen OCR events.

    Queries the events table for recent ocr_result events and extracts
    CamelCase words and domain-specific acronyms.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        seconds: Look-back window in seconds (default 5 minutes).
        max_words: Maximum number of hot-words to return.
        min_confidence: Minimum OCR confidence to include an event.

    Returns:
        List of unique hot-words sorted by frequency (most common first).
    """
    if db_path is None:
        try:
            from contextpulse_core.config import ACTIVITY_DB_PATH
            db_path = ACTIVITY_DB_PATH
        except Exception:
            return []

    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path), timeout=1)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - seconds

        rows = conn.execute(
            "SELECT payload FROM events "
            "WHERE modality = 'sight' AND event_type = 'ocr_result' "
            "AND timestamp > ? ORDER BY timestamp DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        conn.close()
    except (sqlite3.Error, OSError):
        logger.debug("Failed to query OCR events for hot-words", exc_info=True)
        return []

    if not rows:
        return []

    # Count term frequency across all OCR text
    freq: dict[str, int] = {}

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue

        confidence = payload.get("ocr_confidence", 0)
        if confidence < min_confidence:
            continue

        ocr_text = payload.get("ocr_text", "")
        if not ocr_text:
            continue

        # Extract CamelCase words
        for match in _CAMEL_RE.finditer(ocr_text):
            word = match.group(1)
            if len(word) >= 6:
                freq[word] = freq.get(word, 0) + 1

        # Extract acronyms (filtered)
        for match in _ACRONYM_RE.finditer(ocr_text):
            acr = match.group(1)
            if acr not in _COMMON_ACRONYMS:
                freq[acr] = freq.get(acr, 0) + 1

    # Sort by frequency (descending), then alphabetical
    sorted_words = sorted(freq.keys(), key=lambda w: (-freq[w], w))
    return sorted_words[:max_words]


def build_whisper_prompt(
    hot_words: list[str],
    context_vocab_nouns: list[str] | None = None,
    max_length: int = 200,
) -> str:
    """Build a Whisper initial_prompt from hot-words and context vocabulary.

    Combines screen-extracted hot-words with known proper nouns from
    the context vocabulary. Truncates to stay under Whisper's 224-token
    prompt limit (approximated by character count).

    Args:
        hot_words: Terms extracted from recent screen OCR.
        context_vocab_nouns: Known proper nouns from context_vocab.
        max_length: Maximum character length for the prompt.

    Returns:
        Comma-separated string of terms, or empty string if no terms.
    """
    # Deduplicate while preserving order (hot-words first, they're more timely)
    seen: set[str] = set()
    terms: list[str] = []

    for word in hot_words:
        lower = word.lower()
        if lower not in seen:
            seen.add(lower)
            terms.append(word)

    if context_vocab_nouns:
        for noun in context_vocab_nouns:
            lower = noun.lower()
            if lower not in seen:
                seen.add(lower)
                terms.append(noun)

    if not terms:
        return ""

    # Build comma-separated string, truncating to max_length
    prompt = ", ".join(terms)
    if len(prompt) <= max_length:
        return prompt

    # Truncate at the last complete term before max_length
    truncated = prompt[:max_length]
    last_comma = truncated.rfind(", ")
    if last_comma > 0:
        return truncated[:last_comma]
    return truncated
