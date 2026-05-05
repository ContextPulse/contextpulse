# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 6 — Voice isolation per speaker (GPU spot variant).

Sibling to ``pipelines.phase1_5_fingerprint``. Uses the same _spot_fleet
machinery and reuses ``contextpulse_pipeline.voice_isolation`` for the
algorithmic core. Runs WeSep target speaker extraction on g6.xlarge spot.

Cross-source merging (``cross_source_merger.merge_all_speakers``) runs
LOCALLY after the GPU job completes — it's pure CPU and doesn't benefit from
the GPU.
"""
