# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for contextpulse_pipeline.cluster_validation.

Covers the cluster -> person identity gate that lives between Phase 1.5
(anonymous speaker_A/B/C clusters) and Stage 6 voice isolation. Skipping
this gate produced speaker-mixing on the Josh hike (skill rule 2026-05-03).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from contextpulse_pipeline.cluster_validation import (
    CONFIDENCE_REVIEW_FLOOR,
    ClusterIdentityReviewRequired,
    EpisodeManifest,
    ExpectedSpeaker,
    apply_identity_to_isolation,
    validate_cluster_identity,
    warn_if_anonymous_labels,
    write_cluster_review_samples,
)
from contextpulse_pipeline.speaker_fingerprint import (
    EmbeddingChunk,
    FingerprintResult,
    SpeakerCluster,
)
from contextpulse_pipeline.unified_transcript import (
    UnifiedSegment,
    UnifiedTranscript,
)
from contextpulse_pipeline.voice_isolation import IsolatedTrack, IsolationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_segment(
    start: datetime, text: str, source_sha: str, speaker_label: str
) -> UnifiedSegment:
    return UnifiedSegment(
        wall_start_utc=start,
        wall_end_utc=start + timedelta(seconds=10),
        source_sha256=source_sha,
        source_filename="test.wav",
        source_tier="A",
        text=text,
        avg_logprob=-0.3,
        speaker_label=speaker_label,
    )


def _build_unified(segments: list[UnifiedSegment]) -> UnifiedTranscript:
    anchor = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    return UnifiedTranscript(
        container="ep-test",
        anchor_origination_utc=anchor,
        segments=segments,
    )


def _make_chunk(source_sha: str, start_sec: float, dur: float) -> EmbeddingChunk:
    return EmbeddingChunk(
        source_sha256=source_sha,
        source_relative_start_sec=start_sec,
        wall_start_utc=datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc),
        duration_sec=dur,
        embedding=np.zeros(192, dtype=np.float32),
    )


def _build_fingerprint_result(
    cluster_to_chunks: dict[str, list[EmbeddingChunk]]
) -> FingerprintResult:
    """Build a FingerprintResult from a {cluster_label: chunks} mapping."""
    all_chunks: list[EmbeddingChunk] = []
    clusters: list[SpeakerCluster] = []
    for label, chunks in cluster_to_chunks.items():
        start = len(all_chunks)
        all_chunks.extend(chunks)
        member_indices = list(range(start, start + len(chunks)))
        clusters.append(
            SpeakerCluster(
                label=label,
                member_indices=member_indices,
                centroid=np.zeros(192, dtype=np.float32),
            )
        )
    return FingerprintResult(chunks=all_chunks, clusters=clusters)


def _build_manifest_3speakers() -> EpisodeManifest:
    """Josh-hike-shaped manifest: David / Chris / Josh with the 2026-05-02
    fingerprint additions."""
    return EpisodeManifest(
        episode_id="ep-test",
        expected_speakers=[
            ExpectedSpeaker(
                id="david",
                display_name="David",
                role="host",
                mic_channel=None,
                fingerprints=["AWS", "EC2", "trading bot", "spot instance", "AgentConfig"],
                fingerprint_phrases=[
                    "I have a fleet of trading bots",
                    "skill that creates skills",
                ],
            ),
            ExpectedSpeaker(
                id="chris",
                display_name="Chris",
                role="host",
                mic_channel="TX00",
                fingerprints=["Outside Inc", "180 contracts", "FreeSkier", "Cowork"],
                fingerprint_phrases=["brand activations", "marketing funnel"],
            ),
            ExpectedSpeaker(
                id="josh",
                display_name="Josh",
                role="guest",
                mic_channel="TX01",
                fingerprints=["QoEAgent", "Tom Yeh", "Ron Neufeld", "transaction advisory"],
                fingerprint_phrases=["quality of earnings", "proof of cash"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: high-confidence match (the happy path)
# ---------------------------------------------------------------------------


def test_high_confidence_match_routes_each_cluster_to_correct_speaker():
    """3 clusters with strong distinctive content -> all assigned, no review."""
    base = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    segments = [
        # speaker_A is Josh (QoEAgent + transaction advisory + quality of earnings)
        _make_segment(
            base + timedelta(seconds=0),
            "I work on QoEAgent doing transaction advisory and quality of earnings work with Tom Yeh.",
            "sha_a", "speaker_A",
        ),
        _make_segment(
            base + timedelta(seconds=20),
            "Yeah, our proof of cash methodology comes from Ron Neufeld at Intrinsic.",
            "sha_a", "speaker_A",
        ),
        # speaker_B is Chris (Outside Inc + brand activations + Cowork)
        _make_segment(
            base + timedelta(seconds=40),
            "At Outside Inc we run brand activations across 180 contracts.",
            "sha_b", "speaker_B",
        ),
        _make_segment(
            base + timedelta(seconds=60),
            "FreeSkier and Cowork are part of our marketing funnel.",
            "sha_b", "speaker_B",
        ),
        # speaker_C is David (AWS + trading bot + AgentConfig + skill that creates skills)
        _make_segment(
            base + timedelta(seconds=80),
            "I have a fleet of trading bots running on AWS EC2 spot instances.",
            "sha_c", "speaker_C",
        ),
        _make_segment(
            base + timedelta(seconds=100),
            "AgentConfig has a skill that creates skills.",
            "sha_c", "speaker_C",
        ),
    ]
    unified = _build_unified(segments)
    fp = _build_fingerprint_result({
        "speaker_A": [_make_chunk("sha_a", 0.0, 4.0), _make_chunk("sha_a", 20.0, 4.0)],
        "speaker_B": [_make_chunk("sha_b", 40.0, 4.0)],
        "speaker_C": [_make_chunk("sha_c", 80.0, 4.0)],
    })
    manifest = _build_manifest_3speakers()

    result = validate_cluster_identity(fp, unified, manifest)

    assert result.review_required == [], f"Expected zero review-required, got {result.review_required}"
    by_cluster = {m.cluster_label: m for m in result.mappings}
    assert by_cluster["speaker_A"].speaker_id == "josh"
    assert by_cluster["speaker_B"].speaker_id == "chris"
    assert by_cluster["speaker_C"].speaker_id == "david"
    for m in result.mappings:
        assert m.confidence >= CONFIDENCE_REVIEW_FLOOR, (
            f"{m.cluster_label} confidence {m.confidence:.2f} < floor"
        )


# ---------------------------------------------------------------------------
# Test 2: ambiguous (low-confidence) -> review_required
# ---------------------------------------------------------------------------


def test_ambiguous_match_routes_to_review():
    """Cluster with no distinctive content -> review_required, conf < floor."""
    base = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    segments = [
        _make_segment(base, "yeah", "sha_x", "speaker_A"),
        _make_segment(base + timedelta(seconds=10), "right", "sha_x", "speaker_A"),
        _make_segment(base + timedelta(seconds=20), "uh huh", "sha_x", "speaker_A"),
    ]
    unified = _build_unified(segments)
    fp = _build_fingerprint_result({
        "speaker_A": [_make_chunk("sha_x", 0.0, 4.0)],
    })
    manifest = _build_manifest_3speakers()

    result = validate_cluster_identity(fp, unified, manifest)

    assert "speaker_A" in result.review_required
    by_cluster = {m.cluster_label: m for m in result.mappings}
    assert by_cluster["speaker_A"].speaker_id == "REVIEW"
    assert by_cluster["speaker_A"].confidence < CONFIDENCE_REVIEW_FLOOR


# ---------------------------------------------------------------------------
# Test 3: cold start (no fingerprints) -> all clusters to review
# ---------------------------------------------------------------------------


def test_cold_start_no_manifest_fingerprints_routes_all_to_review():
    """Manifest with empty fingerprints -> all clusters routed to REVIEW
    via the cold-start branch."""
    base = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    segments = [_make_segment(base, "hello world", "sha_a", "speaker_A")]
    unified = _build_unified(segments)
    fp = _build_fingerprint_result({"speaker_A": [_make_chunk("sha_a", 0.0, 4.0)]})

    # Manifest with no fingerprints at all
    bare_manifest = EpisodeManifest(
        episode_id="ep-test",
        expected_speakers=[
            ExpectedSpeaker(id="alice", display_name="Alice", role="host"),
            ExpectedSpeaker(id="bob", display_name="Bob", role="guest"),
        ],
    )

    result = validate_cluster_identity(fp, unified, bare_manifest)

    # With empty fingerprints, all candidates score 0 -> top_score=0 -> REVIEW
    assert "speaker_A" in result.review_required
    by_cluster = {m.cluster_label: m for m in result.mappings}
    assert by_cluster["speaker_A"].speaker_id == "REVIEW"


# ---------------------------------------------------------------------------
# Test 4: empty manifest -> cold-start path note + all to review
# ---------------------------------------------------------------------------


def test_empty_manifest_speakers_routes_all_to_review_with_note():
    """Manifest with NO expected_speakers -> all clusters to review,
    notes explain the cold start."""
    base = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    segments = [_make_segment(base, "AWS trading bot", "sha_a", "speaker_A")]
    unified = _build_unified(segments)
    fp = _build_fingerprint_result({"speaker_A": [_make_chunk("sha_a", 0.0, 4.0)]})

    empty_manifest = EpisodeManifest(episode_id="ep-test", expected_speakers=[])

    result = validate_cluster_identity(fp, unified, empty_manifest)

    assert result.review_required == ["speaker_A"]
    assert any("no expected_speakers" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Test 5: uniqueness conflict -> highest-conf wins, runner-up to review
# ---------------------------------------------------------------------------


def test_uniqueness_conflict_downgrades_loser_to_review():
    """When two clusters both pick the same speaker, the higher-confidence
    one keeps it; the other goes to REVIEW with method='downgraded-...'.
    """
    base = datetime(2026, 4, 26, 13, 0, 0, tzinfo=timezone.utc)
    segments = [
        # speaker_A: heavy Josh content (multiple distinctive hits)
        _make_segment(
            base, "QoEAgent transaction advisory quality of earnings Tom Yeh proof of cash",
            "sha_a", "speaker_A",
        ),
        # speaker_B: ALSO Josh content but weaker (one hit only)
        _make_segment(
            base + timedelta(seconds=20), "QoEAgent",
            "sha_b", "speaker_B",
        ),
    ]
    unified = _build_unified(segments)
    fp = _build_fingerprint_result({
        "speaker_A": [_make_chunk("sha_a", 0.0, 4.0)],
        "speaker_B": [_make_chunk("sha_b", 20.0, 4.0)],
    })
    manifest = _build_manifest_3speakers()

    result = validate_cluster_identity(fp, unified, manifest, enforce_unique=True)

    by_cluster = {m.cluster_label: m for m in result.mappings}
    # Only one of them keeps "josh"; the other is REVIEW
    josh_holders = [
        c for c, m in by_cluster.items() if m.speaker_id == "josh"
    ]
    review_holders = [
        c for c, m in by_cluster.items() if m.speaker_id == "REVIEW"
    ]
    assert len(josh_holders) == 1
    assert len(review_holders) == 1
    assert any("uniqueness-conflict" in m.method for m in result.mappings)


# ---------------------------------------------------------------------------
# Test 6: apply_identity_to_isolation renames track labels
# ---------------------------------------------------------------------------


def test_apply_identity_renames_isolation_tracks(tmp_path: Path):
    """speaker_A -> josh on every track; review_required clusters refused."""
    iso = IsolationResult(
        container="ep-test",
        tracks=[
            IsolatedTrack(
                speaker_label="speaker_A",
                source_sha256="sha_a",
                source_filename="a.wav",
                source_tier="A",
                output_path=tmp_path / "a.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
            IsolatedTrack(
                speaker_label="speaker_B",
                source_sha256="sha_b",
                source_filename="b.wav",
                source_tier="A",
                output_path=tmp_path / "b.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
        ],
    )

    # Build identity_map manually (no review_required)
    from contextpulse_pipeline.cluster_validation import (
        ClusterIdentityMap,
        ClusterMapping,
    )

    identity_map = ClusterIdentityMap(
        container="ep-test",
        mappings=[
            ClusterMapping(cluster_label="speaker_A", speaker_id="josh", confidence=0.92),
            ClusterMapping(cluster_label="speaker_B", speaker_id="chris", confidence=0.88),
        ],
        review_required=[],
    )

    new_iso = apply_identity_to_isolation(iso, identity_map)

    labels = sorted(t.speaker_label for t in new_iso.tracks)
    assert labels == ["chris", "josh"]


def test_apply_identity_raises_on_review_required():
    """When review_required is non-empty and force=False, raise."""
    iso = IsolationResult(container="ep-test", tracks=[])
    from contextpulse_pipeline.cluster_validation import (
        ClusterIdentityMap,
        ClusterMapping,
    )

    identity_map = ClusterIdentityMap(
        container="ep-test",
        mappings=[
            ClusterMapping(cluster_label="speaker_A", speaker_id="REVIEW", confidence=0.4),
        ],
        review_required=["speaker_A"],
    )

    with pytest.raises(ClusterIdentityReviewRequired):
        apply_identity_to_isolation(iso, identity_map, force=False)


def test_apply_identity_force_drops_unmapped_tracks(tmp_path: Path):
    """force=True allows partial mapping; tracks belonging to REVIEW
    clusters are dropped (not relabeled)."""
    iso = IsolationResult(
        container="ep-test",
        tracks=[
            IsolatedTrack(
                speaker_label="speaker_A",
                source_sha256="sha_a",
                source_filename="a.wav",
                source_tier="A",
                output_path=tmp_path / "a.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
            IsolatedTrack(
                speaker_label="speaker_REVIEW",
                source_sha256="sha_x",
                source_filename="x.wav",
                source_tier="A",
                output_path=tmp_path / "x.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
        ],
    )
    from contextpulse_pipeline.cluster_validation import (
        ClusterIdentityMap,
        ClusterMapping,
    )

    identity_map = ClusterIdentityMap(
        container="ep-test",
        mappings=[
            ClusterMapping(cluster_label="speaker_A", speaker_id="josh", confidence=0.92),
            ClusterMapping(cluster_label="speaker_REVIEW", speaker_id="REVIEW", confidence=0.4),
        ],
        review_required=["speaker_REVIEW"],
    )

    new_iso = apply_identity_to_isolation(iso, identity_map, force=True)

    assert len(new_iso.tracks) == 1
    assert new_iso.tracks[0].speaker_label == "josh"
    assert any("dropped 1 tracks" in s for s in new_iso.skipped)


# ---------------------------------------------------------------------------
# Test 7: defensive WARN
# ---------------------------------------------------------------------------


def test_warn_if_anonymous_labels_returns_true_for_speaker_X(tmp_path: Path, caplog):
    iso = IsolationResult(
        container="ep-test",
        tracks=[
            IsolatedTrack(
                speaker_label="speaker_A",
                source_sha256="sha_a",
                source_filename="a.wav",
                source_tier="A",
                output_path=tmp_path / "a.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
        ],
    )
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        flagged = warn_if_anonymous_labels(iso)
    assert flagged is True
    assert any("anonymous cluster labels" in r.message for r in caplog.records)


def test_warn_if_anonymous_labels_returns_false_for_real_names(tmp_path: Path):
    iso = IsolationResult(
        container="ep-test",
        tracks=[
            IsolatedTrack(
                speaker_label="josh",
                source_sha256="sha_a",
                source_filename="a.wav",
                source_tier="A",
                output_path=tmp_path / "a.wav",
                duration_sec=10.0,
                confidence=0.9,
            ),
        ],
    )
    assert warn_if_anonymous_labels(iso) is False


# ---------------------------------------------------------------------------
# Test 8: EpisodeManifest JSON round-trip
# ---------------------------------------------------------------------------


def test_episode_manifest_from_json_round_trip(tmp_path: Path):
    manifest_dict = {
        "episode_id": "ep-test",
        "expected_speakers": [
            {
                "id": "alice",
                "display_name": "Alice",
                "role": "host",
                "mic_channel": "TX00",
                "fingerprints": ["foo", "bar"],
                "fingerprint_phrases": ["my distinctive phrase"],
            },
        ],
        "voice_profile_library": "s3://bucket/voice-profiles/test/",
        "channel_to_speaker_default": {"TX00": "alice"},
    }
    p = tmp_path / "episode_manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")

    m = EpisodeManifest.from_json(p)

    assert m.episode_id == "ep-test"
    assert len(m.expected_speakers) == 1
    assert m.expected_speakers[0].id == "alice"
    assert m.expected_speakers[0].fingerprints == ["foo", "bar"]
    assert m.expected_speakers[0].fingerprint_phrases == ["my distinctive phrase"]
    assert m.channel_to_speaker_default == {"TX00": "alice"}


# ---------------------------------------------------------------------------
# Test 9: ClusterIdentityMap.to_json / confident_mappings
# ---------------------------------------------------------------------------


def test_cluster_identity_map_to_json(tmp_path: Path):
    from contextpulse_pipeline.cluster_validation import (
        ClusterIdentityMap,
        ClusterMapping,
    )

    cim = ClusterIdentityMap(
        container="ep-test",
        mappings=[
            ClusterMapping(cluster_label="speaker_A", speaker_id="josh", confidence=0.92),
            ClusterMapping(cluster_label="speaker_B", speaker_id="REVIEW", confidence=0.4),
        ],
        review_required=["speaker_B"],
    )
    p = tmp_path / "out.json"
    cim.to_json(path=p)

    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["container"] == "ep-test"
    assert data["review_required"] == ["speaker_B"]
    assert len(data["mappings"]) == 2

    # confident_mappings only includes the non-REVIEW ones
    assert cim.confident_mappings == {"speaker_A": "josh"}
