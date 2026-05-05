# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for speaker_fingerprint (Phase 1.5 — ECAPA cross-source speaker ID)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# ---------------------------------------------------------------------------
# ECAPAExtractor — lazy load contract
# ---------------------------------------------------------------------------


class TestECAPAExtractorLazyLoad:
    """The real ECAPA extractor must be CHEAP to construct (no heavy imports
    at __init__ time) so that contract verification, type-checking, and tests
    can run in environments where speechbrain isn't installed."""

    def test_construct_does_not_import_speechbrain(self, monkeypatch) -> None:
        """Construction MUST NOT trigger the speechbrain import."""
        # Pre-install a sentinel that would fire if speechbrain were imported
        import sys

        from contextpulse_pipeline.speaker_fingerprint import ECAPAExtractor

        called = {"n": 0}
        original_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def tripwire_import(name, *args, **kwargs):
            if name.startswith("speechbrain"):
                called["n"] += 1
            return original_import(name, *args, **kwargs)

        # Don't actually monkey-patch __import__ here (too invasive); instead
        # check whether the module is in sys.modules after construction.
        mod_before = "speechbrain" in sys.modules
        ext = ECAPAExtractor()
        mod_after = "speechbrain" in sys.modules
        assert mod_before == mod_after, "Constructing ECAPAExtractor must not import speechbrain"
        assert ext._model is None

    def test_embed_raises_actionable_error_when_speechbrain_missing(self, monkeypatch) -> None:
        """If speechbrain isn't installed, .embed() must raise a RuntimeError
        whose message tells the user how to fix it."""
        import sys

        from contextpulse_pipeline.speaker_fingerprint import ECAPAExtractor

        # Force the import path to fail by stubbing out the speechbrain module
        # name so `from speechbrain.inference.speaker import EncoderClassifier`
        # raises ImportError. This works even if speechbrain IS installed.
        for k in [k for k in sys.modules if k.startswith("speechbrain")]:
            monkeypatch.delitem(sys.modules, k, raising=False)
        # Block future imports
        import importlib.abc
        import importlib.machinery

        class _BlockSpeechbrain(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):  # noqa: D401 (not a docstring lint issue)
                if fullname.startswith("speechbrain"):
                    raise ImportError("speechbrain blocked for test")
                return None

        finder = _BlockSpeechbrain()
        sys.meta_path.insert(0, finder)
        try:
            ext = ECAPAExtractor()
            audio = np.zeros(16000, dtype=np.float32)
            try:
                ext.embed(audio, sample_rate=16000)
            except RuntimeError as exc:
                msg = str(exc).lower()
                assert "speechbrain" in msg
                assert "install" in msg or "phase1_5" in msg
                return
            raise AssertionError("Expected RuntimeError but no exception was raised")
        finally:
            sys.meta_path.remove(finder)

    def test_embed_rejects_multidim_audio(self) -> None:
        """Stereo / batched audio is a caller bug — fail fast with a clear error."""
        from contextpulse_pipeline.speaker_fingerprint import ECAPAExtractor

        ext = ECAPAExtractor()
        bad_audio = np.zeros((2, 16000), dtype=np.float32)
        try:
            ext.embed(bad_audio, sample_rate=16000)
        except ValueError as exc:
            assert "mono" in str(exc).lower() or "1-d" in str(exc).lower()
            return
        raise AssertionError("Expected ValueError on multi-dim audio")


class TestResampler:
    def test_resample_passthrough_when_rates_match(self) -> None:
        from contextpulse_pipeline.speaker_fingerprint import _resample

        audio = np.random.randn(16000).astype(np.float32)
        out = _resample(audio, 16000, 16000)
        assert out is audio  # exact pass-through

    def test_resample_changes_length_proportionally(self) -> None:
        from contextpulse_pipeline.speaker_fingerprint import _resample

        audio = np.random.randn(48000).astype(np.float32)  # 1 sec at 48 kHz
        out = _resample(audio, 48000, 16000)
        assert abs(len(out) - 16000) <= 1


# ---------------------------------------------------------------------------
# FingerprintResult JSON roundtrip
# ---------------------------------------------------------------------------


class TestFingerprintResultJsonRoundtrip:
    def test_to_from_json_preserves_structure(self, tmp_path) -> None:
        from contextpulse_pipeline.speaker_fingerprint import FingerprintResult

        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        sha_a = "a" * 64
        chunk = EmbeddingChunk(
            source_sha256=sha_a,
            source_relative_start_sec=2.0,
            wall_start_utc=anchor,
            duration_sec=4.0,
            embedding=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )
        cluster = SpeakerCluster(
            label="speaker_A",
            member_indices=[0],
            centroid=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )
        original = FingerprintResult(chunks=[chunk], clusters=[cluster])

        path = tmp_path / "result.json"
        original.to_json(path=path)
        loaded = FingerprintResult.from_json(path=path)

        assert len(loaded.chunks) == 1
        assert loaded.chunks[0].source_sha256 == sha_a
        assert loaded.chunks[0].source_relative_start_sec == 2.0
        assert loaded.chunks[0].wall_start_utc == anchor
        assert loaded.chunks[0].duration_sec == 4.0
        assert loaded.chunks[0].embedding is not None
        np.testing.assert_allclose(loaded.chunks[0].embedding, [0.1, 0.2, 0.3], rtol=1e-5)

        assert len(loaded.clusters) == 1
        assert loaded.clusters[0].label == "speaker_A"
        assert loaded.clusters[0].member_indices == [0]
        np.testing.assert_allclose(loaded.clusters[0].centroid, [0.1, 0.2, 0.3], rtol=1e-5)

    def test_include_embeddings_false_strips_vectors(self, tmp_path) -> None:
        from contextpulse_pipeline.speaker_fingerprint import FingerprintResult

        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        chunk = EmbeddingChunk(
            source_sha256="a" * 64,
            source_relative_start_sec=0.0,
            wall_start_utc=anchor,
            duration_sec=4.0,
            embedding=np.zeros(192, dtype=np.float32),
        )
        result = FingerprintResult(chunks=[chunk], clusters=[])
        text = result.to_json(include_embeddings=False)
        assert '"embedding"' not in text
        # round-trip: embedding should be None after load
        loaded = FingerprintResult.from_json(text)
        assert loaded.chunks[0].embedding is None

    def test_from_json_requires_text_or_path(self) -> None:
        from contextpulse_pipeline.speaker_fingerprint import FingerprintResult

        try:
            FingerprintResult.from_json()
        except ValueError as exc:
            assert "text or path" in str(exc).lower()
            return
        raise AssertionError("Expected ValueError")


# ---------------------------------------------------------------------------
# run_fingerprinting orchestrator (with stub extractor + on-disk audio)
# ---------------------------------------------------------------------------


class TestRunFingerprintingOrchestrator:
    """End-to-end orchestrator test using StubEmbeddingExtractor + synthetic
    on-disk WAV files. Validates the full plan->extract->cluster wiring
    without needing speechbrain installed."""

    def _write_wav(
        self, path: Path, *, duration_sec: float, freq_hz: float, sample_rate: int = 16000
    ) -> None:
        """Write a tone WAV to disk via numpy + raw f32le + a wav header."""
        import wave

        n = int(duration_sec * sample_rate)
        t = np.linspace(0, duration_sec, num=n, endpoint=False, dtype=np.float64)
        samples = (np.sin(2 * np.pi * freq_hz * t) * 0.3).astype(np.float32)
        # Convert to int16 for WAV
        ints = (samples * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(ints.tobytes())

    def test_orchestrator_returns_clusters_for_two_sources(self, tmp_path) -> None:
        from contextpulse_pipeline.speaker_fingerprint import (
            StubEmbeddingExtractor,
            run_fingerprinting,
        )

        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        sha_a = "a" * 64
        sha_b = "b" * 64

        # Two sources, each with one long-enough segment
        segments = [
            _make_unified_segment(anchor, 5.0, sha_a, "from A"),
            _make_unified_segment(anchor + timedelta(seconds=10), 5.0, sha_b, "from B"),
        ]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        # Write tone WAVs (different freqs so audio bytes differ → stub
        # extractor produces different embeddings)
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        self._write_wav(wav_a, duration_sec=10.0, freq_hz=300)
        self._write_wav(wav_b, duration_sec=10.0, freq_hz=600)

        audio_paths = {sha_a: wav_a, sha_b: wav_b}
        result = run_fingerprinting(
            unified,
            audio_paths,
            StubEmbeddingExtractor(dim=64),
            min_chunk_sec=2.0,
            target_chunk_sec=4.0,
        )

        assert len(result.chunks) == 2
        # Stub embeddings keyed on audio bytes — different files → distinct
        # embeddings → typically separate clusters at threshold=0.5. The
        # exact count depends on the random direction; assert at least 1.
        assert result.n_speakers >= 1

    def test_orchestrator_returns_empty_when_no_segments_long_enough(self, tmp_path) -> None:
        from contextpulse_pipeline.speaker_fingerprint import (
            StubEmbeddingExtractor,
            run_fingerprinting,
        )

        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        # All segments are 0.5s — below the 2.0s min_chunk_sec
        segments = [_make_unified_segment(anchor, 0.5, "a" * 64)]
        unified = UnifiedTranscript(container="t", anchor_origination_utc=anchor, segments=segments)

        result = run_fingerprinting(unified, {}, StubEmbeddingExtractor(dim=8), min_chunk_sec=2.0)
        assert result.n_speakers == 0
        assert len(result.chunks) == 0

    def test_max_clusters_merges_smallest(self) -> None:
        """When max_clusters caps the count, the smallest cluster should be
        merged into the nearest larger one."""
        from contextpulse_pipeline.speaker_fingerprint import (
            _merge_smallest_clusters,
            cluster_embeddings,
        )

        # 3 well-separated speakers with 8/5/2 members
        embeds: list[np.ndarray] = []
        for spk, count in [(0, 8), (1, 5), (2, 2)]:
            for i in range(count):
                embeds.append(_embed_for(spk, dim=64, seed=spk * 100 + i))
        X = np.stack(embeds)

        clusters = cluster_embeddings(X, distance_threshold=0.5)
        assert len(clusters) == 3

        merged = _merge_smallest_clusters(clusters, X, max_clusters=2)
        assert len(merged) == 2
        # Total members preserved
        total = sum(c.size for c in merged)
        assert total == 15  # 8 + 5 + 2

        # Re-labeled by size descending
        assert merged[0].label == "speaker_A"
        assert merged[1].label == "speaker_B"
