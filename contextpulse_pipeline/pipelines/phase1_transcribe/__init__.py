# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1 Transcribe-Only pipeline variant.

What this pipeline does:
    Input  : N raw audio files + RawSourceCollection metadata
    Output : N per-source Whisper transcripts (JSON + TXT)

What this pipeline does NOT do:
    - Speaker diarization (Phase 1.5 / A.5)
    - Cross-source sync (A.3 sync_matcher consumes these transcripts)
    - Per-speaker extraction (Phase 1.6 / A.6)
    - Mastering or mixing (Phase 2)

This is the deliberately-minimal "just give me clean per-source transcripts"
shape, designed for the Phase 1 architecture where sync is derived AFTER
transcription, not assumed.

Components:
    worker.py — runs on a g6.xlarge spot instance, downloads audio from S3,
                transcribes, uploads JSON+TXT, self-terminates.
    submit.py — local orchestrator: uploads inputs, launches spot, polls,
                downloads results.
"""
