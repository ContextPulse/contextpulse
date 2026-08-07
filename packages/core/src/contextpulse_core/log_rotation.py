# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Size-bounded log files for the daemon and its sibling entry points.

Background — observed 2026-08-07: ``contextpulse.log`` had grown to 431 MB and
``contextpulse_crash.log`` to 339 MB (770 MB combined) because every entry
point attached a plain :class:`logging.FileHandler` and the crash reporter
appended straight to an open file. Nothing ever truncated them.

Two shapes are needed:

* :func:`rotating_file_handler` — for the ``logging`` pipeline, a configured
  :class:`~logging.handlers.RotatingFileHandler`.
* :func:`rotate_if_oversized` — for the crash reporter, which writes with a
  bare ``open(..., "a")`` outside the ``logging`` machinery and so cannot use
  a handler.

Both read their limits from the environment so an operator can widen them
without a code change:

``CONTEXTPULSE_LOG_MAX_BYTES``
    Roll the active file once it exceeds this size. Default 10 MiB.
``CONTEXTPULSE_LOG_BACKUP_COUNT``
    How many rolled generations to keep. Default 3.

With the defaults, total on-disk usage per log is capped at ~40 MiB.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read a non-negative int from the environment, falling back on garbage.

    A malformed value must not stop the daemon from starting, so an
    unparseable or out-of-range value degrades to ``default`` rather than
    raising.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def get_max_bytes() -> int:
    """Size in bytes at which a log file is rolled."""
    return env_int("CONTEXTPULSE_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, minimum=1)


def get_backup_count() -> int:
    """Number of rolled generations to retain alongside the active file."""
    return env_int("CONTEXTPULSE_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT, minimum=0)


def rotating_file_handler(
    path: Path,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> RotatingFileHandler:
    """Build a size-bounded file handler for ``path``.

    Drop-in replacement for ``logging.FileHandler(path, encoding="utf-8")``.
    ``delay=True`` defers opening the file until the first record, which keeps
    import of an entry-point module from creating an empty log.
    """
    return RotatingFileHandler(
        path,
        maxBytes=max_bytes if max_bytes is not None else get_max_bytes(),
        backupCount=backup_count if backup_count is not None else get_backup_count(),
        encoding="utf-8",
        delay=True,
    )


def rotate_if_oversized(
    path: Path,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> bool:
    """Roll ``path`` to ``path.1`` when it has outgrown ``max_bytes``.

    Mirrors :class:`RotatingFileHandler` naming (``x.log`` -> ``x.log.1`` ->
    ``x.log.2`` ...) for files written outside the ``logging`` pipeline. Older
    generations past ``backup_count`` are dropped.

    Returns True if a rotation happened. Never raises: this runs on the crash
    path, where an OS error must not mask the crash being reported.
    """
    limit = max_bytes if max_bytes is not None else get_max_bytes()
    keep = backup_count if backup_count is not None else get_backup_count()
    try:
        if not path.is_file() or path.stat().st_size <= limit:
            return False
        if keep == 0:
            path.unlink()
            return True
        # Shift existing generations down, oldest first.
        oldest = path.with_name(f"{path.name}.{keep}")
        if oldest.exists():
            oldest.unlink()
        for generation in range(keep - 1, 0, -1):
            source = path.with_name(f"{path.name}.{generation}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{generation + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        logging.getLogger(__name__).warning("could not rotate %s", path, exc_info=True)
        return False
