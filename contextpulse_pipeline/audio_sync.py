# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""A.3b — Audio cross-correlation refinement of pair offsets.

The coarse sync_matcher (A.3) computes pair offsets from Whisper segment
timestamps. Whisper segment.start has 0.5-1.5s jitter, so coarse pair
std_dev is typically 0.3-1.0s — too loose for per-speaker mixing.

This module refines those offsets by cross-correlating the actual audio
around each shared phrase. Cross-correlation peak gives sub-sample precision
(~0.1ms at 16 kHz), and aggregating across many anchors produces sub-ms
median offsets.

Algorithm per anchor pair (phrase appearing in both A and B):
  1. Extract a window of audio from A around its segment.start
  2. Extract a window from B around its segment.start
  3. Both windows should contain the same phrase (modulo Whisper jitter)
  4. Cross-correlate -> peak at lag k; refined wall-offset = (t_a - t_b) + k_sec
  5. Aggregate refined wall-offsets across anchors via median + std_dev
"""

from __future__ import annotations

import logging
import statistics
import subprocess
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy.signal import correlate

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection
from contextpulse_pipeline.sync_matcher import (
    AnchorPair,
    PairOffset,
    ResolvedSource,
    SyncResult,
    _build_offset_map,
    _propagate_from_anchor,
    common_ngrams,
    extract_ngrams,
    find_pair_anchors,
)

logger = logging.getLogger(__name__)

# Tunables
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_WINDOW_SEC = 6.0
DEFAULT_MAX_LAG_SEC = 3.0  # Whisper jitter ceiling
DEFAULT_MIN_CONFIDENCE = 0.1  # normalized correlation peak (0..1)
DEFAULT_MAX_ANCHORS_PER_PAIR = 30


# ---------------------------------------------------------------------------
# Audio extraction (ffmpeg subprocess)
# ---------------------------------------------------------------------------


def load_audio_window(
    path: Path,
    start_sec: float,
    duration_sec: float,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> np.ndarray:
    """Extract a mono float32 PCM window from an audio file via ffmpeg.

    Negative start_sec is clipped to 0. Returned array length may be shorter
    than requested if the window extends past EOF (or starts before BOF).
    """
    actual_start = max(0.0, start_sec)
    if start_sec < 0:
        # Caller asked for [start, start+dur]; we clip the front, return shorter
        duration_sec = max(0.0, duration_sec - (actual_start - start_sec))
    if duration_sec <= 0:
        return np.zeros(0, dtype=np.float32)

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{actual_start:.6f}",
        "-i",
        str(path),
        "-t",
        f"{duration_sec:.6f}",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning("ffmpeg failed for %s @ %.2fs: %s", path, start_sec, e)
        return np.zeros(0, dtype=np.float32)

    return np.frombuffer(result.stdout, dtype=np.float32)


# ---------------------------------------------------------------------------
# Cross-correlation
# ---------------------------------------------------------------------------


def _parabolic_interpolate(values: np.ndarray, peak_idx: int) -> float:
    """Sub-sample peak refinement via parabolic fit through 3 points.

    Returns fractional offset from peak_idx (in samples). Useful only for
    sharp peaks (impulses, transients). For smooth peaks the gain is small.
    """
    if peak_idx <= 0 or peak_idx >= len(values) - 1:
        return 0.0
    y_minus = float(values[peak_idx - 1])
    y_zero = float(values[peak_idx])
    y_plus = float(values[peak_idx + 1])
    denom = y_minus - 2 * y_zero + y_plus
    if abs(denom) < 1e-12:
        return 0.0
    return 0.5 * (y_minus - y_plus) / denom


def cross_correlate_lag(
    a: np.ndarray,
    b: np.ndarray,
    *,
    max_lag_sec: float = DEFAULT_MAX_LAG_SEC,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[float, float]:
    """Compute the lag (in seconds) at which a and b best align, plus confidence.

    Follows scipy.signal.correlate's natural convention:
        peak at lag k  <=>  a[t] ~= b[t - k]
    So if b is delayed by D samples relative to a (b[t] = a[t - D]),
    the returned lag is -D/sample_rate (NEGATIVE). If b leads a by D samples
    (b[t] = a[t + D]), the returned lag is +D/sample_rate (POSITIVE).

    Confidence = normalized correlation peak amplitude. Because both inputs
    are unit-normalized, this is in [0, 1]: 1.0 for identical signals,
    typically 0.3-0.8 for real speech matches, < 0.05 for uncorrelated noise.

    Returns (lag_sec, confidence).
    """
    if len(a) == 0 or len(b) == 0:
        return 0.0, 0.0

    # Normalize (zero-mean, unit-energy) so confidence is scale-invariant
    a_norm = a - a.mean()
    b_norm = b - b.mean()
    a_energy = float(np.linalg.norm(a_norm))
    b_energy = float(np.linalg.norm(b_norm))
    if a_energy < 1e-9 or b_energy < 1e-9:
        return 0.0, 0.0
    a_norm = a_norm / a_energy
    b_norm = b_norm / b_energy

    corr = correlate(a_norm, b_norm, mode="full", method="fft")

    # scipy convention: corr[i] corresponds to lag = i - (len(b) - 1)
    zero_lag_idx = len(b_norm) - 1
    max_lag_samples = int(max_lag_sec * sample_rate)
    lo = max(0, zero_lag_idx - max_lag_samples)
    hi = min(len(corr), zero_lag_idx + max_lag_samples + 1)
    sub = corr[lo:hi]

    peak_offset_in_sub = int(np.argmax(np.abs(sub)))
    peak_lag_samples = peak_offset_in_sub + lo - zero_lag_idx
    # Sub-sample refinement
    frac = _parabolic_interpolate(np.abs(sub), peak_offset_in_sub)
    peak_lag_samples_refined = peak_lag_samples + frac

    confidence = float(np.abs(sub[peak_offset_in_sub]))
    lag_sec = peak_lag_samples_refined / sample_rate
    return lag_sec, confidence


# ---------------------------------------------------------------------------
# Pair refinement
# ---------------------------------------------------------------------------


def _select_anchors(
    anchors: list[AnchorPair],
    max_count: int,
) -> list[AnchorPair]:
    """Sample anchors uniformly by start_a_sec to avoid clustering."""
    if len(anchors) <= max_count:
        return anchors
    sorted_anchors = sorted(anchors, key=lambda p: p.start_a_sec)
    step = len(sorted_anchors) / max_count
    return [sorted_anchors[int(i * step)] for i in range(max_count)]


def refine_pair_offset(
    audio_a_path: Path,
    audio_b_path: Path,
    anchors: list[AnchorPair],
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_lag_sec: float = DEFAULT_MAX_LAG_SEC,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_anchors: int = DEFAULT_MAX_ANCHORS_PER_PAIR,
) -> PairOffset | None:
    """Refine a coarse pair offset using audio cross-correlation.

    For each AnchorPair, extract a window from each file centered on the
    Whisper segment.start, cross-correlate, and accept the result if the
    peak/baseline confidence clears the threshold.

    Returns a refined PairOffset whose `offset_sec` follows the same
    convention as compute_pair_offset (B.t - A.t on transcript clocks),
    or None if too few anchors clear the confidence floor.
    """
    if not anchors:
        return None
    selected = _select_anchors(anchors, max_anchors)

    refined_offsets: list[float] = []  # signed transcript-clock delta per anchor
    confidences: list[float] = []

    for anchor in selected:
        # Window centered at the anchor's segment.start in each file
        a_start = anchor.start_a_sec - window_sec / 2
        b_start = anchor.start_b_sec - window_sec / 2
        a_window = load_audio_window(audio_a_path, a_start, window_sec, sample_rate)
        b_window = load_audio_window(audio_b_path, b_start, window_sec, sample_rate)
        if len(a_window) < sample_rate or len(b_window) < sample_rate:
            # Less than 1 second of audio — too short for reliable correlation
            continue

        # Truncate to common length
        n = min(len(a_window), len(b_window))
        lag_sec, conf = cross_correlate_lag(
            a_window[:n],
            b_window[:n],
            max_lag_sec=max_lag_sec,
            sample_rate=sample_rate,
        )
        if conf < min_confidence:
            continue

        # Derivation:
        #   Audio sample i in A's window corresponds to wall (A.wall_start + a_start + i/sr).
        #   Audio sample j in B's window corresponds to wall (B.wall_start + b_start + j/sr).
        #   Cross-correlation peak at lag k means a[i] matches b[i - k], i.e. for a
        #   given i in A, the matching sample in B is at j = i - k.  Equating wall times:
        #     A.wall_start + a_start + i/sr = B.wall_start + b_start + (i - k)/sr
        #     B.wall_start - A.wall_start = a_start - b_start + k/sr
        #   In transcript-clock terms, offset_sec = B.t - A.t = -(B.wall_start - A.wall_start)
        #   for the SAME wall event.  So:
        #     refined_offset_sec_per_anchor = -(a_start - b_start + lag_sec)
        #                                   = (b_start - a_start) - lag_sec
        # Note: window centers were anchor.start_a_sec and anchor.start_b_sec, so
        # b_start - a_start = anchor.start_b_sec - anchor.start_a_sec = anchor.delta_sec.
        refined_offsets.append(anchor.delta_sec - lag_sec)
        confidences.append(conf)

    if len(refined_offsets) < 3:
        return None

    median_offset = statistics.median(refined_offsets)
    std_dev = statistics.pstdev(refined_offsets) if len(refined_offsets) > 1 else 0.0

    return PairOffset(
        source_a=anchors[0].source_a,
        source_b=anchors[0].source_b,
        offset_sec=median_offset,
        n_anchors=len(refined_offsets),
        std_dev_sec=std_dev,
    )


# ---------------------------------------------------------------------------
# End-to-end refinement of a SyncResult
# ---------------------------------------------------------------------------


def _build_sha_map(coll: RawSourceCollection) -> dict[str, RawSource]:
    return {s.sha256: s for s in coll.sources}


def refine_sync_result(
    coarse: SyncResult,
    coll: RawSourceCollection,
    transcripts_dir: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_lag_sec: float = DEFAULT_MAX_LAG_SEC,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_anchors_per_pair: int = DEFAULT_MAX_ANCHORS_PER_PAIR,
    n_min: int = 5,
    n_max: int = 7,
    idf_max_fraction: float = 0.5,
    agreement_eps_sec: float = 2.0,
) -> SyncResult:
    """Refine every pair_offset in `coarse` using audio cross-correlation;
    rebuild the timeline graph and return a new SyncResult.

    Sources that were resolved via BWF (provenance="bwf") keep their UTC
    unchanged. Only pair-propagated sources benefit from the refinement.
    """
    if not coarse.pair_offsets:
        return coarse

    sha_map = _build_sha_map(coll)

    # Rebuild ngram indexes (cheap; CPU-bound only on the first transcript load)
    indexes: dict[str, dict[str, list]] = {}
    for rs in coll.sources:
        json_path = transcripts_dir / f"{rs.sha256[:16]}.json"
        if not json_path.exists():
            continue
        import json as _json

        transcript = _json.loads(json_path.read_text(encoding="utf-8"))
        transcript["source_sha256"] = rs.sha256
        indexes[rs.sha256] = extract_ngrams(transcript, n_min=n_min, n_max=n_max)
    excluded = common_ngrams(indexes.values(), max_fraction=idf_max_fraction)

    # Refine each pair
    refined_pairs: list[PairOffset] = []
    for coarse_pair in coarse.pair_offsets:
        sha_a = coarse_pair.source_a
        sha_b = coarse_pair.source_b
        if sha_a not in indexes or sha_b not in indexes:
            refined_pairs.append(coarse_pair)
            continue
        rs_a = sha_map.get(sha_a)
        rs_b = sha_map.get(sha_b)
        if rs_a is None or rs_b is None:
            refined_pairs.append(coarse_pair)
            continue
        path_a = Path(rs_a.file_path)
        path_b = Path(rs_b.file_path)
        if not path_a.exists() or not path_b.exists():
            logger.warning(
                "Audio file missing; keeping coarse offset for %s↔%s",
                sha_a[:8],
                sha_b[:8],
            )
            refined_pairs.append(coarse_pair)
            continue

        # Re-derive the agreeing anchor list for this pair
        all_anchors = find_pair_anchors(indexes[sha_a], indexes[sha_b], excluded_ngrams=excluded)
        # Filter to the agreeing subset (within eps of coarse offset)
        agreeing = [
            ap
            for ap in all_anchors
            if abs(ap.delta_sec - coarse_pair.offset_sec) <= agreement_eps_sec
        ]
        if not agreeing:
            refined_pairs.append(coarse_pair)
            continue

        refined = refine_pair_offset(
            audio_a_path=path_a,
            audio_b_path=path_b,
            anchors=agreeing,
            sample_rate=sample_rate,
            window_sec=window_sec,
            max_lag_sec=max_lag_sec,
            min_confidence=min_confidence,
            max_anchors=max_anchors_per_pair,
        )
        if refined is None:
            logger.warning(
                "Audio refinement failed for %s↔%s; keeping coarse offset",
                sha_a[:8],
                sha_b[:8],
            )
            refined_pairs.append(coarse_pair)
        else:
            logger.info(
                "Refined %s↔%s: %.4fs → %.4fs (std %.4fs → %.4fs, n=%d)",
                sha_a[:8],
                sha_b[:8],
                coarse_pair.offset_sec,
                refined.offset_sec,
                coarse_pair.std_dev_sec,
                refined.std_dev_sec,
                refined.n_anchors,
            )
            refined_pairs.append(refined)

    # Rebuild graph + propagate from same anchor
    graph = _build_offset_map(refined_pairs)
    propagated = _propagate_from_anchor(graph, coarse.anchor_source_sha256)
    anchor_utc = coarse.anchor_origination_utc

    refined_sources: list[ResolvedSource] = []
    unreachable: list[str] = []
    for rs in coll.sources:
        if rs.bwf_origination is not None:
            offset_sec = (rs.bwf_origination - anchor_utc).total_seconds()
            refined_sources.append(
                ResolvedSource(
                    sha256=rs.sha256,
                    wall_start_utc=rs.bwf_origination,
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=offset_sec,
                )
            )
        elif rs.sha256 in propagated:
            offset_sec = propagated[rs.sha256]
            wall_start = anchor_utc + timedelta(seconds=offset_sec)
            anchor_count = sum(
                p.n_anchors
                for p in refined_pairs
                if p.source_a == rs.sha256 or p.source_b == rs.sha256
            )
            refined_sources.append(
                ResolvedSource(
                    sha256=rs.sha256,
                    wall_start_utc=wall_start,
                    provenance="matched",
                    anchor_count=anchor_count,
                    offset_from_anchor_sec=offset_sec,
                )
            )
        else:
            unreachable.append(rs.sha256)

    return SyncResult(
        container=coarse.container,
        anchor_source_sha256=coarse.anchor_source_sha256,
        anchor_origination_utc=anchor_utc,
        resolved_sources=refined_sources,
        unreachable_sources=unreachable,
        pair_offsets=refined_pairs,
    )
