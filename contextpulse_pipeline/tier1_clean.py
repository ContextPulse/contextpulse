# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tier 1 — Classical signal-processing cleanup, per source.

Per the ``enhancing-audio-for-podcasts`` skill, Tier 1 is the always-first
baseline: high-pass filter + broadband denoise + per-channel loudness
normalization. It runs locally via ffmpeg, costs nothing, and produces
audio ready for downstream merging/mastering.

Chain (matches skill section 1.1-1.3):
    1. high-pass at 80 Hz (kills sub-80 Hz rumble, footstep thump)
    2. afftdn FFT-based denoise (-25 dB noise floor — viable arnndn substitute,
       preferred when arnndn model files are missing on Windows ffmpeg builds)
    3. loudnorm to -23 LUFS (per-channel target; the master target -16 LUFS
       is applied later in Stage 7 mastering)

Anti-pattern this module avoids: aggressive ML denoise BEFORE diarization
(skill anti-pattern #4 — corrupts speaker embeddings). Diarization
(Phase 1.5) already ran on the raw audio, so this Tier 1 pass after
diarization is the right place for cleanup.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HIGHPASS_HZ = 80
DEFAULT_NOISE_FLOOR_DB = -25
DEFAULT_TARGET_LUFS = -23.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
DEFAULT_LRA = 11.0
DEFAULT_SAMPLE_RATE = 16000


@dataclass
class CleanedSource:
    sha256: str
    input_path: Path
    output_path: Path
    measured_input_lufs: float | None = None
    measured_output_lufs: float | None = None
    chain_applied: list[str] = field(default_factory=list)


@dataclass
class Tier1Result:
    container: str
    target_lufs: float = DEFAULT_TARGET_LUFS
    sample_rate: int = DEFAULT_SAMPLE_RATE
    cleaned: list[CleanedSource] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_json(self, *, path: Path | None = None) -> str:
        payload: dict[str, Any] = {
            "container": self.container,
            "target_lufs": self.target_lufs,
            "sample_rate": self.sample_rate,
            "cleaned": [
                {
                    "sha256": c.sha256,
                    "input_path": str(c.input_path),
                    "output_path": str(c.output_path),
                    "measured_input_lufs": c.measured_input_lufs,
                    "measured_output_lufs": c.measured_output_lufs,
                    "chain_applied": list(c.chain_applied),
                }
                for c in self.cleaned
            ],
            "skipped": list(self.skipped),
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text

    def cleaned_paths(self) -> dict[str, Path]:
        """Returns sha256 -> cleaned WAV path. Convenience for downstream stages."""
        return {c.sha256: c.output_path for c in self.cleaned}


_LUFS_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.S)


def measure_lufs(input_path: Path) -> float | None:
    """Run ffmpeg ebur128 to measure integrated LUFS. Returns None on failure."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-i",
        str(input_path),
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=300)
    except subprocess.CalledProcessError:
        return None
    stderr = (result.stderr or b"").decode(errors="replace")
    matches = _LUFS_RE.findall(stderr)
    if not matches:
        m = re.search(r"Integrated loudness:.*?I:\s+(-?\d+(?:\.\d+)?)\s*LUFS", stderr, re.S)
        if m:
            return float(m.group(1))
        return None
    return float(matches[-1])


def _parse_loudnorm_json(stderr: str) -> dict[str, str] | None:
    m = _LOUDNORM_JSON_RE.search(stderr)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def clean_source(
    input_path: Path,
    output_path: Path,
    sha256: str,
    *,
    highpass_hz: int = DEFAULT_HIGHPASS_HZ,
    noise_floor_db: int = DEFAULT_NOISE_FLOOR_DB,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
    lra: float = DEFAULT_LRA,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    measure_input: bool = False,
) -> CleanedSource:
    """Apply Tier 1 chain (HPF + afftdn + two-pass loudnorm) to a single source.

    Output is mono int16 WAV at the given sample rate, normalized to
    ``target_lufs`` (-23 LUFS by default).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chain_applied: list[str] = []
    measured_input = measure_lufs(input_path) if measure_input else None

    chain = f"highpass=f={highpass_hz},afftdn=nf={noise_floor_db}"
    chain_applied.extend([f"highpass(f={highpass_hz})", f"afftdn(nf={noise_floor_db})"])

    # Two-pass loudnorm: pass 1 measures, pass 2 applies with measured values.
    chain_tmp = output_path.with_suffix(".chain.wav")

    # Pass 0: HPF + afftdn → temp
    cmd_chain = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-loglevel",
        "info",
        "-i",
        str(input_path),
        "-af",
        chain,
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(chain_tmp),
    ]
    subprocess.run(cmd_chain, capture_output=True, check=True, timeout=600)

    # Pass 1: measure loudnorm
    cmd_measure = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-loglevel",
        "info",
        "-i",
        str(chain_tmp),
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd_measure, capture_output=True, timeout=600)
    stderr = (proc.stderr or b"").decode(errors="replace")
    measured = _parse_loudnorm_json(stderr)

    if measured is not None:
        norm_filter = (
            f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}:"
            f"measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:"
            "linear=true:print_format=summary"
        )
        chain_applied.append(f"loudnorm-2pass(I={target_lufs})")
    else:
        # Fallback: single-pass
        norm_filter = f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}"
        chain_applied.append(f"loudnorm-1pass(I={target_lufs})")

    cmd_norm = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-loglevel",
        "info",
        "-i",
        str(chain_tmp),
        "-af",
        norm_filter,
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    subprocess.run(cmd_norm, capture_output=True, check=True, timeout=600)

    # Clean up temp file
    if chain_tmp.exists():
        try:
            chain_tmp.unlink()
        except Exception:
            pass

    measured_output = measure_lufs(output_path) if measure_input else None
    return CleanedSource(
        sha256=sha256,
        input_path=input_path,
        output_path=output_path,
        measured_input_lufs=measured_input,
        measured_output_lufs=measured_output,
        chain_applied=chain_applied,
    )


def clean_collection(
    audio_paths: dict[str, Path],
    output_dir: Path,
    container: str,
    *,
    highpass_hz: int = DEFAULT_HIGHPASS_HZ,
    noise_floor_db: int = DEFAULT_NOISE_FLOOR_DB,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    measure_lufs_each: bool = False,
) -> Tier1Result:
    """Apply Tier 1 chain to every source in ``audio_paths``.

    Returns a Tier1Result with one CleanedSource per successful conversion.
    Failures are recorded in ``skipped`` but do not raise.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = Tier1Result(
        container=container,
        target_lufs=target_lufs,
        sample_rate=sample_rate,
    )
    for sha, in_path in audio_paths.items():
        if not in_path.exists():
            result.skipped.append(f"{sha[:8]}: input missing at {in_path}")
            continue
        out_path = output_dir / f"{Path(in_path).stem}_tier1.wav"
        try:
            cleaned = clean_source(
                in_path,
                out_path,
                sha256=sha,
                highpass_hz=highpass_hz,
                noise_floor_db=noise_floor_db,
                target_lufs=target_lufs,
                sample_rate=sample_rate,
                measure_input=measure_lufs_each,
            )
            result.cleaned.append(cleaned)
            logger.info(
                "Tier1 %s -> %s (in_LUFS=%s out_LUFS=%s)",
                in_path.name,
                out_path.name,
                f"{cleaned.measured_input_lufs:.1f}" if cleaned.measured_input_lufs else "?",
                f"{cleaned.measured_output_lufs:.1f}" if cleaned.measured_output_lufs else "?",
            )
        except subprocess.CalledProcessError as exc:
            result.skipped.append(f"{Path(in_path).name}: ffmpeg rc={exc.returncode}")
        except subprocess.TimeoutExpired:
            result.skipped.append(f"{Path(in_path).name}: ffmpeg timeout")
    return result
