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

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from contextpulse_pipeline.unified_transcript import UnifiedTranscript

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHUNK_SEC = 2.0
DEFAULT_TARGET_CHUNK_SEC = 4.0
DEFAULT_DISTANCE_THRESHOLD = 0.5  # cosine distance in [0, 2]; ECAPA same-speaker ~0.1-0.3
DEFAULT_ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
DEFAULT_ECAPA_DIM = 192


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

    def to_json(self, *, path: Path | None = None, include_embeddings: bool = True) -> str:
        """Serialize to JSON. Embeddings are stored as plain float lists (not
        base64) to keep the output diff-able and language-agnostic. With
        include_embeddings=False, only chunk metadata + cluster assignments
        are saved (much smaller — useful for downstream stages that only
        need labels, not vectors).
        """
        chunks_payload: list[dict[str, Any]] = []
        for c in self.chunks:
            entry: dict[str, Any] = {
                "source_sha256": c.source_sha256,
                "source_relative_start_sec": c.source_relative_start_sec,
                "wall_start_utc": c.wall_start_utc.isoformat(),
                "duration_sec": c.duration_sec,
            }
            if include_embeddings and c.embedding is not None:
                entry["embedding"] = c.embedding.astype(float).tolist()
            chunks_payload.append(entry)

        clusters_payload: list[dict[str, Any]] = []
        for cl in self.clusters:
            clusters_payload.append(
                {
                    "label": cl.label,
                    "member_indices": list(cl.member_indices),
                    "centroid": cl.centroid.astype(float).tolist(),
                    "size": cl.size,
                }
            )

        payload = {
            "n_chunks": len(self.chunks),
            "n_clusters": len(self.clusters),
            "chunks": chunks_payload,
            "clusters": clusters_payload,
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_json(cls, text: str | None = None, *, path: Path | None = None) -> "FingerprintResult":
        if text is None:
            if path is None:
                raise ValueError("FingerprintResult.from_json requires text or path")
            text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        chunks: list[EmbeddingChunk] = []
        for c in data.get("chunks", []):
            emb = c.get("embedding")
            chunks.append(
                EmbeddingChunk(
                    source_sha256=c["source_sha256"],
                    source_relative_start_sec=float(c["source_relative_start_sec"]),
                    wall_start_utc=datetime.fromisoformat(c["wall_start_utc"]),
                    duration_sec=float(c["duration_sec"]),
                    embedding=np.asarray(emb, dtype=np.float32) if emb is not None else None,
                )
            )
        clusters: list[SpeakerCluster] = []
        for cl in data.get("clusters", []):
            clusters.append(
                SpeakerCluster(
                    label=cl["label"],
                    member_indices=list(cl["member_indices"]),
                    centroid=np.asarray(cl["centroid"], dtype=np.float32),
                )
            )
        return cls(chunks=chunks, clusters=clusters)


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


class ECAPAExtractor:
    """Real ECAPA-TDNN voice embedding extractor (speechbrain).

    Wraps `speechbrain.inference.speaker.EncoderClassifier` and produces a
    192-dim L2-normalized embedding per audio chunk. Same speaker → cosine
    distance ~0.1-0.3. Different speaker → cosine distance ~0.7-1.2.

    Lazy-loaded: speechbrain + torch are not imported at construction time.
    Calling ``.embed()`` for the first time triggers the load. This means
    constructing the extractor is cheap (used in tests for contract
    verification without dragging in the heavy deps), and failure to import
    is reported with an actionable error message at first use, not at module
    import time.

    Args:
        model_source: HuggingFace repo / local path passed to
            ``EncoderClassifier.from_hparams``. Default
            ``speechbrain/spkrec-ecapa-voxceleb`` (the standard ECAPA-TDNN
            model trained on VoxCeleb).
        savedir: Local cache directory for the model files. Defaults to
            ``~/.cache/speechbrain/spkrec-ecapa`` so the model is shared
            across processes/sessions.
        device: ``"cpu"``, ``"cuda"``, or ``None`` to auto-detect CUDA.
        target_sample_rate: ECAPA expects 16 kHz; if a different sample
            rate is passed at embed() time, the audio is resampled.
    """

    def __init__(
        self,
        *,
        model_source: str = DEFAULT_ECAPA_SOURCE,
        savedir: Path | None = None,
        device: str | None = None,
        target_sample_rate: int = 16000,
    ) -> None:
        self.model_source = model_source
        self.savedir = savedir or (Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa")
        self.device = device  # None → auto-detect at load time
        self.target_sample_rate = target_sample_rate
        self._model: Any | None = None  # speechbrain EncoderClassifier; loaded lazily

    def _ensure_loaded(self) -> Any:
        """Load the model on first use. Raises a helpful error if speechbrain
        or torch is not available in the environment."""
        if self._model is not None:
            return self._model
        try:
            import torch  # noqa: F401  (used below)
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as exc:  # pragma: no cover - exercised in env without speechbrain
            raise RuntimeError(
                "ECAPAExtractor requires speechbrain and torch. Install with "
                "`pip install speechbrain torch torchaudio` or run on the GPU "
                "spot worker (pipelines/phase1_5_fingerprint) which provisions "
                "them in the boot script."
            ) from exc

        device = self.device
        if device is None:
            import torch as _torch  # local alias to avoid shadowing

            device = "cuda" if _torch.cuda.is_available() else "cpu"
        logger.info("Loading ECAPA-TDNN from %s on %s", self.model_source, device)
        self.savedir.mkdir(parents=True, exist_ok=True)
        self._model = EncoderClassifier.from_hparams(
            source=self.model_source,
            savedir=str(self.savedir),
            run_opts={"device": device},
        )
        self.device = device
        return self._model

    def embed(self, audio: np.ndarray, *, sample_rate: int) -> np.ndarray:
        """Encode a mono float32 audio chunk into an L2-normalized embedding."""
        if audio.ndim != 1:
            raise ValueError(f"Expected mono 1-D audio, got shape {audio.shape}")
        if sample_rate != self.target_sample_rate:
            audio = _resample(audio, sample_rate, self.target_sample_rate)
        model = self._ensure_loaded()
        import torch  # safe — _ensure_loaded validated it imports

        wav = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
        with torch.no_grad():
            emb_tensor = model.encode_batch(wav).squeeze().detach().cpu().numpy()
        emb = np.asarray(emb_tensor, dtype=np.float32)
        norm = float(np.linalg.norm(emb))
        if norm < 1e-9:
            # Pathological zero-vector; return a unit vector along the first axis
            # so downstream cosine distance behavior stays defined.
            unit = np.zeros_like(emb)
            unit[0] = 1.0
            return unit
        return emb / norm


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resampler — adequate for speech embedding pre-processing.

    We deliberately don't pull in scipy.signal.resample_poly here since this
    helper is hot-path on the worker and the dst=16k case dominates: most
    callers pass 16 kHz audio already and bypass this entirely.
    """
    if src_sr == dst_sr:
        return audio
    ratio = dst_sr / src_sr
    n_dst = int(round(len(audio) * ratio))
    if n_dst <= 1:
        return np.zeros(0, dtype=np.float32)
    src_t = np.linspace(0, 1, num=len(audio), endpoint=False, dtype=np.float64)
    dst_t = np.linspace(0, 1, num=n_dst, endpoint=False, dtype=np.float64)
    return np.interp(dst_t, src_t, audio).astype(np.float32)


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


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def run_fingerprinting(
    unified: UnifiedTranscript,
    audio_paths: dict[str, Path],
    extractor: EmbeddingExtractor,
    *,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
    min_chunk_sec: float = DEFAULT_MIN_CHUNK_SEC,
    target_chunk_sec: float = DEFAULT_TARGET_CHUNK_SEC,
    sample_rate: int = 16000,
    max_clusters: int | None = None,
) -> FingerprintResult:
    """End-to-end Phase 1.5: plan -> embed -> cluster.

    1. ``plan_chunks_from_unified`` — one embedding chunk per long-enough segment
    2. ``extract_embeddings_for_chunks`` — run the extractor on each chunk's audio
    3. ``cluster_embeddings`` — agglomerative clustering on cosine distance

    Use ``assign_speakers_to_unified`` afterward to apply the cluster labels
    back to the unified transcript.

    The ``max_clusters`` arg is a soft post-cluster cap: if more clusters
    survived the distance threshold than ``max_clusters``, the smallest are
    merged into the nearest larger one. Useful when you know N (e.g. "this
    hike had 3 speakers — David, Chris, Josh") and the threshold over-splits.
    """
    chunks = plan_chunks_from_unified(
        unified,
        min_chunk_sec=min_chunk_sec,
        target_chunk_sec=target_chunk_sec,
    )
    if not chunks:
        logger.warning("No chunks produced — unified transcript has no long-enough segments")
        return FingerprintResult(chunks=[], clusters=[])

    chunks_with_embeddings = extract_embeddings_for_chunks(
        chunks, audio_paths, extractor, sample_rate=sample_rate
    )
    if not chunks_with_embeddings:
        logger.warning("No embeddings extracted — check audio_paths and chunk durations")
        return FingerprintResult(chunks=[], clusters=[])

    embeddings_matrix = np.stack(
        [c.embedding for c in chunks_with_embeddings if c.embedding is not None]
    )
    clusters = cluster_embeddings(embeddings_matrix, distance_threshold=distance_threshold)

    if max_clusters is not None and len(clusters) > max_clusters:
        clusters = _merge_smallest_clusters(clusters, embeddings_matrix, max_clusters)

    return FingerprintResult(chunks=chunks_with_embeddings, clusters=clusters)


def _merge_smallest_clusters(
    clusters: list[SpeakerCluster],
    embeddings_matrix: np.ndarray,
    max_clusters: int,
) -> list[SpeakerCluster]:
    """Greedy merge: while len(clusters) > max_clusters, find the smallest
    cluster and merge it into the cluster with the closest centroid.
    Re-label A..Z by size descending after merging.
    """
    working = [
        SpeakerCluster(label="", member_indices=list(c.member_indices), centroid=c.centroid.copy())
        for c in clusters
    ]
    while len(working) > max_clusters:
        smallest_idx = min(range(len(working)), key=lambda i: working[i].size)
        smallest = working[smallest_idx]
        # Find nearest other cluster by cosine distance between centroids
        nearest_idx = -1
        nearest_dist = float("inf")
        for j, other in enumerate(working):
            if j == smallest_idx:
                continue
            denom = float(np.linalg.norm(smallest.centroid) * np.linalg.norm(other.centroid))
            if denom < 1e-9:
                continue
            cos = float(np.dot(smallest.centroid, other.centroid)) / denom
            dist = 1.0 - cos
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_idx = j
        if nearest_idx < 0:
            break  # pathological — bail out rather than infinite-loop
        target = working[nearest_idx]
        merged_indices = sorted(target.member_indices + smallest.member_indices)
        merged_members = embeddings_matrix[merged_indices]
        target.member_indices = merged_indices
        target.centroid = merged_members.mean(axis=0)
        working.pop(smallest_idx)

    working.sort(key=lambda c: -c.size)
    for idx, c in enumerate(working):
        c.label = f"speaker_{chr(ord('A') + idx)}"
    return working
