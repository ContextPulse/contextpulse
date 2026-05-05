# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 6 cross-source merger — produce ``speaker_X_unified.wav`` per speaker.

After Stage 6 voice isolation, we have N (speaker, source) clean tracks for
each speaker. This module greedily merges them onto a single per-speaker
track on the unified wall-clock timeline:

  For each 100 ms hop:
    1. Find all sources that captured this region for speaker A
    2. Score each by: (tier weight) * (energy in region) * (extraction confidence)
    3. Take the winner; crossfade across transitions

Tier weights default to A=1.0 / B=0.7 / C=0.4 (DJI > phone > Telegram), per
the building-transcription-pipelines architecture doc.

Pure CPU; ~RTF 0.05 on a 4-core laptop. Designed to run locally as the last
post-processing step before mastering (Stage 7).
"""

from __future__ import annotations

import json
import logging
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from contextpulse_pipeline.sync_matcher import SyncResult
from contextpulse_pipeline.voice_isolation import IsolationResult, write_wav_mono

logger = logging.getLogger(__name__)

DEFAULT_HOP_MS = 100
DEFAULT_CROSSFADE_MS = 50
DEFAULT_TIER_WEIGHTS = {"A": 1.0, "B": 0.7, "C": 0.4}
DEFAULT_SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class MergedSpeakerTrack:
    """One merged per-speaker track."""

    speaker_label: str
    output_path: Path
    duration_sec: float
    n_regions: int  # number of hops with non-silence content
    n_source_switches: int  # how many times the winning source changed


@dataclass
class MergerResult:
    container: str
    sample_rate: int = DEFAULT_SAMPLE_RATE
    hop_ms: int = DEFAULT_HOP_MS
    crossfade_ms: int = DEFAULT_CROSSFADE_MS
    tier_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TIER_WEIGHTS))
    tracks: list[MergedSpeakerTrack] = field(default_factory=list)

    def to_json(self, *, path: Path | None = None) -> str:
        payload: dict[str, Any] = {
            "container": self.container,
            "sample_rate": self.sample_rate,
            "hop_ms": self.hop_ms,
            "crossfade_ms": self.crossfade_ms,
            "tier_weights": self.tier_weights,
            "tracks": [
                {
                    "speaker_label": t.speaker_label,
                    "output_path": str(t.output_path),
                    "duration_sec": t.duration_sec,
                    "n_regions": t.n_regions,
                    "n_source_switches": t.n_source_switches,
                }
                for t in self.tracks
            ],
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Read a mono PCM WAV into float32 [-1.0, 1.0]. Returns (audio, sample_rate)."""
    with wave.open(str(path), "rb") as r:
        if r.getnchannels() != 1:
            raise ValueError(f"{path} is not mono ({r.getnchannels()} channels)")
        sr = r.getframerate()
        n_frames = r.getnframes()
        sampwidth = r.getsampwidth()
        raw = r.readframes(n_frames)
    if sampwidth == 2:
        ints = np.frombuffer(raw, dtype=np.int16)
        audio = ints.astype(np.float32) / 32768.0
    elif sampwidth == 4:
        # 32-bit PCM (rare); treat as int32
        ints = np.frombuffer(raw, dtype=np.int32)
        audio = ints.astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"{path}: unsupported sampwidth {sampwidth}")
    return audio, sr


def _hop_energies(audio: np.ndarray, *, hop_samples: int) -> np.ndarray:
    """RMS-style energy per hop. Returns float32 array of length ceil(N/hop)."""
    n = len(audio)
    n_hops = (n + hop_samples - 1) // hop_samples
    energies = np.zeros(n_hops, dtype=np.float32)
    for i in range(n_hops):
        chunk = audio[i * hop_samples : (i + 1) * hop_samples]
        if len(chunk) == 0:
            continue
        energies[i] = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
    return energies


def _crossfade_into(
    output: np.ndarray,
    new_chunk: np.ndarray,
    write_start: int,
    fade_samples: int,
) -> None:
    """Write ``new_chunk`` into ``output`` starting at ``write_start`` with
    an equal-power crossfade against whatever is already there in the first
    ``fade_samples`` samples. Modifies output in place."""
    n_new = len(new_chunk)
    if n_new == 0:
        return
    end = min(write_start + n_new, len(output))
    n_write = end - write_start
    if n_write <= 0:
        return
    fade = min(fade_samples, n_write)
    if fade > 0:
        # equal-power fade ramps (sin/cos)
        t = np.linspace(0.0, 1.0, num=fade, endpoint=False, dtype=np.float32)
        in_ramp = np.sin(0.5 * np.pi * t)
        out_ramp = np.cos(0.5 * np.pi * t)
        output[write_start : write_start + fade] = (
            output[write_start : write_start + fade] * out_ramp
            + new_chunk[:fade] * in_ramp
        )
    output[write_start + fade : end] = new_chunk[fade:n_write]


# ---------------------------------------------------------------------------
# Per-speaker merger
# ---------------------------------------------------------------------------


def _wall_start_for_source(sync: SyncResult, sha256: str) -> datetime | None:
    for r in sync.resolved_sources:
        if r.sha256 == sha256:
            return r.wall_start_utc
    return None


def merge_speaker(
    speaker_label: str,
    isolation: IsolationResult,
    sync: SyncResult,
    output_path: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    hop_ms: int = DEFAULT_HOP_MS,
    crossfade_ms: int = DEFAULT_CROSSFADE_MS,
    tier_weights: dict[str, float] | None = None,
) -> MergedSpeakerTrack:
    """Merge all (speaker_label, source) isolated tracks into one unified track."""
    weights = tier_weights if tier_weights is not None else dict(DEFAULT_TIER_WEIGHTS)
    hop_samples = max(1, sample_rate * hop_ms // 1000)
    fade_samples = max(0, sample_rate * crossfade_ms // 1000)

    # Pull only this speaker's tracks
    tracks_for_speaker = [t for t in isolation.tracks if t.speaker_label == speaker_label]
    if not tracks_for_speaker:
        raise ValueError(f"No isolated tracks for speaker {speaker_label}")

    # Load each track + compute its wall-clock start + per-hop energy
    loaded: list[dict[str, Any]] = []
    timeline_min: datetime | None = None
    timeline_max: datetime | None = None
    for t in tracks_for_speaker:
        wall_start = _wall_start_for_source(sync, t.source_sha256)
        if wall_start is None:
            logger.warning(
                "No SyncResult entry for source %s — skipping in merge",
                t.source_sha256[:8],
            )
            continue
        audio, sr = _read_wav_mono(t.output_path)
        if sr != sample_rate:
            logger.warning(
                "Sample-rate mismatch for %s (%d != %d) — skipping",
                t.output_path.name,
                sr,
                sample_rate,
            )
            continue
        wall_end = wall_start + timedelta(seconds=len(audio) / sample_rate)
        loaded.append(
            {
                "track": t,
                "audio": audio,
                "wall_start": wall_start,
                "wall_end": wall_end,
                "energies": _hop_energies(audio, hop_samples=hop_samples),
                "weight": weights.get(t.source_tier, 0.5) * float(max(0.0, t.confidence)),
            }
        )
        timeline_min = wall_start if timeline_min is None else min(timeline_min, wall_start)
        timeline_max = wall_end if timeline_max is None else max(timeline_max, wall_end)

    if not loaded or timeline_min is None or timeline_max is None:
        raise ValueError(f"No usable tracks for speaker {speaker_label}")

    duration_sec = (timeline_max - timeline_min).total_seconds()
    total_samples = int(duration_sec * sample_rate) + 1
    output = np.zeros(total_samples, dtype=np.float32)
    n_hops = (total_samples + hop_samples - 1) // hop_samples

    n_regions = 0
    n_source_switches = 0
    last_winner_sha: str | None = None

    for hop_idx in range(n_hops):
        hop_wall_start = timeline_min + timedelta(seconds=hop_idx * hop_ms / 1000)

        # Find the candidate with the best score at this hop
        best_score = 0.0
        best = None
        for entry in loaded:
            if hop_wall_start < entry["wall_start"] or hop_wall_start >= entry["wall_end"]:
                continue
            relative_sec = (hop_wall_start - entry["wall_start"]).total_seconds()
            local_hop_idx = int(relative_sec * 1000 / hop_ms)
            energies = entry["energies"]
            if local_hop_idx < 0 or local_hop_idx >= len(energies):
                continue
            energy = float(energies[local_hop_idx])
            score = entry["weight"] * energy
            if score > best_score:
                best_score = score
                best = (entry, local_hop_idx)

        if best is None or best_score < 1e-6:
            continue
        entry, local_hop_idx = best
        winner_sha = entry["track"].source_sha256
        if last_winner_sha is not None and winner_sha != last_winner_sha:
            n_source_switches += 1
        last_winner_sha = winner_sha

        # Copy this hop's samples (with crossfade if we just switched sources)
        write_start = hop_idx * hop_samples
        chunk_start = local_hop_idx * hop_samples
        chunk = entry["audio"][chunk_start : chunk_start + hop_samples]
        if len(chunk) == 0:
            continue
        _crossfade_into(output, chunk, write_start, fade_samples)
        n_regions += 1

    write_wav_mono(output_path, output, sample_rate=sample_rate)
    return MergedSpeakerTrack(
        speaker_label=speaker_label,
        output_path=output_path,
        duration_sec=duration_sec,
        n_regions=n_regions,
        n_source_switches=n_source_switches,
    )


def merge_all_speakers(
    isolation: IsolationResult,
    sync: SyncResult,
    output_dir: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    hop_ms: int = DEFAULT_HOP_MS,
    crossfade_ms: int = DEFAULT_CROSSFADE_MS,
    tier_weights: dict[str, float] | None = None,
) -> MergerResult:
    """Run merge_speaker for every speaker in the IsolationResult.

    Defense-in-depth: if any track still has an anonymous ``speaker_X``
    label, log a loud WARNING. Cluster validation (see
    ``contextpulse_pipeline.cluster_validation``) should have renamed
    these to manifest speaker IDs before reaching this function. Skipping
    the validation gate produced the speaker-mixing bug on the Josh hike
    (skill rule 2026-05-03).
    """
    # Defense-in-depth — local import to avoid a hard dep cycle in tests
    # that exercise the merger without the validation module available.
    try:
        from contextpulse_pipeline.cluster_validation import warn_if_anonymous_labels

        warn_if_anonymous_labels(isolation)
    except ImportError:
        # cluster_validation should always be importable in production;
        # missing it is a packaging error, not a runtime failure.
        logger.debug("cluster_validation not importable; skipping anon-label check")

    output_dir.mkdir(parents=True, exist_ok=True)
    weights = tier_weights if tier_weights is not None else dict(DEFAULT_TIER_WEIGHTS)
    result = MergerResult(
        container=isolation.container,
        sample_rate=sample_rate,
        hop_ms=hop_ms,
        crossfade_ms=crossfade_ms,
        tier_weights=weights,
    )
    for speaker_label in isolation.speakers:
        out_path = output_dir / f"{speaker_label}_unified.wav"
        try:
            track = merge_speaker(
                speaker_label,
                isolation,
                sync,
                out_path,
                sample_rate=sample_rate,
                hop_ms=hop_ms,
                crossfade_ms=crossfade_ms,
                tier_weights=weights,
            )
            result.tracks.append(track)
            logger.info(
                "Merged %s: %.1f sec, %d regions, %d source switches -> %s",
                speaker_label,
                track.duration_sec,
                track.n_regions,
                track.n_source_switches,
                out_path.name,
            )
        except ValueError as exc:
            logger.warning("Skipping %s: %s", speaker_label, exc)
    return result
