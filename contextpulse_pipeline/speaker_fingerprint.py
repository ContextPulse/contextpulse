# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.5 — Cross-source speaker fingerprinting.

Goal: identify how many distinct voices exist across all the synced sources
in a container, and link them ("voice A in file 1 = voice A in file 7 = Josh").

Algorithm:
  1. From the unified transcript, plan one embedding chunk per long-enough
     speech segment (per-source clock, centered on the segment).
  2. Extract a voice embedding for each chunk via an EmbeddingExtractor
     (typically ECAPA-TDNN — pyannote or speechbrain).
  3. Cluster the embeddings via agglomerative clustering on cosine distance.
  4. Assign each unified-transcript segment a speaker label by matching
     the segment's (source, time) to its embedding chunk's cluster.

Local CPU runs are slow on hours of audio — the production deployment is a
GPU spot fleet variant `pipelines/phase1_5_fingerprint/` that wraps this
module and runs ECAPA on L4 (~RTF 0.05). This module is the orchestrator-
side library that the GPU worker imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from contextpulse_pipeline.unified_transcript import UnifiedTranscript

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHUNK_SEC = 2.0
DEFAULT_TARGET_CHUNK_SEC = 4.0
DEFAULT_DISTANCE_THRESHOLD = 0.5  # cosine distance in [0, 2]; ECAPA same-speaker ~0.1-0.3


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingChunk:
    """A planned audio chunk that will get a voice embedding extracted."""

    source_sha256: str
    source_relative_start_sec: float
    wall_start_utc: datetime
    duration_sec: float
    embedding: np.ndarray | None = None  # filled after extraction


@dataclass
class SpeakerCluster:
    """One cluster of embeddings, treated as one identified speaker."""

    label: str  # "speaker_A", "speaker_B", ...
    member_indices: list[int]
    centroid: np.ndarray

    @property
    def size(self) -> int:
        return len(self.member_indices)


@dataclass
class FingerprintResult:
    """End-to-end output of Phase 1.5."""

    chunks: list[EmbeddingChunk] = field(default_factory=list)
    clusters: list[SpeakerCluster] = field(default_factory=list)

    @property
    def n_speakers(self) -> int:
        return len(self.clusters)


# ---------------------------------------------------------------------------
# EmbeddingExtractor protocol + stub
# ---------------------------------------------------------------------------


class EmbeddingExtractor(Protocol):
    """Anything that turns mono float32 audio into a fixed-dim embedding.

    Production implementation is an ECAPA-TDNN model (192 dims). The
    contract: returns an L2-normalized 1-D float32 array. Same speaker
    → cosine distance < 0.4. Different speakers → cosine distance > 0.5.
    """

    def embed(self, audio: np.ndarray, *, sample_rate: int) -> np.ndarray: ...


class StubEmbeddingExtractor:
    """Test/scaffold extractor — returns a deterministic vector keyed on
    the audio's SHA256, just to wire up the pipeline mechanics. Replace with
    the real ECAPA call before production use.
    """

    def __init__(self, dim: int = 192) -> None:
        self.dim = dim

    def embed(self, audio: np.ndarray, *, sample_rate: int) -> np.ndarray:
        import hashlib

        # Deterministic per-content vector (no actual speaker information!)
        h = hashlib.sha256(audio.tobytes()).digest()
        rng = np.random.default_rng(int.from_bytes(h[:8], "big") % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / np.linalg.norm(v)


# Real ECAPA implementation — DEFERRED until ECAPA dependencies are installed
# either locally (speechbrain) or on a GPU worker (pyannote). When you wire
# this up, plug in:
#
#   from speechbrain.inference.speaker import EncoderClassifier
#
#   class ECAPAExtractor:
#       def __init__(self) -> None:
#           self.model = EncoderClassifier.from_hparams(
#               source="speechbrain/spkrec-ecapa-voxceleb",
#               run_opts={"device": "cuda" if torch.cuda.is_available() else "cpu"},
#           )
#
#       def embed(self, audio, *, sample_rate):
#           import torch
#           wav = torch.from_numpy(audio).unsqueeze(0)
#           emb = self.model.encode_batch(wav).squeeze().detach().cpu().numpy()
#           return emb / np.linalg.norm(emb)


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------


def plan_chunks_from_unified(
    unified: UnifiedTranscript,
    *,
    min_chunk_sec: float = DEFAULT_MIN_CHUNK_SEC,
    target_chunk_sec: float = DEFAULT_TARGET_CHUNK_SEC,
) -> list[EmbeddingChunk]:
    """For each unified-transcript segment long enough to embed, plan one chunk.

    The chunk is centered on the segment's midpoint and clipped to the
    segment's duration. Caller is responsible for resolving source-relative
    offsets when actually extracting audio.
    """
    chunks: list[EmbeddingChunk] = []
    for seg in unified.segments:
        seg_dur = (seg.wall_end_utc - seg.wall_start_utc).total_seconds()
        if seg_dur < min_chunk_sec:
            continue
        # Center the chunk; clamp to segment bounds
        chunk_dur = min(target_chunk_sec, seg_dur)
        center_offset = seg_dur / 2
        chunk_wall_start = seg.wall_start_utc + timedelta(seconds=center_offset - chunk_dur / 2)
        # source_relative_start_sec is the chunk's offset relative to the source's t=0;
        # we need the segment's source-relative start (Whisper segment.start) to compute it.
        # For now we leave source_relative_start_sec relative to wall_start_utc;
        # actual audio extractors should compute it from (chunk_wall_start - resolved.wall_start_utc).
        # We store wall-times here as the canonical reference.
        chunks.append(
            EmbeddingChunk(
                source_sha256=seg.source_sha256,
                source_relative_start_sec=center_offset - chunk_dur / 2,
                wall_start_utc=chunk_wall_start,
                duration_sec=chunk_dur,
            )
        )
    return chunks


def extract_embeddings_for_chunks(
    chunks: list[EmbeddingChunk],
    audio_paths: dict[str, Path],
    extractor: EmbeddingExtractor,
    *,
    sample_rate: int = 16000,
) -> list[EmbeddingChunk]:
    """Run the EmbeddingExtractor on each chunk's audio. Returns the same
    list with `embedding` populated (in-place). Chunks whose audio cannot be
    extracted are dropped.
    """
    from contextpulse_pipeline.audio_sync import load_audio_window

    out: list[EmbeddingChunk] = []
    for chunk in chunks:
        audio_path = audio_paths.get(chunk.source_sha256)
        if audio_path is None or not audio_path.exists():
            logger.warning("Audio missing for %s; skipping chunk", chunk.source_sha256[:8])
            continue
        # We use source_relative_start_sec which the planner tracks per-chunk
        audio = load_audio_window(
            audio_path,
            chunk.source_relative_start_sec,
            chunk.duration_sec,
            sample_rate=sample_rate,
        )
        if len(audio) < int(0.5 * sample_rate):
            continue
        chunk.embedding = extractor.embed(audio, sample_rate=sample_rate).astype(np.float32)
        out.append(chunk)
    return out


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def cluster_embeddings(
    embeddings: np.ndarray,
    *,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> list[SpeakerCluster]:
    """Agglomerative clustering on cosine distance.

    `distance_threshold` controls when to STOP merging — clusters whose nearest
    pair has cosine distance > threshold are kept separate. For ECAPA-TDNN
    embeddings, 0.4-0.6 is the typical sweet spot (same-speaker pairs cluster
    around 0.1-0.3, different-speaker around 0.7-1.2).

    Returns clusters sorted by size descending, labeled "speaker_A",
    "speaker_B", etc.
    """
    if embeddings.ndim != 2:
        raise ValueError(f"Expected 2-D embedding matrix, got shape {embeddings.shape}")
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [
            SpeakerCluster(label="speaker_A", member_indices=[0], centroid=embeddings[0].copy())
        ]

    clusterer = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
    )
    labels_arr = clusterer.fit_predict(embeddings)

    raw_clusters: dict[int, list[int]] = {}
    for i, lab in enumerate(labels_arr):
        raw_clusters.setdefault(int(lab), []).append(i)

    cluster_objects: list[SpeakerCluster] = []
    for indices in raw_clusters.values():
        members = embeddings[indices]
        centroid = members.mean(axis=0)
        cluster_objects.append(SpeakerCluster(label="", member_indices=indices, centroid=centroid))

    cluster_objects.sort(key=lambda c: -c.size)
    for idx, c in enumerate(cluster_objects):
        c.label = f"speaker_{chr(ord('A') + idx)}"
    return cluster_objects


# ---------------------------------------------------------------------------
# Segment-to-speaker assignment
# ---------------------------------------------------------------------------


def assign_speakers_to_unified(
    unified: UnifiedTranscript,
    fingerprint: FingerprintResult,
) -> UnifiedTranscript:
    """Stamp each UnifiedSegment with the speaker_label of the closest
    same-source chunk.

    Returns a NEW UnifiedTranscript (does not mutate the input) — the
    segments are dataclasses but we replace `speaker_label` only.

    Same-source matching is critical: when speaker A is bleed-captured in
    speaker B's mic, we still want B's microphone segments labeled with
    whoever's mic this is, not whoever's voice is dominant in the bleed.
    Cross-source speaker identity (Phase 1.5's job) and per-channel mic-
    ownership (this assignment's job) are different questions.
    """
    cluster_label_for_chunk: dict[int, str] = {}
    for cluster in fingerprint.clusters:
        for idx in cluster.member_indices:
            cluster_label_for_chunk[idx] = cluster.label

    chunks_by_source: dict[str, list[tuple[int, EmbeddingChunk]]] = {}
    for i, chunk in enumerate(fingerprint.chunks):
        chunks_by_source.setdefault(chunk.source_sha256, []).append((i, chunk))

    # Sort each source's chunks by wall_start_utc for fast nearest lookup
    for chunks_list in chunks_by_source.values():
        chunks_list.sort(key=lambda kv: kv[1].wall_start_utc)

    new_segments = []
    for seg in unified.segments:
        candidates = chunks_by_source.get(seg.source_sha256, [])
        if not candidates:
            new_segments.append(seg)
            continue
        # Find chunk closest in wall-time
        best_idx = min(
            candidates,
            key=lambda kv: abs((kv[1].wall_start_utc - seg.wall_start_utc).total_seconds()),
        )[0]
        label = cluster_label_for_chunk.get(best_idx)
        # Build a new segment with speaker_label set
        from dataclasses import replace

        new_segments.append(replace(seg, speaker_label=label))

    return UnifiedTranscript(
        container=unified.container,
        anchor_origination_utc=unified.anchor_origination_utc,
        segments=new_segments,
        unreachable_sources=list(unified.unreachable_sources),
        missing_transcripts=list(unified.missing_transcripts),
    )
