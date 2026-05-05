# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 7 — Per-speaker mastering pipeline.

Takes the per-speaker unified tracks from Stage 6 (cross_source_merger) and
applies a standard podcast mastering chain so each track is ready to drag
into a DAW (Reaper, Logic, Audition) and mix without further cleanup.

Mastering chain (in order):
    1. High-pass at 80 Hz (rumble, wind, breath thumps)
    2. Optional de-noise (DeepFilterNet if installed; otherwise ffmpeg afftdn)
    3. De-ess (gentle 6-9 kHz cut, sibilance taming)
    4. Compressor (ratio 3:1, soft knee)
    5. Light voice EQ (gentle 2-4 kHz boost, slight 200-400 Hz cut)
    6. True-peak limiter at -1 dBTP
    7. Two-pass loudnorm to -16 LUFS (streaming standard)

Most steps are pure ffmpeg filters. DeepFilterNet is the only optional dep —
if it's not installed, the chain falls back to ffmpeg's `afftdn` denoiser
(less effective but always available).

Pure CPU. ~RTF 0.5 end-to-end on a 4-core CPU. For 3.5 hr × 3 speakers,
total wall time is ~5-10 minutes locally.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TARGET_LUFS = -16.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
DEFAULT_LRA = 11.0  # default loudness range
DEFAULT_HIGHPASS_HZ = 80
DEFAULT_SAMPLE_RATE = 48000  # output mastering rate (DAW-friendly)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class MasteredTrack:
    """One mastered per-speaker output."""

    speaker_label: str
    input_path: Path
    output_path: Path
    measured_input_lufs: float | None = None
    measured_output_lufs: float | None = None
    target_lufs: float = DEFAULT_TARGET_LUFS
    chain_applied: list[str] = field(default_factory=list)


@dataclass
class MasteringResult:
    container: str
    target_lufs: float = DEFAULT_TARGET_LUFS
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP
    lra: float = DEFAULT_LRA
    sample_rate: int = DEFAULT_SAMPLE_RATE
    tracks: list[MasteredTrack] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_json(self, *, path: Path | None = None) -> str:
        payload: dict[str, Any] = {
            "container": self.container,
            "target_lufs": self.target_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "lra": self.lra,
            "sample_rate": self.sample_rate,
            "tracks": [
                {
                    "speaker_label": t.speaker_label,
                    "input_path": str(t.input_path),
                    "output_path": str(t.output_path),
                    "measured_input_lufs": t.measured_input_lufs,
                    "measured_output_lufs": t.measured_output_lufs,
                    "target_lufs": t.target_lufs,
                    "chain_applied": list(t.chain_applied),
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
# DeepFilterNet detection (optional)
# ---------------------------------------------------------------------------


def _has_deepfilternet() -> bool:
    """Lazy probe — returns True iff DeepFilterNet is importable AND its
    `deepFilter` CLI is on PATH. Either condition can fail; we don't load the
    Python module at probe time (heavy import), only at use time."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("df")
        if spec is None:
            return False
    except Exception:
        return False
    return shutil.which("deepFilter") is not None


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _run_ffmpeg(args: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[bytes]:
    """Run ffmpeg with the given args. Returns the completed process; on
    failure, logs stderr and re-raises CalledProcessError so callers can
    decide whether to fall back."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "info", *args]
    try:
        return subprocess.run(cmd, capture_output=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        logger.error(
            "ffmpeg failed (rc=%d): %s",
            exc.returncode,
            (exc.stderr or b"").decode(errors="replace")[-2000:],
        )
        raise


_LUFS_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)


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
    except subprocess.CalledProcessError as exc:
        logger.warning("ebur128 measurement failed for %s: rc=%d", input_path, exc.returncode)
        return None
    stderr = (result.stderr or b"").decode(errors="replace")
    matches = _LUFS_RE.findall(stderr)
    if not matches:
        # Fallback: parse the summary block
        m = re.search(r"Integrated loudness:.*?I:\s+(-?\d+(?:\.\d+)?)\s*LUFS", stderr, re.S)
        if m:
            return float(m.group(1))
        return None
    return float(matches[-1])


# ---------------------------------------------------------------------------
# Chain step builders (return ffmpeg -af strings)
# ---------------------------------------------------------------------------


def _highpass_filter(freq_hz: int = DEFAULT_HIGHPASS_HZ) -> str:
    return f"highpass=f={freq_hz}"


def _afftdn_filter() -> str:
    """ffmpeg's built-in spectral denoiser (fallback for DeepFilterNet)."""
    return "afftdn=nr=12:nf=-25"


def _de_ess_filter() -> str:
    """Gentle de-ess via firequalizer dipping the 5-9 kHz region by ~3 dB."""
    return "firequalizer=gain_entry='entry(0,0);entry(4000,0);entry(6000,-3);entry(8000,-3);entry(10000,0)'"


def _compressor_filter() -> str:
    """3:1 soft-knee compressor, attack 10ms / release 100ms, makeup +1 dB."""
    return "acompressor=ratio=3:threshold=-18dB:attack=10:release=100:makeup=1:knee=4"


def _voice_eq_filter() -> str:
    """Subtle voice EQ: dip 250 Hz, lift 3 kHz."""
    return "equalizer=f=250:width_type=h:width=200:g=-1.5,equalizer=f=3000:width_type=h:width=2000:g=1.5"


def _limiter_filter(true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP) -> str:
    """ffmpeg's alimiter with true-peak ceiling."""
    return f"alimiter=limit=-{abs(true_peak_dbtp):.2f}dB:level=disabled"


# ---------------------------------------------------------------------------
# The mastering chain
# ---------------------------------------------------------------------------


def master_track(
    input_path: Path,
    output_path: Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
    lra: float = DEFAULT_LRA,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    use_deepfilternet: bool | None = None,
    measure_input: bool = True,
) -> MasteredTrack:
    """Master one per-speaker track end-to-end.

    Two passes:
        1. Apply the chain (high-pass, denoise, de-ess, comp, EQ, limiter)
           and write to a temp file.
        2. Two-pass loudnorm: measure the temp's loudness, then apply
           ``loudnorm`` with the measured values to land precisely at
           ``target_lufs``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chain_applied: list[str] = []
    measured_input = measure_lufs(input_path) if measure_input else None

    # Decide on denoise step
    if use_deepfilternet is None:
        use_deepfilternet = _has_deepfilternet()
    denoise_step = "deepfilternet" if use_deepfilternet else "afftdn"

    # Chain (everything before loudnorm)
    chain_filters: list[str] = []
    chain_filters.append(_highpass_filter(DEFAULT_HIGHPASS_HZ))
    chain_applied.append("highpass")
    if use_deepfilternet:
        # DeepFilterNet runs as a separate CLI step (not a libavfilter), so we
        # apply it via a pre-pass to a temp .wav.  ffmpeg post-pass does the
        # remaining filters.  Not fatal if denoise fails; falls back.
        try:
            denoised_tmp = output_path.with_suffix(".denoised.wav")
            subprocess.run(
                ["deepFilter", "-o", str(denoised_tmp), str(input_path)],
                check=True,
                capture_output=True,
                timeout=600,
            )
            input_path = denoised_tmp
            chain_applied.append("deepfilternet")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "DeepFilterNet failed (%s) — falling back to afftdn", type(exc).__name__
            )
            chain_filters.append(_afftdn_filter())
            chain_applied.append("afftdn")
            denoise_step = "afftdn"
    else:
        chain_filters.append(_afftdn_filter())
        chain_applied.append("afftdn")

    chain_filters.append(_de_ess_filter())
    chain_applied.append("de-ess")
    chain_filters.append(_compressor_filter())
    chain_applied.append("compressor")
    chain_filters.append(_voice_eq_filter())
    chain_applied.append("voice-eq")
    chain_filters.append(_limiter_filter(true_peak_dbtp))
    chain_applied.append("limiter")

    # Pass 1: chain-only to temp .wav
    chain_tmp = output_path.with_suffix(".chain.wav")
    af_chain = ",".join(chain_filters)
    _run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-af",
            af_chain,
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(chain_tmp),
        ]
    )
    chain_applied.append(f"_temp:{chain_tmp.name}:{denoise_step}")

    # Pass 2: two-pass loudnorm — measure first
    measure_args = [
        "-i",
        str(chain_tmp),
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "info", *measure_args],
        capture_output=True,
        timeout=600,
    )
    stderr = (proc.stderr or b"").decode(errors="replace")
    measured = _parse_loudnorm_measurement(stderr)
    if measured is not None:
        # Pass 2: apply loudnorm with measured values
        norm_filter = (
            f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}:"
            f"measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:"
            "linear=true:print_format=summary"
        )
        _run_ffmpeg(
            [
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
        )
        chain_applied.append(f"loudnorm-2pass(target={target_lufs})")
    else:
        # Pass 2 fallback: single-pass loudnorm
        logger.warning("Could not parse loudnorm measurement; using single-pass")
        _run_ffmpeg(
            [
                "-i",
                str(chain_tmp),
                "-af",
                f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={lra}",
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        chain_applied.append(f"loudnorm-1pass(target={target_lufs})")

    # Cleanup temp
    if chain_tmp.exists():
        try:
            chain_tmp.unlink()
        except Exception:
            pass

    measured_output = measure_lufs(output_path) if measure_input else None

    return MasteredTrack(
        speaker_label=output_path.stem,
        input_path=input_path,
        output_path=output_path,
        measured_input_lufs=measured_input,
        measured_output_lufs=measured_output,
        target_lufs=target_lufs,
        chain_applied=chain_applied,
    )


_LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.S)


def _parse_loudnorm_measurement(stderr: str) -> dict[str, str] | None:
    """ffmpeg writes a JSON blob to stderr in pass-1 measurement mode."""
    m = _LOUDNORM_JSON_RE.search(stderr)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def master_all_speakers(
    inputs: dict[str, Path],
    output_dir: Path,
    container: str,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    use_deepfilternet: bool | None = None,
) -> MasteringResult:
    """Master each speaker's unified track.

    ``inputs`` is a dict[speaker_label -> Path-to-unified-wav]. Outputs land
    at ``output_dir/{speaker_label}_mastered.wav``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = MasteringResult(
        container=container,
        target_lufs=target_lufs,
        true_peak_dbtp=true_peak_dbtp,
        sample_rate=sample_rate,
    )
    for speaker_label, in_path in inputs.items():
        if not in_path.exists():
            result.skipped.append(f"{speaker_label}: input missing at {in_path}")
            continue
        out_path = output_dir / f"{speaker_label}_mastered.wav"
        try:
            track = master_track(
                in_path,
                out_path,
                target_lufs=target_lufs,
                true_peak_dbtp=true_peak_dbtp,
                sample_rate=sample_rate,
                use_deepfilternet=use_deepfilternet,
            )
            result.tracks.append(track)
            logger.info(
                "Mastered %s: in_LUFS=%s -> out_LUFS=%s, chain=%s",
                speaker_label,
                track.measured_input_lufs,
                track.measured_output_lufs,
                track.chain_applied,
            )
        except subprocess.CalledProcessError as exc:
            result.skipped.append(f"{speaker_label}: ffmpeg failed (rc={exc.returncode})")
        except subprocess.TimeoutExpired:
            result.skipped.append(f"{speaker_label}: ffmpeg timed out")
    return result
