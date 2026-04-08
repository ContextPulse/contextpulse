#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Nightly vocabulary learning — entry point for scheduled task.

Runs the full consolidation pipeline and records accuracy metrics.
Exit codes: 0=success, 1=partial failure, 2=environment error.
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nightly_learning")


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

    from contextpulse_voice.config import VOICE_DATA_DIR
    if not VOICE_DATA_DIR.exists():
        logger.warning("Voice data dir missing, creating: %s", VOICE_DATA_DIR)
        VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    from contextpulse_voice.metrics import compute_accuracy_scorecard, record_metrics

    # "Before" scorecard
    before = compute_accuracy_scorecard()
    logger.info(
        "Before: %d transcriptions, %.1f%% correction rate, vocab=%d/%d/%d",
        before["total_transcriptions"],
        before["correction_rate"] * 100,
        before["vocab_user_size"],
        before["vocab_learned_size"],
        before["vocab_context_size"],
    )

    # Run consolidation (live mode)
    from contextpulse_voice.consolidator import consolidate_vocabulary

    summary = consolidate_vocabulary(dry_run=False)

    # "After" scorecard
    after = compute_accuracy_scorecard()
    logger.info(
        "After: vocab=%d/%d/%d (was %d/%d/%d)",
        after["vocab_user_size"],
        after["vocab_learned_size"],
        after["vocab_context_size"],
        before["vocab_user_size"],
        before["vocab_learned_size"],
        before["vocab_context_size"],
    )

    # Record metrics
    after["consolidation_summary"] = summary
    record_metrics(after)

    # Output JSON summary for scheduled task
    output = {
        "status": "success",
        "consolidation": summary,
        "vocab_before": {
            "user": before["vocab_user_size"],
            "learned": before["vocab_learned_size"],
            "context": before["vocab_context_size"],
        },
        "vocab_after": {
            "user": after["vocab_user_size"],
            "learned": after["vocab_learned_size"],
            "context": after["vocab_context_size"],
        },
    }
    print(json.dumps(output, indent=2))

    # Check for partial failures
    has_error = False
    for key in ("session_learned", "cross_modal", "ocr_harvested"):
        if summary.get(key, -1) == -1:
            has_error = True

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
