# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Re-export of the shared redaction engine, which now lives in core.

The pattern table moved to ``contextpulse_core.redact`` when redaction stopped
being a screen-only concern: voice transcripts, typed bursts, touch corrections,
the memory store and the knowledge bridge all store captured text, and all of
them depend on ``contextpulse-core`` while none depends on
``contextpulse-screen``. One table, one set of patterns, one place to fix.

This module stays as the import path the sight package (and the purge script,
and the existing tests) already use. Do not add patterns here -- add them to
``contextpulse_core.redact`` or the two copies will drift, which is the exact
failure the consolidation exists to prevent.
"""

from contextpulse_core.redact import (
    _CATEGORY_RE,
    _PATTERNS,
    category_of,
    redact_payload,
    redact_sensitive,
    redact_with_counts,
)

__all__ = [
    "_CATEGORY_RE",
    "_PATTERNS",
    "category_of",
    "redact_payload",
    "redact_sensitive",
    "redact_with_counts",
]
