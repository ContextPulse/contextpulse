# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Cap C-extension thread pools to keep the daemon resource footprint bounded.

Background — observed in production (2026-04-29): a fresh daemon spawned 163
threads within 2 seconds of startup, almost all of them idle workers from
ctranslate2 (faster-whisper), OpenMP, BLAS, and NumExpr. Each pool defaults
to ``cpu_count()`` workers, which on a 16-thread machine produces 4 pools x
~16 workers + Python module threads = ~80-160 baseline. The pools then drive
sustained ~30% CPU even on an idle machine because OCR + screen capture run
every 5 s and farm work out across all the workers.

Capping each pool to a small constant (default 2) drops baseline thread count
to ~30-50 and idle CPU proportionally. The trade-off is a small slowdown on
heavyweight workloads (Whisper transcription); for ContextPulse the work is
small (5-15 s of audio), so the wall-clock impact is negligible.

This module must be imported BEFORE numpy / faster-whisper / pytorch so that
the env vars are visible when those libraries initialize their pools. Entry
points (``daemon.py``, ``mcp_unified.py``) import this as their first
ContextPulse import; module-level ``apply_caps()`` runs as a side effect.

Override the cap by setting ``CONTEXTPULSE_CPU_THREADS`` in the environment
(e.g. for benchmarking) or by setting the individual ``OMP_NUM_THREADS`` etc.
vars yourself before launch — those are respected via ``setdefault``.
"""

from __future__ import annotations

import logging
import os
from typing import MutableMapping

logger = logging.getLogger(__name__)

_DEFAULT_CAP = 2

# Whisper is deliberately NOT capped at _DEFAULT_CAP. The 2026-04-29 incident
# this module exists for was four libraries each allocating ~cpu_count() IDLE
# workers; ctranslate2's pool is the one that does user-visible, latency-
# critical work, and capping it at the idle-pool number conflated "bound the
# idle footprint" with "throttle the hot path".
#
# The cost of that conflation was measured 2026-09-20 against David's report
# that long dictations "get stuck for a while". Whisper small/int8 on CPU,
# a real 75.0s clip, median of 3 runs, AMD Ryzen AI MAX+ 395 (32 logical):
#
#     cpu_threads=2  ->  8.74s  (0.117x realtime)  10 OS threads
#     cpu_threads=6  ->  6.32s  (0.084x realtime)  18 OS threads
#     cpu_threads=8  ->  6.33s  (0.084x realtime)  22 OS threads
#
# 6 is the knee: 28% faster than 2, while 8 buys nothing measurable and costs
# 4 more threads. Transcript output was byte-identical (1176 chars) at every
# setting, so this buys latency with threads and changes nothing else. The
# +8 threads land against a daemon baseline the module's own docstring puts
# at ~30-50, well clear of the 163 that triggered the original incident.
_DEFAULT_WHISPER_CAP = 6

_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",        # OpenMP (numpy, scipy, ctranslate2 intra-op)
    "MKL_NUM_THREADS",        # Intel MKL (numpy on Intel builds)
    "OPENBLAS_NUM_THREADS",   # OpenBLAS (numpy on most other builds)
    "NUMEXPR_NUM_THREADS",    # NumExpr (pandas eval)
)


def get_cap() -> int:
    """Return the configured per-pool thread cap.

    Reads ``CONTEXTPULSE_CPU_THREADS`` from the environment; falls back to
    :data:`_DEFAULT_CAP` (2) for invalid or missing values. Floors at 1 since
    a value of 0 is interpreted as "use system default" by several of the
    underlying libraries — which would defeat the purpose of capping.
    """
    raw = os.environ.get("CONTEXTPULSE_CPU_THREADS")
    if raw is None:
        return _DEFAULT_CAP
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_CAP


def get_whisper_cap() -> int:
    """Return the intra-op thread budget for the Whisper model specifically.

    Read by :class:`contextpulse_voice.transcriber.LocalTranscriber` for
    ``WhisperModel(cpu_threads=...)``. Deliberately a SEPARATE knob from
    :func:`get_cap`: this value is passed straight to ctranslate2 and is
    never written into ``OMP_NUM_THREADS`` and friends, so raising it
    cannot re-inflate the idle pools :func:`apply_caps` exists to bound.

    Overridable via ``CONTEXTPULSE_WHISPER_THREADS``. It does not read
    ``CONTEXTPULSE_CPU_THREADS`` — that var has a history of lingering as a
    stale persistent Windows user variable (see :func:`apply_caps`), and
    the hot path must not inherit a stray benchmark value.
    """
    raw = os.environ.get("CONTEXTPULSE_WHISPER_THREADS")
    if raw is None:
        return _DEFAULT_WHISPER_CAP
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_WHISPER_CAP


def apply_caps(
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply thread caps to ``environ`` (default: :data:`os.environ`).

    Uses :py:meth:`dict.setdefault` semantics so a value already present in
    the environment is preserved. Returns a dict ``{var: value}`` containing
    only the variables that were newly set (useful for tests and logging).
    """
    target: MutableMapping[str, str] = environ if environ is not None else os.environ
    cap = get_cap()
    if cap != _DEFAULT_CAP:
        # A silent override here is exactly how CONTEXTPULSE_CPU_THREADS=8 sat as
        # a stray persistent Windows user env var for months (left over from a
        # benchmarking session per this module's own docstring), quietly raising
        # every pool's baseline 4x and going unnoticed until a psutil-based
        # thread-budget monitor started firing near the original 163-thread
        # incident level. Logged via logging's handler-of-last-resort if this
        # runs before basicConfig(), since this module must import before any
        # entry point configures logging.
        logger.warning(
            "Thread pool cap overridden to %d (default %d) via "
            "CONTEXTPULSE_CPU_THREADS -- raises OMP/MKL/OPENBLAS/NUMEXPR pool "
            "baseline roughly proportionally. Unset the env var to restore the "
            "documented default unless this is a deliberate benchmark run.",
            cap, _DEFAULT_CAP,
        )
    cap_str = str(cap)
    applied: dict[str, str] = {}
    for var in _ENV_VARS:
        if var not in target:
            target[var] = cap_str
            applied[var] = cap_str
    return applied


# Side effect: apply caps at import time so that simply importing this module
# from an entry point is sufficient — callers don't have to remember to call
# apply_caps() explicitly.
apply_caps()
