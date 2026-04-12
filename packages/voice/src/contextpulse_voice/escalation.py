# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Correction escalation — promotes repeated corrections to learned vocabulary.

Monitors correction_detected events from the Touch module. When a word
has been corrected 3+ times within 72 hours and isn't already in the
learned vocabulary, it gets promoted via VocabularyBridge.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def check_repeated_corrections(
    db_path: Path | None = None,
    hours: int = 72,
    threshold: int = 3,
    dry_run: bool = True,
) -> list[dict]:
    """Find corrections that recur above threshold and escalate them.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        hours: Look-back window in hours.
        threshold: Minimum correction count to trigger escalation.
        dry_run: If True, return findings without writing to vocabulary.

    Returns:
        List of dicts: {original, corrected, count, action}
        action is one of: 'added', 'already_exists', 'dry_run'
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
        "WHERE modality = 'keys' AND event_type = 'correction_detected' "
        "AND timestamp > ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # Group corrections by original word
    corrections: dict[str, dict] = {}

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue

        original = payload.get("original_word", "").strip().lower()
        corrected = payload.get("corrected_word", "").strip()

        if not original or not corrected or original == corrected.lower():
            continue

        if original not in corrections:
            corrections[original] = {"corrected": corrected, "count": 0}
        corrections[original]["count"] += 1

    # Filter by threshold
    results = []
    bridge = None

    for original, data in corrections.items():
        if data["count"] < threshold:
            continue

        action = "dry_run"
        if not dry_run:
            if bridge is None:
                from contextpulse_touch.correction_detector import VocabularyBridge
                bridge = VocabularyBridge()

            added = bridge.add_correction(original, data["corrected"])
            action = "added" if added else "already_exists"

        results.append({
            "original": original,
            "corrected": data["corrected"],
            "count": data["count"],
            "action": action,
        })

    logger.info(
        "Escalation check: scanned %d events, %d above threshold (%s)",
        len(rows), len(results), "dry_run" if dry_run else "applied",
    )
    return results
