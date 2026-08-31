#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""One-time cleanup of pre-guard garbage in the live voice context vocabulary.

Background -- cp-vocab-context-file-has-existing-garbage: is_safe_harvested_phrase()
(added for cp-vocab-camelcases-common-phrases) only guards NEW entries going into
vocabulary_context.json. merge_terms_to_context_vocab() is additive-only, so OCR/
clipboard-whitespace-collapse artifacts and ordinary multi-word English phrases
harvested BEFORE the guard existed are still sitting in the live file, which the
running voice daemon reads on every startup.

This is not safe to script-delete unilaterally: some multi-word or long-token
entries are legitimate scan-sourced proper nouns (project/skill names), and those
never go through is_safe_harvested_phrase() at all -- see that function's own
docstring. find_stale_harvested_garbage() (packages/voice/src/contextpulse_voice/
context_vocab.py) only flags an entry when a fresh directory/skills scan does NOT
explain it AND the current guard would reject it today. This script is the
reviewed, backed-up application of that function against the live file.

**Dry-run by default.** Pass --execute to actually remove flagged entries. A
timestamped backup of the live file is always written first when --execute is
used, and the write is atomic (temp file + os.replace) so a crash mid-write
cannot leave the live file the daemon reads half-written or truncated.

Usage:
    python scripts/cleanup_vocab_garbage.py              # dry-run, prints candidates
    python scripts/cleanup_vocab_garbage.py --execute     # backs up, then removes them
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "packages" / "voice" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from contextpulse_voice.context_vocab import (  # noqa: E402
    CONTEXT_VOCAB_FILE,
    find_stale_harvested_garbage,
    get_context_entries,
)


def _write_atomic(path: Path, data: dict[str, str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Actually remove flagged entries (default: dry-run)"
    )
    args = parser.parse_args()

    if not CONTEXT_VOCAB_FILE.exists():
        print(f"No context vocab file at {CONTEXT_VOCAB_FILE} -- nothing to clean.")
        return 0

    entries = get_context_entries()
    garbage = find_stale_harvested_garbage(entries)

    print(f"Live file: {CONTEXT_VOCAB_FILE} ({len(entries)} entries)")
    print(f"Flagged as pre-guard garbage: {len(garbage)} entries")
    for key, value in sorted(garbage.items()):
        print(f"  {key!r} -> {value!r}")

    if not garbage:
        print("Nothing to remove.")
        return 0

    if not args.execute:
        print(f"\nDry-run only. Re-run with --execute to remove these {len(garbage)} entries.")
        return 0

    backup_path = CONTEXT_VOCAB_FILE.with_name(
        f"{CONTEXT_VOCAB_FILE.stem}.backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    _write_atomic(backup_path, entries)
    print(f"Backup written: {backup_path}")

    cleaned = {k: v for k, v in entries.items() if k not in garbage}
    _write_atomic(CONTEXT_VOCAB_FILE, cleaned)
    print(f"Removed {len(garbage)} entries. {len(cleaned)} entries remain in {CONTEXT_VOCAB_FILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
