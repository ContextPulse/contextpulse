"""contextpulse_pipeline — foundational transcription + synthesis pipeline.

AGPL-3.0. Part of the ContextPulse platform.

Public API:
    BatchPipeline   — local-runnable batch pipeline (Mode A)
    AudioSourceTier — enum of source quality tiers (A=best, C=lowest)
    ContainerState  — lifecycle states (open, finalized, published, superseded)

Usage:
    from contextpulse_pipeline import BatchPipeline, AudioSourceTier, ContainerState
    from pathlib import Path

    pipeline = BatchPipeline(
        container="ep-2026-04-26-my-episode",
        config={"synthesis_prompts": {"summary": "Summarize this."}},
        s3_client=boto3.client("s3"),
        bucket="my-bucket",
    )
    pipeline.ingest([Path("audio/recording.opus")])
    pipeline.finalize()

v0.1 scope: batch mode with Groq Whisper API + Anthropic Sonnet synthesis.
v0.2 scope: GPU spot workers (Inferentia2/G6), WhisperX alignment, diarization.
v0.3 scope: StreamingPipeline for live/real-time workloads.
"""

from __future__ import annotations

from enum import Enum

from contextpulse_pipeline.manifest import ContainerState, Manifest
from contextpulse_pipeline.workers.batch import BatchPipeline, ConcurrentSynthesisError


class AudioSourceTier(str, Enum):
    """Canonical audio source tier hierarchy.

    Tier A = highest quality (broadcast WAV, lavalier mic).
    Tier B = mid quality (phone direct recording).
    Tier C = lowest quality (transport-compressed, Telegram upload).

    Higher tier wins on time overlap (supersession).
    Consumer configs may define their own tier names -- this enum provides
    the canonical three-tier baseline that AF and other products use.
    """

    A = "A"
    B = "B"
    C = "C"


__all__ = [
    "BatchPipeline",
    "AudioSourceTier",
    "ContainerState",
    "ConcurrentSynthesisError",
    "Manifest",
]

__version__ = "0.1.0"
