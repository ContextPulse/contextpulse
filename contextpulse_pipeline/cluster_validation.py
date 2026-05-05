# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.7 — Cluster identity validation gate.

After Phase 1.5 fingerprinting produces anonymous ``speaker_A`` /
``speaker_B`` / ... clusters, this module maps them to real speaker
identities (e.g. ``josh`` / ``chris``) using an episode manifest's
content fingerprints.

This is the gate referenced in the ``attributing-speakers`` skill rule
2026-05-03: cluster labels from ECAPA / pyannote / Sortformer are
anonymous, and downstream voice isolation cannot safely assume
``cluster_X == person_Y`` without explicit validation. Skipping this gate
on the Josh hike (ep-2026-04-26-josh-cashman) produced mastered output
where each per-speaker file contained multiple real speakers.

Validation runs in **content-only** mode by default (signals 4 + 5 + 6
from the attributing-speakers skill). Voice-embedding cross-check
(signal 1) lands in v2 once enrolled voice profiles exist.

Workflow:

    manifest = EpisodeManifest.from_json(Path("episode_manifest.json"))
    identity_map = validate_cluster_identity(
        fingerprint_result=fp_result,
        unified_transcript=unified,  # speaker_label populated by Phase 1.5
        manifest=manifest,
    )
    if identity_map.review_required:
        write_cluster_review_samples(identity_map, fp_result, audio_paths, out_dir)
        raise ClusterIdentityReviewRequired(identity_map.review_required)

    isolation = apply_identity_to_isolation(isolation, identity_map)
    merger_result = merge_all_speakers(isolation, sync, output_dir)

The output ``cluster_identity_map.json`` is the artifact downstream
consumers read to know which cluster maps to which person.
"""

from __future__ import annotations

import json
import logging
import re
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from contextpulse_pipeline.speaker_fingerprint import (
    EmbeddingChunk,
    FingerprintResult,
    SpeakerCluster,
)
from contextpulse_pipeline.unified_transcript import UnifiedTranscript
from contextpulse_pipeline.voice_isolation import IsolationResult, IsolatedTrack

logger = logging.getLogger(__name__)

# Confidence buckets per attributing-speakers skill
# >= 0.85    -> auto-accept
# 0.60-0.85  -> accept, flag for spot-check
# <  0.60    -> review_required
CONFIDENCE_AUTO_ACCEPT = 0.85
CONFIDENCE_REVIEW_FLOOR = 0.60

# Phrase scoring weighs phrases higher than single tokens (skill rule 2026-05-01)
SINGLE_FINGERPRINT_WEIGHT = 1.0
PHRASE_FINGERPRINT_WEIGHT = 2.5

# Adaptive weighting trigger thresholds (skill rule 2026-05-02)
DOMINANT_HIT_RATIO = 3.0     # top has >=3x hits over second
DOMINANT_DENSITY_FLOOR = 0.8

# Sample length for review WAVs
DEFAULT_REVIEW_SAMPLE_SEC = 10.0
DEFAULT_SAMPLE_RATE = 16000

# Cluster label pattern (speaker_A, speaker_B, ...)
CLUSTER_LABEL_RE = re.compile(r"^speaker_[A-Z]$")


# ---------------------------------------------------------------------------
# Episode manifest schema (per attributing-speakers skill)
# ---------------------------------------------------------------------------


@dataclass
class ExpectedSpeaker:
    """One expected speaker in an episode manifest.

    Mirrors the schema from the attributing-speakers skill. ``mic_channel``
    is ``None`` for speakers without a dedicated mic (e.g. David in the
    AF Josh hike). ``voice_profile_path`` is reserved for v2 (voice
    embedding signal); v1 is content-only.
    """

    id: str
    display_name: str
    role: str  # "host" | "guest" | "other"
    mic_channel: str | None = None
    voice_profile_path: str | None = None
    fingerprints: list[str] = field(default_factory=list)
    fingerprint_phrases: list[str] = field(default_factory=list)


@dataclass
class EpisodeManifest:
    """Manifest of expected speakers + fingerprint vocabulary for an episode.

    Loaded from JSON; never mutated in place by this module. To add a
    confirmed fingerprint after a human review pass, the caller should
    serialize the updated manifest back to disk explicitly.
    """

    episode_id: str
    expected_speakers: list[ExpectedSpeaker]
    voice_profile_library: str | None = None
    channel_to_speaker_default: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: Path | str) -> "EpisodeManifest":
        """Load manifest from a JSON file. Raises FileNotFoundError if absent."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        speakers = [
            ExpectedSpeaker(
                id=s["id"],
                display_name=s["display_name"],
                role=s.get("role", "other"),
                mic_channel=s.get("mic_channel"),
                voice_profile_path=s.get("voice_profile_path"),
                fingerprints=list(s.get("fingerprints", [])),
                fingerprint_phrases=list(s.get("fingerprint_phrases", [])),
            )
            for s in data.get("expected_speakers", [])
        ]
        return cls(
            episode_id=data["episode_id"],
            expected_speakers=speakers,
            voice_profile_library=data.get("voice_profile_library"),
            channel_to_speaker_default=dict(data.get("channel_to_speaker_default", {})),
        )

    def speaker_by_id(self, speaker_id: str) -> ExpectedSpeaker | None:
        for s in self.expected_speakers:
            if s.id == speaker_id:
                return s
        return None


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


@dataclass
class ClusterMapping:
    """One cluster's mapping to a real speaker identity (or REVIEW)."""

    cluster_label: str  # e.g. "speaker_A"
    speaker_id: str  # speaker_id from manifest, or "REVIEW" if low-confidence
    confidence: float  # 0.0-1.0
    method: str = "content-fingerprints"  # v2 will add "voice-embedding", "fused"
    fingerprint_hits: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)  # ordered top-3
    score_density: float = 0.0  # hits per kchar of cluster text
    n_segments: int = 0
    n_chars_aggregated: int = 0


@dataclass
class ClusterIdentityMap:
    """Output of validate_cluster_identity().

    ``review_required`` is the list of cluster labels whose confidence
    fell below ``CONFIDENCE_REVIEW_FLOOR``. The recommended workflow is
    to fail the pipeline if ``review_required`` is non-empty AND no
    ``--force`` override was passed; the ``apply_identity_to_isolation``
    helper enforces this by default.
    """

    container: str
    validation_version: str = "v1-content-only"
    mappings: list[ClusterMapping] = field(default_factory=list)
    review_required: list[str] = field(default_factory=list)
    review_audio_dir: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def confident_mappings(self) -> dict[str, str]:
        """``{cluster_label: speaker_id}`` for all clusters with conf >= floor."""
        return {
            m.cluster_label: m.speaker_id
            for m in self.mappings
            if m.speaker_id != "REVIEW"
        }

    def to_json(self, *, path: Path | None = None) -> str:
        payload: dict[str, Any] = {
            "container": self.container,
            "validation_version": self.validation_version,
            "mappings": [
                {
                    "cluster_label": m.cluster_label,
                    "speaker_id": m.speaker_id,
                    "confidence": round(m.confidence, 3),
                    "method": m.method,
                    "fingerprint_hits": list(m.fingerprint_hits),
                    "candidates": list(m.candidates),
                    "score_density": round(m.score_density, 4),
                    "n_segments": m.n_segments,
                    "n_chars_aggregated": m.n_chars_aggregated,
                }
                for m in self.mappings
            ],
            "review_required": list(self.review_required),
            "review_audio_dir": str(self.review_audio_dir)
            if self.review_audio_dir
            else None,
            "notes": list(self.notes),
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text


class ClusterIdentityReviewRequired(RuntimeError):
    """Raised when ``apply_identity_to_isolation`` is called on an identity
    map whose ``review_required`` list is non-empty and no force override
    was passed."""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Lowercase + collapse whitespace. Cheap normalization for substring matching."""
    return re.sub(r"\s+", " ", text.lower())


def _count_substring(haystack: str, needle: str) -> int:
    """Case-insensitive substring count (overlapping)."""
    if not needle:
        return 0
    needle_norm = _normalize_text(needle)
    if not needle_norm:
        return 0
    count = 0
    start = 0
    while True:
        idx = haystack.find(needle_norm, start)
        if idx < 0:
            break
        count += 1
        start = idx + 1  # overlapping count
    return count


def _aggregate_cluster_text(
    cluster_label: str, unified: UnifiedTranscript
) -> tuple[str, int]:
    """Concatenate all transcript text from segments labeled with this cluster.

    Returns ``(aggregated_text_normalized, n_segments)``. Empty string + 0
    if no segments matched (cluster present but unlabeled in transcript).
    """
    parts: list[str] = []
    n_segments = 0
    for seg in unified.segments:
        if seg.speaker_label == cluster_label:
            parts.append(seg.text)
            n_segments += 1
    aggregated = " ".join(parts)
    return _normalize_text(aggregated), n_segments


def _score_speaker_for_text(
    text_normalized: str, speaker: ExpectedSpeaker
) -> tuple[float, list[str]]:
    """Score one candidate speaker against this aggregated text.

    Returns ``(weighted_hit_count, list_of_unique_fingerprints_hit)``.
    Single-token fingerprints get weight 1.0; phrases get weight 2.5
    (skill rule 2026-05-01: phrases are more discriminative than single
    tokens).
    """
    weighted_hits = 0.0
    hits_seen: list[str] = []

    for fp in speaker.fingerprints:
        n = _count_substring(text_normalized, fp)
        if n > 0:
            weighted_hits += n * SINGLE_FINGERPRINT_WEIGHT
            hits_seen.append(fp)

    for phrase in speaker.fingerprint_phrases:
        n = _count_substring(text_normalized, phrase)
        if n > 0:
            weighted_hits += n * PHRASE_FINGERPRINT_WEIGHT
            hits_seen.append(phrase)

    return weighted_hits, hits_seen


def _confidence_from_scores(
    top_score: float,
    second_score: float,
    top_density: float,
) -> float:
    """Convert raw scores to a confidence in [0, 1].

    Heuristic: confidence rises with both the absolute density (hits per
    kchar of cluster text) and the relative margin over the runner-up.
    Tuned against the Josh hike reference to put strong content matches
    above 0.85 and ambiguous ones below 0.60.

    Adaptive boost (skill rule 2026-05-02):
      - DOMINANT trigger: top has >=3x runner-up AND density >= 0.8 ->
        boost confidence by 0.15 (clamped at 1.0).
      - UNIQUE trigger: only top scored anything -> boost by 0.10
        (caller checks; passed in via second_score == 0).
    """
    if top_score <= 0.0:
        return 0.0

    # Margin in [0, 1]: 1.0 means runner-up is zero; 0.0 means tie
    margin = (top_score - max(0.0, second_score)) / max(top_score, 1e-9)

    # Base curve: density gets us to 0.5 quickly, margin pushes higher
    # Density of 1.0 hit / 100 chars (~ 1 hit per 25 words) is "decent"
    density_component = min(1.0, top_density / 1.5)
    margin_component = max(0.0, min(1.0, margin))
    base = 0.30 * density_component + 0.45 * margin_component + 0.20

    # UNIQUE trigger: only top scored at all
    if second_score <= 0.0 and top_score > 0.0:
        base += 0.10

    # DOMINANT trigger
    if (
        second_score > 0.0
        and (top_score / max(second_score, 1e-9)) >= DOMINANT_HIT_RATIO
        and top_density >= DOMINANT_DENSITY_FLOOR
    ):
        base += 0.15

    return float(max(0.0, min(1.0, base)))


# ---------------------------------------------------------------------------
# Public API: validate_cluster_identity
# ---------------------------------------------------------------------------


def validate_cluster_identity(
    fingerprint_result: FingerprintResult,
    unified_transcript: UnifiedTranscript,
    manifest: EpisodeManifest,
    *,
    container: str | None = None,
    enforce_unique: bool = True,
) -> ClusterIdentityMap:
    """Map anonymous Phase 1.5 clusters to manifest speaker IDs.

    For each cluster, aggregate the transcript text from every
    UnifiedSegment whose ``speaker_label`` matches the cluster, score
    each candidate manifest speaker by weighted fingerprint hits, and
    assign the top scorer if confidence >= CONFIDENCE_REVIEW_FLOOR.

    Args:
        fingerprint_result: Output of Phase 1.5 ECAPA fingerprinting.
            Provides the cluster list and ``size`` per cluster (used for
            tiebreaking and review-sample selection).
        unified_transcript: Phase 1.6 output AFTER ``assign_speakers_to_unified``
            has stamped ``speaker_label`` on every segment.
        manifest: Episode manifest with expected speakers + fingerprints.
        container: Container ID (e.g. episode slug). Defaults to
            ``manifest.episode_id``.
        enforce_unique: If True (default), each speaker_id can be assigned
            to at most one cluster; if multiple clusters claim the same
            speaker, the higher-confidence one wins and the runners-up go
            to REVIEW. Set False to allow many-to-one mapping (rare —
            useful when a single person was clustered into multiple IDs
            by an over-aggressive distance threshold).

    Returns:
        ClusterIdentityMap with per-cluster mappings + review_required list.
        Never raises for "low confidence" — that's signaled via
        ``review_required``. ``apply_identity_to_isolation`` is the place
        that raises on review.
    """
    if container is None:
        container = manifest.episode_id

    mappings: list[ClusterMapping] = []
    notes: list[str] = []

    if not fingerprint_result.clusters:
        notes.append(
            "FingerprintResult has no clusters; nothing to validate. "
            "Did Phase 1.5 fingerprinting produce output?"
        )
        return ClusterIdentityMap(
            container=container,
            mappings=[],
            review_required=[],
            notes=notes,
        )

    if not manifest.expected_speakers:
        notes.append(
            "Manifest has no expected_speakers; cannot score clusters by "
            "content. All clusters routed to REVIEW. Add fingerprints + "
            "fingerprint_phrases per speaker before re-running."
        )
        for cluster in fingerprint_result.clusters:
            mappings.append(
                ClusterMapping(
                    cluster_label=cluster.label,
                    speaker_id="REVIEW",
                    confidence=0.0,
                    method="cold-start-no-manifest",
                    n_segments=0,
                    n_chars_aggregated=0,
                )
            )
        review_required = [m.cluster_label for m in mappings]
        return ClusterIdentityMap(
            container=container,
            mappings=mappings,
            review_required=review_required,
            notes=notes,
        )

    # Score every cluster against every speaker
    cluster_scores: dict[str, list[tuple[str, float, list[str], float, int, int]]] = {}
    # cluster_label -> [(speaker_id, weighted_score, hits, density, n_seg, n_char)]

    for cluster in fingerprint_result.clusters:
        agg_text, n_segments = _aggregate_cluster_text(cluster.label, unified_transcript)
        n_chars = len(agg_text)
        kchars = max(n_chars, 1) / 1000.0  # avoid div-by-zero

        per_speaker: list[tuple[str, float, list[str], float, int, int]] = []
        for speaker in manifest.expected_speakers:
            score, hits = _score_speaker_for_text(agg_text, speaker)
            density = score / kchars
            per_speaker.append(
                (speaker.id, score, hits, density, n_segments, n_chars)
            )
        per_speaker.sort(key=lambda t: -t[1])  # descending by score
        cluster_scores[cluster.label] = per_speaker

    # First pass: pick top per cluster
    raw_picks: dict[str, ClusterMapping] = {}
    for cluster_label, per_speaker in cluster_scores.items():
        if not per_speaker:
            raw_picks[cluster_label] = ClusterMapping(
                cluster_label=cluster_label,
                speaker_id="REVIEW",
                confidence=0.0,
                method="empty-manifest",
            )
            continue
        top_id, top_score, top_hits, top_density, n_seg, n_char = per_speaker[0]
        if len(per_speaker) > 1:
            second_score = per_speaker[1][1]
        else:
            second_score = 0.0

        confidence = _confidence_from_scores(top_score, second_score, top_density)
        candidates = [s_id for s_id, _, _, _, _, _ in per_speaker[:3]]

        if confidence < CONFIDENCE_REVIEW_FLOOR or top_score <= 0.0:
            assigned_id = "REVIEW"
        else:
            assigned_id = top_id

        raw_picks[cluster_label] = ClusterMapping(
            cluster_label=cluster_label,
            speaker_id=assigned_id,
            confidence=confidence,
            method="content-fingerprints",
            fingerprint_hits=list(top_hits),
            candidates=candidates,
            score_density=top_density,
            n_segments=n_seg,
            n_chars_aggregated=n_char,
        )

    # Second pass: enforce one-to-one if requested.
    # When two clusters both pick the same speaker, the higher-confidence one
    # keeps it and the others are downgraded to REVIEW with a note.
    if enforce_unique:
        by_speaker: dict[str, list[str]] = {}
        for cluster_label, mapping in raw_picks.items():
            if mapping.speaker_id == "REVIEW":
                continue
            by_speaker.setdefault(mapping.speaker_id, []).append(cluster_label)

        for speaker_id, claimants in by_speaker.items():
            if len(claimants) <= 1:
                continue
            # Sort claimants by confidence descending
            claimants_sorted = sorted(
                claimants, key=lambda cl: -raw_picks[cl].confidence
            )
            winner = claimants_sorted[0]
            for loser_label in claimants_sorted[1:]:
                loser = raw_picks[loser_label]
                notes.append(
                    f"Cluster {loser_label} also matched speaker '{speaker_id}' "
                    f"(conf={loser.confidence:.2f}); '{winner}' won "
                    f"(conf={raw_picks[winner].confidence:.2f}). "
                    f"{loser_label} downgraded to REVIEW."
                )
                raw_picks[loser_label] = ClusterMapping(
                    cluster_label=loser_label,
                    speaker_id="REVIEW",
                    confidence=loser.confidence,  # preserve actual conf
                    method="downgraded-uniqueness-conflict",
                    fingerprint_hits=loser.fingerprint_hits,
                    candidates=loser.candidates,
                    score_density=loser.score_density,
                    n_segments=loser.n_segments,
                    n_chars_aggregated=loser.n_chars_aggregated,
                )

    mappings = [raw_picks[c.label] for c in fingerprint_result.clusters]
    review_required = [m.cluster_label for m in mappings if m.speaker_id == "REVIEW"]

    return ClusterIdentityMap(
        container=container,
        mappings=mappings,
        review_required=review_required,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Listen-test sample writer
# ---------------------------------------------------------------------------


def _longest_chunk_for_cluster(
    cluster: SpeakerCluster, chunks: list[EmbeddingChunk]
) -> EmbeddingChunk | None:
    """Return the longest member chunk for this cluster, or None if empty."""
    if not cluster.member_indices:
        return None
    members = [chunks[i] for i in cluster.member_indices if 0 <= i < len(chunks)]
    if not members:
        return None
    return max(members, key=lambda c: c.duration_sec)


def write_cluster_review_samples(
    identity_map: ClusterIdentityMap,
    fingerprint_result: FingerprintResult,
    audio_paths: dict[str, Path],
    output_dir: Path,
    *,
    sample_sec: float = DEFAULT_REVIEW_SAMPLE_SEC,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    only_review_required: bool = True,
) -> dict[str, Path]:
    """Write a representative WAV sample per cluster for human listen-test.

    Selects the longest member chunk per cluster, extracts up to
    ``sample_sec`` seconds of audio from its source, and writes it to
    ``output_dir/cluster_{cluster_label}_review.wav``. Only clusters in
    ``identity_map.review_required`` are processed by default — to write
    samples for all clusters (including auto-accepted ones), pass
    ``only_review_required=False``.

    Returns ``{cluster_label: written_path}``. Skipped clusters do not
    appear in the output dict.
    """
    from contextpulse_pipeline.audio_sync import load_audio_window
    from contextpulse_pipeline.voice_isolation import write_wav_mono

    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    target_clusters = (
        set(identity_map.review_required)
        if only_review_required
        else {m.cluster_label for m in identity_map.mappings}
    )
    if not target_clusters:
        return written

    label_to_cluster = {c.label: c for c in fingerprint_result.clusters}

    for cluster_label in target_clusters:
        cluster = label_to_cluster.get(cluster_label)
        if cluster is None:
            logger.warning(
                "Cluster %s in identity_map but not in FingerprintResult — "
                "skipping review sample",
                cluster_label,
            )
            continue
        chunk = _longest_chunk_for_cluster(cluster, fingerprint_result.chunks)
        if chunk is None:
            logger.warning(
                "Cluster %s has no member chunks; cannot write review sample",
                cluster_label,
            )
            continue
        audio_path = audio_paths.get(chunk.source_sha256)
        if audio_path is None or not audio_path.exists():
            logger.warning(
                "Audio missing for source %s; cannot write review sample for %s",
                chunk.source_sha256[:8],
                cluster_label,
            )
            continue
        try:
            audio = load_audio_window(
                audio_path,
                start_sec=chunk.source_relative_start_sec,
                duration_sec=min(sample_sec, chunk.duration_sec),
                sample_rate=sample_rate,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load review window for %s from %s: %s",
                cluster_label,
                audio_path.name,
                exc,
            )
            continue
        out_path = output_dir / f"cluster_{cluster_label}_review.wav"
        write_wav_mono(out_path, audio.astype(np.float32), sample_rate=sample_rate)
        written[cluster_label] = out_path
        logger.info(
            "Wrote cluster review sample: %s (%.1fs from %s)",
            out_path.name,
            len(audio) / sample_rate,
            audio_path.name,
        )

    if written:
        identity_map.review_audio_dir = output_dir

    return written


# ---------------------------------------------------------------------------
# Apply identity map to IsolationResult
# ---------------------------------------------------------------------------


def apply_identity_to_isolation(
    isolation: IsolationResult,
    identity_map: ClusterIdentityMap,
    *,
    force: bool = False,
) -> IsolationResult:
    """Rename anonymous cluster labels on isolation tracks to real speaker IDs.

    Returns a NEW IsolationResult with each ``track.speaker_label``
    rewritten from e.g. ``speaker_A`` to ``josh`` per the identity map.
    Tracks whose cluster did not pass validation are dropped from the
    output (they would mix incorrectly into a person's unified track).

    Args:
        isolation: Output of voice_isolation.extract_per_speaker_tracks()
            (or extract_per_speaker_tracks_from_timeline).
        identity_map: Output of validate_cluster_identity().
        force: If False (default) and identity_map.review_required is
            non-empty, raises ClusterIdentityReviewRequired. Pass True to
            apply the partial map anyway (drops review_required clusters'
            tracks) — used for offline diagnostic runs only.

    Raises:
        ClusterIdentityReviewRequired: if the identity map has any
            review_required clusters and ``force`` is False.
    """
    if identity_map.review_required and not force:
        raise ClusterIdentityReviewRequired(
            f"Cluster identity validation requires human review for: "
            f"{identity_map.review_required}. "
            f"Run write_cluster_review_samples() to generate listen-test "
            f"audio, then either: (a) update the episode manifest with "
            f"more discriminative fingerprints and re-run validation, or "
            f"(b) call apply_identity_to_isolation(..., force=True) to "
            f"drop these clusters and continue with partial mapping."
        )

    rename = identity_map.confident_mappings  # cluster_label -> speaker_id
    new_tracks: list[IsolatedTrack] = []
    dropped_count = 0
    for track in isolation.tracks:
        new_label = rename.get(track.speaker_label)
        if new_label is None:
            # Cluster wasn't validated (review_required, or absent from map)
            dropped_count += 1
            continue
        new_tracks.append(replace(track, speaker_label=new_label))

    new_isolation = IsolationResult(
        container=isolation.container,
        tracks=new_tracks,
        skipped=list(isolation.skipped),
    )
    if dropped_count:
        new_isolation.skipped.append(
            f"apply_identity_to_isolation dropped {dropped_count} tracks "
            f"belonging to unmapped/REVIEW clusters."
        )
    return new_isolation


# ---------------------------------------------------------------------------
# Defensive guard for callers that bypass the gate
# ---------------------------------------------------------------------------


def warn_if_anonymous_labels(isolation: IsolationResult) -> bool:
    """Log a loud WARNING if any track still has a raw ``speaker_X`` label.

    Returns True if anonymous labels were found (caller can decide to
    fail). Used by ``cross_source_merger.merge_all_speakers`` as a
    defense-in-depth check; calling code should run cluster validation
    first.
    """
    anonymous = [
        t.speaker_label
        for t in isolation.tracks
        if CLUSTER_LABEL_RE.match(t.speaker_label)
    ]
    if anonymous:
        unique_labels = sorted(set(anonymous))
        logger.warning(
            "IsolationResult still contains anonymous cluster labels %s. "
            "These should have been renamed to manifest speaker IDs by "
            "apply_identity_to_isolation() before reaching merge_all_speakers. "
            "Output filenames will use cluster IDs, not real names — and "
            "speaker mixing is possible per the 2026-05-03 lesson. "
            "See contextpulse_pipeline.cluster_validation for the gate.",
            unique_labels,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# WAV duration probe (lightweight, used by tests + diagnostics)
# ---------------------------------------------------------------------------


def _probe_wav_duration(path: Path) -> float:
    """Return WAV duration in seconds. Used only for review-sample logging."""
    with wave.open(str(path), "rb") as r:
        return r.getnframes() / float(r.getframerate())
