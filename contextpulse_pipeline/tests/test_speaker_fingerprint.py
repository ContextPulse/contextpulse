# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for speaker_fingerprint (Phase 1.5 — ECAPA cross-source speaker ID)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from contextpulse_pipeline.speaker_fingerprint import (
    EmbeddingChunk,
    FingerprintResult,
    SpeakerCluster,
    assign_speakers_to_unified,
    cluster_embeddings,
    plan_chunks_from_unified,
)
from contextpulse_pipeline.unified_transcript import UnifiedSegment, UnifiedTranscript

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _embed_for(label: int, dim: int = 192, *, noise: float = 0.05, seed: int = 0) -> np.ndarray:
    """Synthetic embedding: a label-specific direction plus small noise.

    Two embeddings with the same label are very close; different labels are
    nearly orthogonal in expectation. Realistic stand-in for ECAPA outputs.
    """
    rng = np.random.default_rng(seed)
    base = np.zeros(dim, dtype=np.float32)
    base[label] = 1.0
    perturbed = base + rng.standard_normal(dim).astype(np.float32) * noise
    return perturbed / np.linalg.norm(perturbed)


def _make_unified_segment(
    wall_start: datetime,
    duration_sec: float,
    sha: str,
    text: str = "test",
) -> UnifiedSegment:
    return UnifiedSegment(
        wall_start_utc=wall_start,
        wall_end_utc=wall_start + timedelta(seconds=duration_sec),
        source_sha256=sha,
        source_filename=f"{sha[:8]}.wav",
        source_tier="A",
        text=text,
    )


# ---------------------------------------------------------------------------
# cluster_embeddings
# ---------------------------------------------------------------------------


class TestClusterEmbeddings:
    def test_recovers_three_speakers_from_synthetic(self) -> None:
        """Three synthetic 'speakers', each with several embeddings.
        Clustering should recover the 3 groups."""
        embeds: list[np.ndarray] = []
        true_labels: list[int] = []
        for spk in range(3):
            for i in range(8):
                embeds.append(_embed_for(spk, seed=spk * 10 + i))
                true_labels.append(spk)
        X = np.stack(embeds)

        clusters = cluster_embeddings(X, distance_threshold=0.5)

        assert len(clusters) == 3
        # Each cluster should be pure (all members from one true label)
        for c in clusters:
            members_true_labels = {true_labels[i] for i in c.member_indices}
            assert len(members_true_labels) == 1, (
                f"Cluster {c.label} has mixed truth: {members_true_labels}"
            )

    def test_handles_single_cluster(self) -> None:
        """All embeddings from one speaker → one cluster."""
        X = np.stack([_embed_for(0, seed=i) for i in range(10)])
        clusters = cluster_embeddings(X, distance_threshold=0.5)
        assert len(clusters) == 1
        assert clusters[0].size == 10

    def test_orders_clusters_by_size_descending(self) -> None:
        """Cluster with most members is labeled 'speaker_A', next 'speaker_B', etc."""
        embeds: list[np.ndarray] = []
        for spk, count in [(0, 10), (1, 5), (2, 2)]:
            for i in range(count):
                embeds.append(_embed_for(spk, seed=spk * 100 + i))
        X = np.stack(embeds)

        clusters = cluster_embeddings(X, distance_threshold=0.5)

        # Sorted by size descending
        assert clusters[0].size == 10
        assert clusters[1].size == 5
        assert clusters[2].size == 2
        assert clusters[0].label == "speaker_A"
        assert clusters[1].label == "speaker_B"
        assert clusters[2].label == "speaker_C"


# ---------------------------------------------------------------------------
# plan_chunks_from_unified
# ---------------------------------------------------------------------------


class TestPlanChunksFromUnified:
    def test_skips_short_segments(self) -> None:
        """Segments shorter than min_chunk_sec should be skipped (insufficient
        audio for a stable embedding)."""
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        segments = [
            _make_unified_segment(anchor, 0.5, "a" * 64),  # too short
            _make_unified_segment(anchor + timedelta(seconds=1), 5.0, "a" * 64),
        ]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        chunks = plan_chunks_from_unified(unified, min_chunk_sec=2.0, target_chunk_sec=4.0)

        assert len(chunks) == 1
        assert chunks[0].source_sha256 == "a" * 64

    def test_picks_one_chunk_per_segment_centered(self) -> None:
        """Each long-enough segment yields one chunk centered on its midpoint."""
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        segments = [
            _make_unified_segment(anchor, 10.0, "a" * 64),
        ]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        chunks = plan_chunks_from_unified(unified, min_chunk_sec=2.0, target_chunk_sec=4.0)

        assert len(chunks) == 1
        # Centered at midpoint (5s into the 10s segment), 4s wide → [3, 7]
        chunk = chunks[0]
        # source-relative_start_sec is 0 (we have no resolved-source metadata in this test)
        # The chunk's wall_start should be 3s after anchor
        expected_wall_start = anchor + timedelta(seconds=3)
        assert chunk.wall_start_utc == expected_wall_start
        assert abs(chunk.duration_sec - 4.0) < 0.01


# ---------------------------------------------------------------------------
# assign_speakers_to_unified
# ---------------------------------------------------------------------------


class TestAssignSpeakersToUnified:
    def test_assigns_label_from_nearest_chunk(self) -> None:
        """A unified segment should be assigned the speaker label of the
        chunk whose wall-time is closest to the segment's wall-time, AND
        whose source matches."""
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        sha_a = "a" * 64
        sha_b = "b" * 64

        # Segments
        segments = [
            _make_unified_segment(anchor, 5.0, sha_a, "from A"),
            _make_unified_segment(anchor + timedelta(seconds=10), 5.0, sha_b, "from B"),
        ]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        # Fingerprint result: chunk on A is speaker_A, chunk on B is speaker_B
        chunks = [
            EmbeddingChunk(
                source_sha256=sha_a,
                source_relative_start_sec=2.0,
                wall_start_utc=anchor + timedelta(seconds=2),
                duration_sec=4.0,
            ),
            EmbeddingChunk(
                source_sha256=sha_b,
                source_relative_start_sec=2.0,
                wall_start_utc=anchor + timedelta(seconds=12),
                duration_sec=4.0,
            ),
        ]
        clusters = [
            SpeakerCluster(label="speaker_A", member_indices=[0], centroid=np.zeros(2)),
            SpeakerCluster(label="speaker_B", member_indices=[1], centroid=np.zeros(2)),
        ]
        result = FingerprintResult(chunks=chunks, clusters=clusters)

        enriched = assign_speakers_to_unified(unified, result)

        assert enriched.segments[0].speaker_label == "speaker_A"
        assert enriched.segments[1].speaker_label == "speaker_B"

    def test_cross_source_assignment(self) -> None:
        """Bleed scenario: a chunk on B was clustered as speaker_A (same person
        speaking, captured in B's mic as bleed). A segment on B should still
        get the label from a chunk that's ON THE SAME SOURCE — we don't
        cross-attribute by wall-time alone."""
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        sha_a = "a" * 64
        sha_b = "b" * 64

        segments = [
            _make_unified_segment(anchor + timedelta(seconds=5), 2.0, sha_b, "B's mic"),
        ]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        # Two chunks at the same wall-time — one on A, one on B.
        # Clustering put A's chunk in speaker_A and B's in speaker_B.
        chunks = [
            EmbeddingChunk(
                source_sha256=sha_a,
                source_relative_start_sec=5.0,
                wall_start_utc=anchor + timedelta(seconds=5),
                duration_sec=4.0,
            ),
            EmbeddingChunk(
                source_sha256=sha_b,
                source_relative_start_sec=5.0,
                wall_start_utc=anchor + timedelta(seconds=5),
                duration_sec=4.0,
            ),
        ]
        clusters = [
            SpeakerCluster(label="speaker_A", member_indices=[0], centroid=np.zeros(2)),
            SpeakerCluster(label="speaker_B", member_indices=[1], centroid=np.zeros(2)),
        ]
        result = FingerprintResult(chunks=chunks, clusters=clusters)

        enriched = assign_speakers_to_unified(unified, result)

        # Segment on B should match B's chunk → speaker_B (NOT speaker_A despite
        # A's chunk having the same wall-time)
        assert enriched.segments[0].speaker_label == "speaker_B"


# ---------------------------------------------------------------------------
# Embedding extractor protocol — verify stub conforms
# ---------------------------------------------------------------------------


class TestStubEmbeddingExtractor:
    """The stub is used in tests; it must conform to the EmbeddingExtractor protocol."""

    def test_stub_returns_correct_shape(self) -> None:
        from contextpulse_pipeline.speaker_fingerprint import StubEmbeddingExtractor

        extractor = StubEmbeddingExtractor(dim=192)
        audio = np.random.randn(16000 * 4).astype(np.float32)
        emb = extractor.embed(audio, sample_rate=16000)
        assert emb.shape == (192,)
        assert emb.dtype == np.float32
        # L2-normalized
        assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-5
