#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Nightly vocabulary learning pipeline entry point.

Orchestrates cross-modal consolidation: session learning, OCR harvesting,
clipboard harvesting, correction escalation, and context vocab rebuild.

Exits:
    0 — success
    1 — partial failure (some modules errored)
    2 — environment error (venv/db missing)
"""

import json
import logging
import sys
from pathlib import Path

# ── Environment checks ──────────────────────────────────────────────
try:
    from contextpulse_core.config import ACTIVITY_DB_PATH
    from contextpulse_voice.config import (
        CONTEXT_VOCAB_FILE,
        LEARNED_VOCAB_FILE,
        VOCAB_FILE,
    )
    from contextpulse_voice.consolidator import consolidate_vocabulary
except ImportError as exc:
    print(json.dumps({"status": "error", "error": f"Import failed: {exc}"}))
    sys.exit(2)

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def _vocab_size(path: Path) -> int:
    """Return number of entries in a JSON vocab file, or 0 if missing."""
    try:
        import json as _json
        return len(_json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return 0


def main() -> int:
    if not ACTIVITY_DB_PATH.exists():
        print(json.dumps({
            "status": "error",
            "error": f"activity.db not found at {ACTIVITY_DB_PATH}",
        }))
        return 2

    # Snapshot vocab sizes before
    vocab_before = {
        "user": _vocab_size(VOCAB_FILE),
        "learned": _vocab_size(LEARNED_VOCAB_FILE),
        "context": _vocab_size(CONTEXT_VOCAB_FILE),
    }

    # Run the consolidation pipeline
    try:
        summary = consolidate_vocabulary(db_path=ACTIVITY_DB_PATH, dry_run=False)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    # Snapshot vocab sizes after
    vocab_after = {
        "user": _vocab_size(VOCAB_FILE),
        "learned": _vocab_size(LEARNED_VOCAB_FILE),
        "context": _vocab_size(CONTEXT_VOCAB_FILE),
    }

    result = {
        "status": "success",
        "consolidation": {
            "session_learned": summary.get("session_learned", 0),
            "cross_modal": summary.get("cross_modal", 0),
            "ocr_harvested": summary.get("ocr_harvested", 0),
            "clipboard_harvested": summary.get("clipboard_harvested", 0),
            "escalated": summary.get("escalated", 0),
            "context_rebuilt": summary.get("context_rebuilt", 0),
            "deduped": summary.get("deduped", 0),
        },
        "vocab_before": vocab_before,
        "vocab_after": vocab_after,
    }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
