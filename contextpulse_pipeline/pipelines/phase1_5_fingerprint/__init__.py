# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.5 — ECAPA-TDNN cross-source speaker fingerprinting (GPU spot variant).

Sibling to ``pipelines.phase1_transcribe``. Uses the same _spot_fleet
machinery and reuses ``contextpulse_pipeline.speaker_fingerprint`` for the
algorithmic core. This package only contains the worker entrypoint + local
submission orchestrator — the algorithm itself is testable independently.

Typical wall time on g6.xlarge L4: ~2-4 minutes for 14 sources / 3.5 hr of
audio (most of the cost is audio I/O via ffmpeg, not the embedding pass).
"""
