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

import os
from typing import MutableMapping

_DEFAULT_CAP = 2

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


def apply_caps(
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply thread caps to ``environ`` (default: :data:`os.environ`).

    Uses :py:meth:`dict.setdefault` semantics so a value already present in
    the environment is preserved. Returns a dict ``{var: value}`` containing
    only the variables that were newly set (useful for tests and logging).
    """
    target: MutableMapping[str, str] = environ if environ is not None else os.environ
    cap_str = str(get_cap())
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
