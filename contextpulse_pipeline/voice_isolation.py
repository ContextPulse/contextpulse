# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 6 — Voice isolation per speaker via target speaker extraction.

Goal: for each identified speaker (output of Phase 1.5), produce a clean
per-speaker audio track from every source — even when the speaker is only
present as bleed in another speaker's mic.

Algorithm (per (speaker, source) pair):
  1. Load source's raw audio at 16 kHz mono float32
  2. Use the speaker's cluster centroid (from Phase 1.5) as the enrollment
     embedding for target speaker extraction
  3. Run a target-speaker-extraction model — WeSep (default), or any
     ``TargetSpeakerExtractor`` implementation that takes (mixed_audio,
     enrollment_embedding) and returns clean per-speaker audio
  4. Save as ``speaker_{label}_from_{source_filename}.wav``

The companion module ``cross_source_merger`` builds the unified
``speaker_X_unified.wav`` per speaker by tier-weighted greedy region
selection across the per-source extracted tracks.

Production deployment is a GPU spot variant under
``pipelines/voice_isolation/`` (mirrors ``pipelines/phase1_5_fingerprint``).
This module is the orchestrator-side library that the GPU worker imports.
"""

from __future__ import annotations

import json
import logging
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from contextpulse_pipeline.audio_sync import load_audio_window
from contextpulse_pipeline.speaker_fingerprint import FingerprintResult

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_WESEP_MODEL = "Wespeaker/wespeaker-voxceleb-resnet34"
# Note: WeSep upstream provides multiple model sources. The wrapper accepts
# any path or HuggingFace repo; we surface the default but never hardcode.


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class IsolatedTrack:
    """One (speaker, source) pair: a clean per-speaker audio file extracted
    from a single source, with the source's wall-clock anchor preserved."""

    speaker_label: str
    source_sha256: str
    source_filename: str
    source_tier: str
    output_path: Path
    duration_sec: float
    confidence: float = 0.0  # extractor-reported quality (0..1)


@dataclass
class IsolationResult:
    """End-to-end output of Stage 6: one IsolatedTrack per (speaker, source)
    pair, plus a per-speaker manifest of which sources were extracted."""

    container: str
    tracks: list[IsolatedTrack] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # reasons / context

    @property
    def speakers(self) -> list[str]:
        return sorted({t.speaker_label for t in self.tracks})

    @property
    def n_tracks(self) -> int:
        return len(self.tracks)

    def to_json(self, *, path: Path | None = None) -> str:
        payload: dict[str, Any] = {
            "container": self.container,
            "n_tracks": self.n_tracks,
            "speakers": self.speakers,
            "tracks": [
                {
                    "speaker_label": t.speaker_label,
                    "source_sha256": t.source_sha256,
                    "source_filename": t.source_filename,
                    "source_tier": t.source_tier,
                    "output_path": str(t.output_path),
                    "duration_sec": t.duration_sec,
                    "confidence": t.confidence,
                }
                for t in self.tracks
            ],
            "skipped": list(self.skipped),
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text


# ---------------------------------------------------------------------------
# TargetSpeakerExtractor protocol + stub
# ---------------------------------------------------------------------------


class TargetSpeakerExtractor(Protocol):
    """Anything that takes mixed mono audio + a speaker enrollment embedding
    and returns clean per-speaker audio of the same length."""

    def extract(
        self,
        mixed_audio: np.ndarray,
        enrollment_embedding: np.ndarray,
        *,
        sample_rate: int,
    ) -> tuple[np.ndarray, float]:
        """Returns (clean_audio_same_length, confidence_in_0_1)."""
        ...


class StubTargetSpeakerExtractor:
    """Test/scaffold extractor — returns the mixed audio unchanged with a
    deterministic stub confidence. Used for orchestrator tests without
    requiring WeSep + speechbrain installed.
    """

    def extract(
        self,
        mixed_audio: np.ndarray,
        enrollment_embedding: np.ndarray,
        *,
        sample_rate: int,
    ) -> tuple[np.ndarray, float]:
        if mixed_audio.ndim != 1:
            raise ValueError(f"Expected mono 1-D audio, got shape {mixed_audio.shape}")
        # Stub: pass-through with a deterministic confidence keyed on the
        # first byte of the enrollment so tests can assert non-zero values.
        if enrollment_embedding.size > 0:
            conf = float(0.5 + 0.4 * np.tanh(float(enrollment_embedding.flat[0])))
        else:
            conf = 0.5
        return mixed_audio.astype(np.float32, copy=True), conf


class WeSepExtractor:
    """Target speaker extractor backed by WeSep (open-source TSE toolkit).

    Lazy-loaded: WeSep + torch are not imported at construction time. Calling
    ``.extract()`` for the first time triggers the load. This means
    constructing the extractor is cheap (used in tests for contract
    verification without dragging in heavy deps), and failure to import is
    reported with an actionable error message at first use, not at module
    import time.

    Notes:
        - WeSep upstream has a moving API surface across releases. The
          wrapper isolates that churn behind a stable ``extract()`` contract.
        - On L4 GPU, RTF is ~0.3-0.5; CPU is ~3-5x slower. For 3.5 hr of
          audio across 3 speakers and 14 sources, GPU runs in ~15 hr of
          compute on a single L4.
    """

    def __init__(
        self,
        *,
        model_source: str = DEFAULT_WESEP_MODEL,
        savedir: Path | None = None,
        device: str | None = None,
        target_sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self.model_source = model_source
        self.savedir = savedir or (Path.home() / ".cache" / "wesep")
        self.device = device  # None → auto-detect at load time
        self.target_sample_rate = target_sample_rate
        self._model: Any | None = None

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import torch  # noqa: F401
            import wesep  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "WeSepExtractor requires wesep and torch. Install with "
                "`pip install wesep torch torchaudio` or run on the GPU "
                "spot worker (pipelines/voice_isolation) which provisions "
                "them in the boot script."
            ) from exc

        device = self.device
        if device is None:
            import torch as _torch

            device = "cuda" if _torch.cuda.is_available() else "cpu"
        logger.info("Loading WeSep model from %s on %s", self.model_source, device)
        self.savedir.mkdir(parents=True, exist_ok=True)

        # WeSep's high-level inference API. The exact loader name may vary by
        # release; we import lazily so callers can override the wrapper if
        # the upstream API changes without us being able to update first.
        from wesep.cli.utils import load_pretrained_model  # type: ignore[import-untyped]

        self._model = load_pretrained_model(
            self.model_source, savedir=str(self.savedir), device=device
        )
        self.device = device
        return self._model

    def extract(
        self,
        mixed_audio: np.ndarray,
        enrollment_embedding: np.ndarray,
        *,
        sample_rate: int,
    ) -> tuple[np.ndarray, float]:
        if mixed_audio.ndim != 1:
            raise ValueError(f"Expected mono 1-D audio, got shape {mixed_audio.shape}")
        if sample_rate != self.target_sample_rate:
            raise ValueError(
                f"WeSep expects {self.target_sample_rate} Hz; got {sample_rate}"
            )
        model = self._ensure_loaded()
        import torch  # safe — _ensure_loaded validated it

        wav = torch.from_numpy(mixed_audio.astype(np.float32)).unsqueeze(0)
        emb = torch.from_numpy(enrollment_embedding.astype(np.float32)).unsqueeze(0)
        with torch.no_grad():
            out = model(wav, emb)
        clean = np.asarray(out.squeeze().detach().cpu().numpy(), dtype=np.float32)
        # Confidence: ratio of output energy to input energy, clamped. WeSep
        # doesn't expose a direct quality score, so this is a heuristic.
        in_energy = float(np.sum(mixed_audio**2))
        out_energy = float(np.sum(clean**2))
        if in_energy < 1e-9:
            conf = 0.0
        else:
            conf = float(min(1.0, out_energy / in_energy))
        return clean, conf


# ---------------------------------------------------------------------------
# Audio I/O
# ---------------------------------------------------------------------------


def write_wav_mono(path: Path, audio: np.ndarray, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
    """Write a mono float32 array to a 16-bit PCM WAV file (no soundfile dep)."""
    if audio.ndim != 1:
        raise ValueError(f"Expected mono 1-D audio, got shape {audio.shape}")
    # Clip + convert to int16
    clipped = np.clip(audio, -1.0, 1.0)
    ints = (clipped * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def _rank_sources_per_cluster(
    fingerprint: FingerprintResult,
) -> dict[str, list[str]]:
    """Returns dict[cluster_label -> ordered list of source_sha256 from
    most to fewest chunks for that cluster].

    Used by ``top_k_sources_per_speaker`` to skip pairs where a speaker
    is barely or not present in a source — voice isolation on those is
    expensive and produces near-silent output.
    """
    from collections import Counter

    chunk_to_cluster_label: dict[int, str] = {}
    for c in fingerprint.clusters:
        for idx in c.member_indices:
            chunk_to_cluster_label[idx] = c.label

    per_cluster_source_counts: dict[str, Counter[str]] = {
        c.label: Counter() for c in fingerprint.clusters
    }
    for i, chunk in enumerate(fingerprint.chunks):
        label = chunk_to_cluster_label.get(i)
        if label is None:
            continue
        per_cluster_source_counts[label][chunk.source_sha256] += 1

    ranked: dict[str, list[str]] = {}
    for label, counts in per_cluster_source_counts.items():
        ranked[label] = [sha for sha, _ in counts.most_common()]
    return ranked


def extract_per_speaker_tracks_from_timeline(
    audio_paths: dict[str, Path],
    labeled_unified_transcript_path: Path,
    raw_sources_path: Path,
    output_dir: Path,
    container: str,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    fade_ms: int = 50,
    top_k_sources_per_speaker: int | None = None,
    sync_result_path: Path | None = None,
) -> IsolationResult:
    """Timeline-gated speaker isolation — local CPU, no GPU model needed.

    Uses Phase 1.5's ``unified_transcript_labeled.json`` (which has a
    ``speaker_label`` per Whisper segment) to build a soft mask per
    (speaker, source) pair. Audio is preserved during the speaker's
    segments and faded to silence elsewhere with a small crossfade at
    boundaries.

    Strictly weaker than true target-speaker extraction (it doesn't remove
    bleed *during* a speaker's segments — only outside them), but: (a)
    requires zero ML compute, (b) runs in seconds locally, (c) feeds the
    cross-source merger with the right shape, (d) has no dep on
    HuggingFace-only research repos.

    For research-grade isolation with bleed removal, swap in
    ``WeSepExtractor`` via a GPU spot pipeline. This function is the
    pragmatic default for "first end-to-end run" workflows.
    """
    import json
    from datetime import datetime

    output_dir.mkdir(parents=True, exist_ok=True)
    iso_dir = output_dir / "voice_isolation"
    iso_dir.mkdir(parents=True, exist_ok=True)

    raw_sources = json.loads(raw_sources_path.read_text(encoding="utf-8"))
    sha_to_meta = {
        s["sha256"]: {
            "filename": Path(s["file_path"]).name,
            "tier": s.get("source_tier", "?"),
            "wall_origination": s.get("bwf_origination") or s.get("filename_origination"),
            "duration_sec": float(s["duration_sec"]),
        }
        for s in raw_sources["sources"]
    }
    # If a SyncResult is provided (refined wall_start_utc from A.3b),
    # use those values as the canonical timeline anchors per source.
    if sync_result_path is not None and sync_result_path.exists():
        sync = json.loads(sync_result_path.read_text(encoding="utf-8"))
        for r in sync.get("resolved_sources", []):
            sha = r["sha256"]
            if sha in sha_to_meta and r.get("wall_start_utc"):
                sha_to_meta[sha]["wall_origination"] = r["wall_start_utc"]

    ut = json.loads(labeled_unified_transcript_path.read_text(encoding="utf-8"))

    # Group segments by (source_sha256, speaker_label) and convert wall_start_utc
    # to source-relative seconds via the source's wall_origination.
    by_pair: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for seg in ut["segments"]:
        sha = seg["source_sha256"]
        speaker = seg.get("speaker_label")
        if not speaker:
            continue
        meta = sha_to_meta.get(sha)
        if meta is None or meta["wall_origination"] is None:
            continue
        wall_origin = datetime.fromisoformat(meta["wall_origination"])
        seg_start_wall = datetime.fromisoformat(seg["wall_start_utc"])
        seg_end_wall = datetime.fromisoformat(seg["wall_end_utc"])
        rel_start = (seg_start_wall - wall_origin).total_seconds()
        rel_end = (seg_end_wall - wall_origin).total_seconds()
        if rel_end <= 0 or rel_start >= meta["duration_sec"]:
            continue
        rel_start = max(0.0, rel_start)
        rel_end = min(meta["duration_sec"], rel_end)
        by_pair.setdefault((sha, speaker), []).append((rel_start, rel_end))

    # Determine speaker chunk-counts per source for top-k filtering
    if top_k_sources_per_speaker is not None and top_k_sources_per_speaker > 0:
        from collections import Counter

        per_speaker_source_count: dict[str, Counter[str]] = {}
        for (sha, spk), segs in by_pair.items():
            per_speaker_source_count.setdefault(spk, Counter())[sha] = len(segs)
        allowed_pairs = set()
        for spk, counter in per_speaker_source_count.items():
            for sha, _ in counter.most_common(top_k_sources_per_speaker):
                allowed_pairs.add((sha, spk))
    else:
        allowed_pairs = set(by_pair.keys())

    result = IsolationResult(container=container)
    fade_samples = max(0, sample_rate * fade_ms // 1000)

    for (sha, speaker), spans in by_pair.items():
        if (sha, speaker) not in allowed_pairs:
            continue
        audio_path = audio_paths.get(sha)
        if audio_path is None or not audio_path.exists():
            result.skipped.append(f"{speaker} from {sha[:8]}: audio missing")
            continue
        meta = sha_to_meta[sha]
        try:
            audio = load_audio_window(
                audio_path, start_sec=0.0, duration_sec=meta["duration_sec"], sample_rate=sample_rate
            )
        except Exception as exc:
            result.skipped.append(f"{speaker} from {meta['filename']}: load failed ({exc})")
            continue

        masked = _apply_timeline_gate(audio, spans, sample_rate, fade_samples)
        out_path = iso_dir / f"{speaker}_from_{Path(meta['filename']).stem}.wav"
        write_wav_mono(out_path, masked, sample_rate=sample_rate)

        # Confidence: ratio of in-segment samples to total
        in_segment = sum(e - s for s, e in spans)
        confidence = float(min(1.0, in_segment / max(meta["duration_sec"], 1.0)))
        result.tracks.append(
            IsolatedTrack(
                speaker_label=speaker,
                source_sha256=sha,
                source_filename=meta["filename"],
                source_tier=meta["tier"],
                output_path=out_path,
                duration_sec=len(masked) / sample_rate,
                confidence=confidence,
            )
        )
        logger.info(
            "Gated %s from %s: %.1f sec audio, %d spans, conf=%.2f",
            speaker,
            meta["filename"],
            len(masked) / sample_rate,
            len(spans),
            confidence,
        )

    return result


def _apply_timeline_gate(
    audio: np.ndarray,
    spans: list[tuple[float, float]],
    sample_rate: int,
    fade_samples: int,
) -> np.ndarray:
    """Build a soft mask from ``spans`` and apply it. spans are (start, end)
    in seconds. Equal-power fade-in/out at every span boundary."""
    n = len(audio)
    mask = np.zeros(n, dtype=np.float32)
    for start_s, end_s in spans:
        s = max(0, int(start_s * sample_rate))
        e = min(n, int(end_s * sample_rate))
        if e <= s:
            continue
        mask[s:e] = 1.0
    if fade_samples > 0:
        # Smooth via moving-average — equal-power-ish boundary
        kernel = np.ones(fade_samples * 2 + 1, dtype=np.float32) / (fade_samples * 2 + 1)
        # Convolve in chunks to avoid O(N*K) explosion via FFT-style smoothing
        from numpy.lib.stride_tricks import sliding_window_view

        # Pad and use cumulative-sum trick for fast moving average
        kernel_w = fade_samples * 2 + 1
        padded = np.concatenate([np.zeros(kernel_w, dtype=np.float32), mask, np.zeros(kernel_w, dtype=np.float32)])
        cumsum = np.cumsum(padded)
        smoothed = (cumsum[kernel_w:] - cumsum[:-kernel_w]) / kernel_w
        mask = smoothed[: len(mask)]
        # Squelch any negative or >1 due to numerical drift
        mask = np.clip(mask, 0.0, 1.0)
    return (audio * mask).astype(np.float32)


def extract_per_speaker_tracks(
    fingerprint: FingerprintResult,
    audio_paths: dict[str, Path],
    extractor: TargetSpeakerExtractor,
    output_dir: Path,
    container: str,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    source_tiers: dict[str, str] | None = None,
    source_filenames: dict[str, str] | None = None,
    top_k_sources_per_speaker: int | None = None,
) -> IsolationResult:
    """Extract one clean per-speaker track from every (speaker, source) pair.

    For each speaker cluster from Phase 1.5 fingerprinting, and for each
    source in ``audio_paths``, run the extractor with the cluster centroid as
    the enrollment embedding. Save the output to
    ``output_dir/voice_isolation/speaker_{label}_from_{source_filename}.wav``.

    ``source_tiers`` and ``source_filenames`` are optional sha256 → label
    mappings; if not provided, the source filename is derived from the
    audio_path stem.

    ``top_k_sources_per_speaker``: if set, only run extraction on the K
    sources with the most chunks for each speaker. Dramatically cuts GPU
    time on multi-source episodes — sources where a speaker has few/zero
    chunks produce near-silent output anyway. Default ``None`` (process
    all sources, matches the design doc).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    iso_dir = output_dir / "voice_isolation"
    iso_dir.mkdir(parents=True, exist_ok=True)

    result = IsolationResult(container=container)

    if not fingerprint.clusters:
        result.skipped.append("no clusters in FingerprintResult — nothing to extract")
        return result

    ranked: dict[str, list[str]] = {}
    if top_k_sources_per_speaker is not None and top_k_sources_per_speaker > 0:
        ranked = _rank_sources_per_cluster(fingerprint)

    for cluster in fingerprint.clusters:
        if top_k_sources_per_speaker is not None and top_k_sources_per_speaker > 0:
            allowed_shas = set(ranked.get(cluster.label, [])[:top_k_sources_per_speaker])
        else:
            allowed_shas = set(audio_paths.keys())
        for sha, audio_path in audio_paths.items():
            if sha not in allowed_shas:
                continue
            if not audio_path.exists():
                result.skipped.append(f"audio missing for {sha[:8]}")
                continue
            tier = (source_tiers or {}).get(sha, "?")
            filename = (source_filenames or {}).get(sha, audio_path.stem)
            mixed = load_audio_window(
                audio_path,
                start_sec=0.0,
                duration_sec=10**9,  # full file
                sample_rate=sample_rate,
            )
            if len(mixed) < int(0.5 * sample_rate):
                result.skipped.append(
                    f"{filename}: audio shorter than 0.5s after load"
                )
                continue

            try:
                clean, conf = extractor.extract(
                    mixed, cluster.centroid, sample_rate=sample_rate
                )
            except Exception as exc:
                logger.warning(
                    "Extraction failed for speaker=%s source=%s: %s",
                    cluster.label,
                    filename,
                    exc,
                )
                result.skipped.append(
                    f"{cluster.label} from {filename}: extractor raised {exc}"
                )
                continue

            safe_filename = Path(filename).stem
            out_path = iso_dir / f"{cluster.label}_from_{safe_filename}.wav"
            write_wav_mono(out_path, clean, sample_rate=sample_rate)
            duration_sec = len(clean) / sample_rate
            result.tracks.append(
                IsolatedTrack(
                    speaker_label=cluster.label,
                    source_sha256=sha,
                    source_filename=filename,
                    source_tier=tier,
                    output_path=out_path,
                    duration_sec=duration_sec,
                    confidence=conf,
                )
            )
            logger.info(
                "Extracted %s from %s (%.1f sec, conf=%.2f) -> %s",
                cluster.label,
                filename,
                duration_sec,
                conf,
                out_path.name,
            )

    return result
