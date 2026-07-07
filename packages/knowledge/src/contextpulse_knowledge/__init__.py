# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""ContextPulse Knowledge — local bi-temporal fused knowledge-graph spine (Phase 1).

Semantic contract lives in ``cp_core`` (pure logic, the referee). The SQLite
adapter (``store_sqlite``), the conformance harness, and any future Rust crate
all defer to cp_core's semantics. See
``.internal/fable-redesign/phase1-build-ready-design-2026-07-07-v2.md``.
"""

__version__ = "0.1.0"
