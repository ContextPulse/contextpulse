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
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3

# A stuck error loop that fires every few seconds forever regrows a log
# faster than rotation can bound it usefully -- rotation caps the DAMAGE per
# generation, this caps the WASTE of writing (and rotating through) the same
# line thousands of times. See get_repeat_dedupe_filter below.
DEFAULT_REPEAT_THRESHOLD = 5
DEFAULT_REPEAT_EVERY = 50
DEFAULT_REPEAT_WINDOW_SECONDS = 300.0


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


class RepeatDedupeFilter(logging.Filter):
    """Rate-limit a log record that repeats identically, over and over.

    Background (cp-daemon-session0-blind-capture): a single stuck error --
    the same exception, same message, every ~30s -- produced 10,524
    consecutive identical "Auto-capture failed" lines over 4 days, 20.5MB.
    ``contextpulse.log`` survives that fine (it's behind a
    :class:`~logging.handlers.RotatingFileHandler`), but ``daemon_stderr.log``
    is an OS-level redirect of this process's stderr set up by
    ``daemon-watchdog.ps1``, rotated only at process RESTART (see that
    script's ``Rotate-StderrLog``). A daemon that logs the same error
    forever but never actually crashes can outrun that rotation entirely,
    because there is no restart to trigger it.

    After ``threshold`` consecutive occurrences of the same
    (logger name, level, formatted message) signature within
    ``window_seconds``, further repeats are suppressed except for one in
    every ``repeat_every`` -- so a stuck loop degrades from "every 30
    seconds forever" to "an occasional reminder that it is still stuck",
    never to total silence. A record whose signature differs from the
    previous one always resets the count and always passes through
    unmodified.

    Deliberately does NOT replace :func:`rotate_if_oversized` /
    :func:`rotating_file_handler` -- this bounds the RATE of a repeating
    message; rotation bounds the SIZE of whatever gets through. Fit this in
    alongside that existing mechanism rather than instead of it.
    """

    def __init__(
        self,
        threshold: int = DEFAULT_REPEAT_THRESHOLD,
        repeat_every: int = DEFAULT_REPEAT_EVERY,
        window_seconds: float = DEFAULT_REPEAT_WINDOW_SECONDS,
    ) -> None:
        super().__init__()
        self.threshold = max(1, threshold)
        self.repeat_every = max(1, repeat_every)
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._sig: tuple[str, int, str] | None = None
        self._count = 0
        self._window_start = 0.0

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 -- logging.Filter's own name
        try:
            message = record.getMessage()
        except Exception:
            # A record that can't even format itself must never be hidden
            # by a filter whose whole job is protecting log visibility.
            return True

        sig = (record.name, record.levelno, message)
        now = time.time()

        with self._lock:
            if sig != self._sig or (now - self._window_start) > self.window_seconds:
                self._sig = sig
                self._count = 1
                self._window_start = now
                return True

            self._count += 1
            count = self._count

        if count <= self.threshold:
            return True
        if count == self.threshold + 1:
            # One record announcing suppression has started, so the log
            # doesn't just go quiet with no explanation for why.
            record.msg = (
                f"{message} (repeating identically -- suppressing further "
                f"copies; 1 in {self.repeat_every} will still print)"
            )
            record.args = ()
            return True
        if count % self.repeat_every == 0:
            record.msg = f"{message} (repeated {count} times since this signature started)"
            record.args = ()
            return True
        return False


def get_repeat_dedupe_filter(
    threshold: int | None = None,
    repeat_every: int | None = None,
    window_seconds: float | None = None,
) -> RepeatDedupeFilter:
    """Build a :class:`RepeatDedupeFilter` reading defaults from the environment.

    ``CONTEXTPULSE_LOG_REPEAT_THRESHOLD`` -- allow this many identical
        records through before rate-limiting starts. Default 5.
    ``CONTEXTPULSE_LOG_REPEAT_EVERY`` -- after the threshold, let 1 in this
        many through. Default 50.
    ``CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC`` -- a gap longer than this between
        two occurrences of the same signature resets the count (read via
        env_int, so it shares that helper's degrade-to-default-on-garbage
        behavior for the two integer settings; the window is a float and is
        parsed directly here with the same never-raise contract).
    """
    window_default = DEFAULT_REPEAT_WINDOW_SECONDS
    if window_seconds is None:
        raw = os.environ.get("CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC")
        if raw is not None:
            try:
                parsed = float(raw)
                window_default = parsed if parsed > 0 else DEFAULT_REPEAT_WINDOW_SECONDS
            except ValueError:
                window_default = DEFAULT_REPEAT_WINDOW_SECONDS
    return RepeatDedupeFilter(
        threshold=threshold
        if threshold is not None
        else env_int("CONTEXTPULSE_LOG_REPEAT_THRESHOLD", DEFAULT_REPEAT_THRESHOLD, minimum=1),
        repeat_every=repeat_every
        if repeat_every is not None
        else env_int("CONTEXTPULSE_LOG_REPEAT_EVERY", DEFAULT_REPEAT_EVERY, minimum=1),
        window_seconds=window_seconds if window_seconds is not None else window_default,
    )
