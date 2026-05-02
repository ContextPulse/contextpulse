# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Audio pipeline variants — each subdir is a self-contained pipeline shape.

Variants:
    phase1_transcribe — Per-source transcription only. Inputs: raw audio.
                         Outputs: one Whisper JSON per source. No diarization,
                         no mastering, no mixing. Used as the foundation for
                         Phase 1 cross-source sync (sync_matcher) and downstream.

Future variants (planned):
    phase1_full      — Transcribe + cross-source sync + per-speaker extraction
    voice_isolation  — Stage 6 (per-speaker WeSep isolation given enrollment)
    podcast_master   — Mix N stems → mastered podcast (Auphonic-style)
"""
