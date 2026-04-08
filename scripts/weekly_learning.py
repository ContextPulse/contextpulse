#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Weekly vocabulary learning — entry point for scheduled task.

Extends the nightly pipeline with vocabulary decay (removing stale entries),
full context vocabulary rebuild, and weekly trend reporting.
Exit codes: 0=success, 1=partial failure, 2=environment error.
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("weekly_learning")


def _decay_stale_entries(
    db_path: Path,
    days: int = 30,
) -> int:
    """Remove learned vocabulary entries not seen in any modality for N days.

    An entry is "seen" if its corrected form appears in OCR text, transcripts,
    or clipboard events within the decay window.

    Returns:
        Number of entries removed.
    """
    import sqlite3

    from contextpulse_voice.config import LEARNED_VOCAB_FILE

    if not LEARNED_VOCAB_FILE.exists():
        return 0

    learned: dict[str, str] = {}
    try:
        learned = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
        if not isinstance(learned, dict):
            return 0
    except (json.JSONDecodeError, OSError):
        return 0

    if not learned or not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path), timeout=2)
    cutoff = time.time() - (days * 86400)

    # Get all text content from recent events for checking
    rows = conn.execute(
        "SELECT payload FROM events "
        "WHERE timestamp > ? AND event_type IN ('ocr_result', 'transcription', 'clipboard_change') "
        "ORDER BY timestamp DESC LIMIT 5000",
        (cutoff,),
    ).fetchall()
    conn.close()

    # Build combined text for lookup
    all_text = ""
    for row in rows:
        try:
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            all_text += " " + payload.get("ocr_text", "")
            all_text += " " + payload.get("transcript", "")
            all_text += " " + payload.get("text", "")
        except (json.JSONDecodeError, TypeError):
            continue

    all_text_lower = all_text.lower()

    # Find entries not seen recently
    stale = []
    for key, value in learned.items():
        if value.lower() not in all_text_lower and key not in all_text_lower:
            stale.append(key)

    if stale:
        for key in stale:
            del learned[key]
        LEARNED_VOCAB_FILE.write_text(
            json.dumps(learned, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Decayed %d stale learned entries (not seen in %d days)", len(stale), days)

    return len(stale)


def main() -> int:
    # Verify environment
    try:
        from contextpulse_core.config import ACTIVITY_DB_PATH
    except ImportError:
        logger.error("contextpulse_core not installed — run from project venv")
        return 2

    if not ACTIVITY_DB_PATH.exists():
        logger.error("activity.db not found at %s", ACTIVITY_DB_PATH)
        return 2

    # Step 1: Vocabulary decay
    decayed = _decay_stale_entries(ACTIVITY_DB_PATH, days=30)
    logger.info("Vocabulary decay: removed %d stale entries", decayed)

    # Step 2: Full context vocabulary rebuild
    from contextpulse_voice.context_vocab import rebuild_context_vocabulary
    context_count = rebuild_context_vocabulary()
    logger.info("Context vocabulary rebuilt: %d entries", context_count)

    # Step 3: Weekly report
    from contextpulse_voice.metrics import generate_weekly_report
    report = generate_weekly_report(weeks=4)

    # Output JSON summary
    output = {
        "status": "success",
        "decayed_entries": decayed,
        "context_vocab_size": context_count,
        "weekly_report": report,
    }
    print(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
