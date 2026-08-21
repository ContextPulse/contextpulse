#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Retention sweep for ContextPulse's unbounded scratch/build directories.

Background — observed 2026-08-15: ``working/`` had grown to 4,339MB with no
retention policy (plus ``dist/`` 388MB, ``installer_output/`` 112MB, ``build/``
58MB) because these directories are gitignored working-tree scratch, outside
the janitor's delete allowlist, and nothing ever swept them
(``cp-working-dir-4gb-unbounded``).

This sweeps each configured target directory's immediate children: anything
whose newest file is older than ``--max-age-days`` (default 30) is a
candidate. Matches against ``--protect`` glob patterns are never touched,
regardless of age. This tool ships with NO default protect patterns baked
in -- a general-purpose retention sweep should never hardcode one specific
real project's directory name into its source (an early draft did exactly
that, naming a private individual's episode folder directly in tracked
source, tests and a commit message). Callers must supply ``--protect``
explicitly for anything that must survive regardless of age, or set the
``CP_RETENTION_PROTECT`` environment variable (comma-separated glob
patterns) for a standing local exclusion that never has to be typed and
never has to live in tracked source. This machine has standing protected
media under ``working/`` that this env var should cover locally -- see the
shared-knowledge journal for the specific finding, not this file.

**Dry-run by default.** Pass ``--execute`` to actually move candidates. Moves
go to the Windows Recycle Bin (``SHFileOperationW`` with ``FOF_ALLOWUNDO``),
never a permanent delete, per the workspace-wide "never permanently delete"
rule.

Usage:
    python scripts/retention_sweep.py                    # dry-run, prints plan
    python scripts/retention_sweep.py --execute           # actually sweeps
    python scripts/retention_sweep.py --max-age-days 60 --execute
"""

from __future__ import annotations

import argparse
import ctypes
import fnmatch
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TARGETS = ["working", "dist", "installer_output", "build", "dist2", "dist3", "build2", "build3"]
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_PROTECT_PATTERNS: list[str] = []


def env_protect_patterns() -> list[str]:
    """Additional protect globs from ``CP_RETENTION_PROTECT`` (comma-separated).

    Kept out of source deliberately: this is a general-purpose tool and must
    never hardcode any specific real directory's name. Operators set this
    env var locally, per machine, for anything that must never be swept
    regardless of age.
    """
    raw = os.environ.get("CP_RETENTION_PROTECT", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class SweepEntry:
    """One immediate child of a target directory, evaluated for sweeping."""

    path: Path
    size_bytes: int
    age_days: float
    protected: bool
    sweep: bool


def _dir_size_bytes(path: Path) -> int:
    """Total size of all files under path (recursive). 0 for an empty/missing dir."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def _newest_mtime(path: Path) -> float:
    """Newest mtime (epoch seconds) of path itself or any file under it.

    Using the newest file's mtime (not the directory's own mtime, which
    Windows does not reliably bump on child writes) means a directory with
    one recently-touched file inside is never swept, even if the directory
    entry itself looks old.
    """
    if path.is_file():
        return path.stat().st_mtime
    newest = path.stat().st_mtime
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest


def _is_protected(name: str, protect_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in protect_patterns)


def scan(
    project_root: Path,
    targets: list[str] | None = None,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    protect_patterns: list[str] | None = None,
    now: float | None = None,
) -> list[SweepEntry]:
    """Scan each target directory's immediate children and classify them.

    Each target directory (e.g. ``working/``) is walked one level: every
    immediate child (file or subdirectory) becomes one :class:`SweepEntry`.
    A target directory that does not exist is silently skipped (already
    reclaimed, or never created on this machine).
    """
    targets = DEFAULT_TARGETS if targets is None else targets
    protect_patterns = DEFAULT_PROTECT_PATTERNS if protect_patterns is None else protect_patterns
    now = time.time() if now is None else now

    entries: list[SweepEntry] = []
    for target_name in targets:
        target_dir = project_root / target_name
        if not target_dir.is_dir():
            continue
        for child in sorted(target_dir.iterdir()):
            protected = _is_protected(child.name, protect_patterns)
            age_days = (now - _newest_mtime(child)) / 86400
            size_bytes = _dir_size_bytes(child)
            sweep = (not protected) and age_days >= max_age_days
            entries.append(
                SweepEntry(path=child, size_bytes=size_bytes, age_days=age_days, protected=protected, sweep=sweep)
            )
    return entries


def send_to_recycle_bin(path: Path) -> bool:
    """Move a file or directory to the Windows Recycle Bin.

    Uses ``shell32.SHFileOperationW`` with ``FO_DELETE`` + ``FOF_ALLOWUNDO``
    (send to Recycle Bin, not permanent delete) + ``FOF_NOCONFIRMATION`` +
    ``FOF_SILENT`` (no UI, this runs unattended). Returns True on success,
    False otherwise. Never raises -- callers decide how to handle a failure,
    same contract as ``log_rotation.rotate_if_oversized``.
    """
    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    # pFrom must be double-null-terminated per SHFileOperationW contract.
    from_buf = str(path.resolve()) + "\0\0"
    op = SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=from_buf,
        pTo=None,
        fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
        return result == 0 and not op.fAnyOperationsAborted
    except OSError:
        logger.exception("SHFileOperationW failed for %s", path)
        return False


def format_report(entries: list[SweepEntry]) -> str:
    lines = []
    swept_bytes = 0
    for e in entries:
        tag = "PROTECTED" if e.protected else ("SWEEP" if e.sweep else "keep (too new)")
        lines.append(f"  [{tag:>15}] {e.size_bytes / 1024 / 1024:>8.1f} MB  age={e.age_days:6.1f}d  {e.path}")
        if e.sweep:
            swept_bytes += e.size_bytes
    lines.append(f"\nTotal reclaimable: {swept_bytes / 1024 / 1024:.1f} MB across "
                 f"{sum(1 for e in entries if e.sweep)} item(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS)
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--protect", nargs="+", default=DEFAULT_PROTECT_PATTERNS,
                         help="glob patterns (matched against the item's name) never to sweep. "
                              "Also merged with CP_RETENTION_PROTECT env var patterns, if set.")
    parser.add_argument("--execute", action="store_true", help="actually move candidates (default: dry-run)")
    args = parser.parse_args(argv)

    protect_patterns = list(args.protect) + env_protect_patterns()
    entries = scan(args.project_root, args.targets, args.max_age_days, protect_patterns)
    if not entries:
        print("Nothing found under any target directory.")
        return 0

    print(format_report(entries))

    if not args.execute:
        print("\nDRY RUN -- nothing moved. Re-run with --execute to sweep the items above.")
        return 0

    failures = 0
    for e in entries:
        if not e.sweep:
            continue
        ok = send_to_recycle_bin(e.path)
        if ok:
            logger.info("Recycled: %s", e.path)
        else:
            logger.error("Failed to recycle: %s", e.path)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
