#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""CLI: recover orphaned voice recordings left by daemon crashes.

Usage
-----
    # List orphans without transcribing
    python scripts/transcribe-orphans.py --dry-run

    # Transcribe everything older than 2 minutes
    python scripts/transcribe-orphans.py

    # Use a larger model for better accuracy
    python scripts/transcribe-orphans.py --model medium

    # Delete WAVs after successful recovery (keep only the .txt sidecars)
    python scripts/transcribe-orphans.py --delete

Each successfully transcribed WAV gets a `.txt` sidecar written next
to it with the transcript and recovery metadata. Failed transcripts
leave the WAV in place for retry.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the package importable when run from a checkout
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from contextpulse_voice.orphan_recovery import (  # noqa: E402
    find_orphan_recordings,
    recover_all,
)
from contextpulse_voice.voice_module import RECORDINGS_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover orphaned ContextPulse voice recordings.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=RECORDINGS_DIR,
        help=f"Recordings directory (default: {RECORDINGS_DIR})",
    )
    parser.add_argument(
        "--model",
        default="small",
        choices=["base", "small", "medium", "large"],
        help="Whisper model size (default: small)",
    )
    parser.add_argument(
        "--min-age-seconds",
        type=float,
        default=120.0,
        help="Skip files younger than N seconds (default: 120)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete WAVs after successful recovery (keeps .txt sidecars)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List orphans without transcribing",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.dir.is_dir():
        print(f"Recordings directory does not exist: {args.dir}")
        return 0

    if args.dry_run:
        orphans = find_orphan_recordings(args.dir, args.min_age_seconds)
        if not orphans:
            print(f"No orphans found in {args.dir}")
            return 0
        print(f"Found {len(orphans)} orphan(s) in {args.dir}:")
        for p in orphans:
            size_kb = p.stat().st_size // 1024
            print(f"  {p.name}  ({size_kb} KB)")
        return 0

    print(f"Scanning {args.dir} (model={args.model}, delete={args.delete})...")
    summary = recover_all(
        args.dir,
        min_age_seconds=args.min_age_seconds,
        delete_on_success=args.delete,
        model_size=args.model,
    )

    print()
    print(f"  Scanned:   {summary['scanned']}")
    print(f"  Recovered: {summary['recovered']}")
    print(f"  Failed:    {summary['failed']}")
    print(f"  Skipped:   {summary['skipped']} (in-flight, < {args.min_age_seconds:.0f}s)")
    print()

    for r in summary["results"]:
        if r["ok"]:
            print(f"  OK  {r['wav']}  -> {r['sidecar']}  ({r['chars']} chars, {r['seconds']}s)")
        else:
            print(f"  FAIL {r['wav']}  (left on disk)")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
