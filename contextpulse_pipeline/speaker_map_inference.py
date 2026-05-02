"""
Speaker map inference from N parallel-channel audio recordings.

Architecture (per Phase A.0 research, 2026-05-02):
  Mirrors Auphonic's two-stage pattern: per-channel signal extraction ->
  cross-channel decision rule. The discriminator between foreground (wearer
  speaking on their own mic) and bleed (other person picked up at distance)
  is the log-RMS RATIO between channels at each timestamp, NOT per-channel
  loudness alone. Foreground is typically 15-25 dB louder than bleed on
  lavalier mics.

Outputs:
  - wearer_channels: which channels have a wearer (high foreground duty cycle)
  - speaker_role: main_speaker / active_participant / passive_or_unworn
  - best_enrollment_window: longest continuous foreground passage per wearer
  - no_mic_present: bool, is there speech that's bleed-equally-into-multiple
                    channels (a person without a mic in the room)
  - no_mic_timestamps: when the no-mic speaker(s) are speaking
  - capture_warnings: gain mismatches, alternating-mode artifacts, etc.

v0.1 implementation notes:
  - Pure numpy + soundfile, no ML deps yet
  - VAD = energy threshold above silence floor (Silero VAD comes in v0.2)
  - Speaker clustering = duty-cycle + role heuristic (ECAPA comes in v0.2)
  - Sufficient for clean 4/26-hike-style multi-mic recordings; v0.2 needed
    for harder scenarios (overlapping speech, mic handoffs, room reverb)

Usage:
  python -m contextpulse_pipeline.speaker_map_inference \\
    --channel chris=path/to/chris.wav \\
    --channel josh=path/to/josh.wav \\
    [--threshold-db -22] [--smooth-k 5] [--ratio-db 6]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import soundfile as sf

WINDOW_SEC = 1.0
SMOOTH_K = 5  # 5-sec moving average


@dataclass
class ChannelStats:
    name: str
    path: str
    duration_sec: int
    p10_db: float
    p50_db: float
    p90_db: float
    p99_db: float
    max_db: float
    foreground_duty_cycle: float
    longest_run_sec: int
    longest_run_start_sec: int
    longest_run_mean_db: float


@dataclass
class SpeakerMap:
    channels: list[ChannelStats]
    wearer_channels: list[str]
    speaker_roles: dict[str, str]  # channel_name -> role
    best_enrollment_window: dict[str, dict]  # channel_name -> {start, end, mean_db}
    no_mic_present: bool
    no_mic_duty_cycle: float
    no_mic_timestamps: list[tuple[int, int]]  # list of (start_sec, end_sec)
    capture_warnings: list[str]
    config: dict


def db(x: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(x, 1e-10))


def rms_per_window(path: Path, window_sec: float = WINDOW_SEC) -> tuple[np.ndarray, int]:
    """Compute RMS in non-overlapping windows. Returns (rms_db_array, sample_rate)."""
    info = sf.info(str(path))
    sr = info.samplerate
    win = int(window_sec * sr)
    n_windows = info.frames // win

    rms = np.empty(n_windows, dtype=np.float64)
    with sf.SoundFile(str(path)) as f:
        for i in range(n_windows):
            chunk = f.read(win, dtype="float32", always_2d=False)
            if chunk.ndim > 1:
                chunk = chunk.mean(axis=1)
            rms[i] = float(np.sqrt(np.mean(chunk * chunk)))
    return db(rms), sr


def smooth(x: np.ndarray, k: int = SMOOTH_K) -> np.ndarray:
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="same")


def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Find all (start, end_exclusive) of True runs in a boolean array."""
    runs = []
    in_run = False
    cur_start = 0
    for i, v in enumerate(mask):
        if v and not in_run:
            cur_start = i
            in_run = True
        elif not v and in_run:
            runs.append((cur_start, i))
            in_run = False
    if in_run:
        runs.append((cur_start, len(mask)))
    return runs


def classify_role(duty_cycle: float, longest_run_sec: int) -> str:
    """Wearer thresholds: >30% main_speaker, >5% active, >2% passive_wearer, else unworn."""
    if duty_cycle > 0.30:
        return "main_speaker"  # interview guest, lecturer, etc.
    elif duty_cycle > 0.05:
        return "active_participant"  # interview host, conversation peer
    elif duty_cycle > 0.02:
        return "passive_wearer"  # very quiet wearer, but the channel does have foreground signal
    else:
        return "unworn"


# Tuning presets per recording environment
PRESETS = {
    "studio": {
        "threshold_db": -22.0,  # clean recording, clear foreground
        "ratio_db": 6.0,        # comfortable margin
        "no_mic_threshold_db": -32.0,
        "coincidence_db": 5.0,
    },
    "hike": {
        "threshold_db": -25.0,  # outdoor + lower mic gain = quieter foreground
        "ratio_db": 3.0,        # narrower margin (room reverb / wind)
        "no_mic_threshold_db": -34.0,
        "coincidence_db": 5.0,
    },
}


def analyze_channels(channel_paths: dict[str, Path], threshold_db: float, smooth_k: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, ChannelStats]]:
    """Per-channel RMS extraction + smoothing + stats. Returns aligned arrays."""
    raw_rms = {}
    smoothed_rms = {}
    stats_by_name = {}

    min_len = None
    for name, path in channel_paths.items():
        rms_db, _ = rms_per_window(path)
        smoothed = smooth(rms_db, k=smooth_k)
        raw_rms[name] = rms_db
        smoothed_rms[name] = smoothed
        if min_len is None or len(rms_db) < min_len:
            min_len = len(rms_db)

    # Truncate all to the minimum length for cross-channel alignment
    for name in channel_paths:
        raw_rms[name] = raw_rms[name][:min_len]
        smoothed_rms[name] = smoothed_rms[name][:min_len]

    # Per-channel stats
    for name, path in channel_paths.items():
        s = smoothed_rms[name]
        mask = s > threshold_db
        runs = find_runs(mask)
        if runs:
            longest = max(runs, key=lambda r: r[1] - r[0])
            longest_len = longest[1] - longest[0]
            longest_start = longest[0]
            longest_mean = float(s[longest[0]:longest[1]].mean())
        else:
            longest_len = 0
            longest_start = 0
            longest_mean = -100.0

        stats_by_name[name] = ChannelStats(
            name=name,
            path=str(path),
            duration_sec=min_len,
            p10_db=float(np.percentile(s, 10)),
            p50_db=float(np.percentile(s, 50)),
            p90_db=float(np.percentile(s, 90)),
            p99_db=float(np.percentile(s, 99)),
            max_db=float(s.max()),
            foreground_duty_cycle=float(mask.sum() / len(mask)),
            longest_run_sec=longest_len,
            longest_run_start_sec=longest_start,
            longest_run_mean_db=longest_mean,
        )

    return raw_rms, smoothed_rms, stats_by_name


def detect_foreground_via_ratio(
    smoothed_rms: dict[str, np.ndarray],
    threshold_db: float,
    ratio_db: float,
) -> dict[str, np.ndarray]:
    """For each channel, mark windows where it's the dominant channel by margin >=ratio_db.

    Bleed of speaker A into mic B is typically 15-25 dB quieter than A on mic A.
    A 6 dB ratio_db threshold is conservative; 10-15 is safer in clean conditions.
    """
    names = list(smoothed_rms.keys())
    n = len(names)
    foreground = {name: np.zeros_like(smoothed_rms[name], dtype=bool) for name in names}

    for i, name in enumerate(names):
        s_i = smoothed_rms[name]
        # Above absolute threshold
        above = s_i > threshold_db
        # Loudest channel by margin
        margins = np.full_like(s_i, 100.0)
        for j, other in enumerate(names):
            if i == j:
                continue
            margins = np.minimum(margins, s_i - smoothed_rms[other])
        # Foreground = above absolute threshold AND beats every other channel by ratio_db
        foreground[name] = above & (margins > ratio_db)

    return foreground


def detect_no_mic_speakers(
    smoothed_rms: dict[str, np.ndarray],
    foreground: dict[str, np.ndarray],
    no_mic_threshold_db: float,
    coincidence_db: float,
) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Find windows where multiple channels show moderate-equal RMS -- likely a no-mic speaker.

    Heuristic: at timestamp t, if no channel is "foreground" (no wearer dominant)
    AND ≥2 channels are above no_mic_threshold_db AND those channels are within
    coincidence_db of each other -> likely a no-mic person speaking, picked up
    as bleed roughly equally across nearby lavaliers.
    """
    names = list(smoothed_rms.keys())
    n_windows = len(smoothed_rms[names[0]])
    no_mic_mask = np.zeros(n_windows, dtype=bool)

    # Stack into (n_windows, n_channels) array
    stacked = np.stack([smoothed_rms[name] for name in names], axis=1)
    foreground_any = np.any(np.stack([foreground[name] for name in names], axis=1), axis=1)

    # For each window: count how many channels are above no_mic threshold
    above_threshold = stacked > no_mic_threshold_db
    n_above = above_threshold.sum(axis=1)

    # Spread between max and min across active channels
    max_per_window = stacked.max(axis=1)
    # Mask out channels below threshold by setting them very low; then take the actual minimum of active ones
    masked_for_min = np.where(above_threshold, stacked, np.inf)
    min_active = np.where(above_threshold.any(axis=1), masked_for_min.min(axis=1), -100.0)
    spread = max_per_window - min_active

    # No-mic speaker: not foreground anywhere, ≥2 channels active, spread < coincidence_db
    no_mic_mask = (~foreground_any) & (n_above >= 2) & (spread < coincidence_db)

    runs = find_runs(no_mic_mask)
    return no_mic_mask, runs


def detect_capture_warnings(stats_by_name: dict[str, ChannelStats]) -> list[str]:
    warnings = []
    names = list(stats_by_name.keys())

    # Peak parity check across wearers
    peaks = [s.max_db for s in stats_by_name.values()]
    if len(peaks) >= 2:
        peak_spread = max(peaks) - min(peaks)
        if peak_spread > 8:
            channel_with_max = max(stats_by_name.values(), key=lambda s: s.max_db).name
            channel_with_min = min(stats_by_name.values(), key=lambda s: s.max_db).name
            warnings.append(
                f"Peak level mismatch across channels: {peak_spread:.1f} dB delta "
                f"({channel_with_max}={stats_by_name[channel_with_max].max_db:.1f} dB vs "
                f"{channel_with_min}={stats_by_name[channel_with_min].max_db:.1f} dB). "
                "Could indicate gain mismatch on receiver. Verify peak-meter parity at next session."
            )

    # Alternating mode check (file-length proxy can't be done here; pass through)
    # Caller should check that all input files are similar duration before calling

    # Very low p99 on any channel = wearer probably never spoke or gain set very low
    for name, s in stats_by_name.items():
        if s.p99_db < -25:
            warnings.append(
                f"Channel '{name}' p99={s.p99_db:.1f} dB -- very low even at the loudest moments. "
                "Either wearer barely spoke OR mic gain set too low."
            )
        if s.foreground_duty_cycle < 0.02:
            warnings.append(
                f"Channel '{name}' foreground duty cycle {s.foreground_duty_cycle*100:.1f}% -- wearer rarely active. "
                "Check capture mode (DJI alternating?) or whether this channel was actually worn."
            )

    return warnings


def render_text_report(speaker_map: SpeakerMap) -> str:
    """Pretty-print the speaker map as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"SPEAKER MAP -- {speaker_map.config.get('episode', 'unknown')}")
    lines.append(
        f"{len(speaker_map.wearer_channels)} miked speakers detected"
        + (f" + 1 no-mic speaker" if speaker_map.no_mic_present else "")
    )
    lines.append("=" * 70)
    lines.append("")

    for stats in speaker_map.channels:
        role = speaker_map.speaker_roles[stats.name]
        wearer = stats.name in speaker_map.wearer_channels
        lines.append(f"CHANNEL: {stats.name}")
        lines.append(f"  Role:                  {role}{' (WEARER)' if wearer else ''}")
        lines.append(f"  Duty cycle (foreground): {stats.foreground_duty_cycle*100:.1f}%")
        lines.append(f"  Peak level:            {stats.max_db:.1f} dB")
        lines.append(f"  Median:                {stats.p50_db:.1f} dB    (p99: {stats.p99_db:.1f})")
        if wearer:
            best = speaker_map.best_enrollment_window[stats.name]
            start_mmss = f"{best['start']//60:02d}:{best['start']%60:02d}"
            end_mmss = f"{best['end']//60:02d}:{best['end']%60:02d}"
            lines.append(f"  Best clip:             {start_mmss} -> {end_mmss} "
                        f"({best['end']-best['start']}s, mean {best['mean_db']:.1f} dB)")
        lines.append("")

    if speaker_map.no_mic_present:
        lines.append("NO-MIC SPEAKER DETECTED")
        lines.append(
            f"  Active duty cycle:     {speaker_map.no_mic_duty_cycle*100:.1f}% of recording"
        )
        lines.append(f"  Detected via:          cross-channel moderate-equal RMS coincidence")
        lines.append(f"  Cannot be enrolled from this recording")
        lines.append(f"  -> Add a transmitter for this person next session")
        if speaker_map.no_mic_timestamps:
            lines.append("  Sample windows (first 5):")
            for start, end in speaker_map.no_mic_timestamps[:5]:
                lines.append(
                    f"    {start//60:02d}:{start%60:02d} -> {end//60:02d}:{end%60:02d}  ({end-start}s)"
                )
        lines.append("")

    if speaker_map.capture_warnings:
        lines.append("=" * 70)
        lines.append("CAPTURE WARNINGS")
        lines.append("=" * 70)
        for w in speaker_map.capture_warnings:
            lines.append(f"!! {w}")
        lines.append("")

    return "\n".join(lines)


def infer_speaker_map(
    channel_paths: dict[str, Path],
    threshold_db: float = -22.0,
    smooth_k: int = SMOOTH_K,
    ratio_db: float = 6.0,
    no_mic_threshold_db: float = -32.0,
    coincidence_db: float = 5.0,
    episode: str = "unknown",
) -> SpeakerMap:
    """Main entry point. Returns a SpeakerMap object."""
    raw_rms, smoothed_rms, stats_by_name = analyze_channels(
        channel_paths, threshold_db=threshold_db, smooth_k=smooth_k
    )

    foreground = detect_foreground_via_ratio(
        smoothed_rms, threshold_db=threshold_db, ratio_db=ratio_db
    )

    # Wearer channels = >2% foreground duty cycle (matches passive_wearer role threshold)
    wearer_channels = [name for name, s in stats_by_name.items() if s.foreground_duty_cycle > 0.02]

    # Roles
    roles = {
        name: classify_role(s.foreground_duty_cycle, s.longest_run_sec)
        for name, s in stats_by_name.items()
    }

    # Best enrollment window per wearer (longest run on their own channel)
    best_enrollment = {}
    for name in wearer_channels:
        s = stats_by_name[name]
        # Recompute the start/end of the longest run (we have it in stats already)
        best_enrollment[name] = {
            "start": s.longest_run_start_sec,
            "end": s.longest_run_start_sec + s.longest_run_sec,
            "mean_db": s.longest_run_mean_db,
        }

    # No-mic speaker detection
    no_mic_mask, no_mic_runs = detect_no_mic_speakers(
        smoothed_rms, foreground, no_mic_threshold_db=no_mic_threshold_db, coincidence_db=coincidence_db
    )
    no_mic_duty = float(no_mic_mask.sum() / len(no_mic_mask))
    no_mic_present = no_mic_duty > 0.02

    capture_warnings = detect_capture_warnings(stats_by_name)

    return SpeakerMap(
        channels=list(stats_by_name.values()),
        wearer_channels=wearer_channels,
        speaker_roles=roles,
        best_enrollment_window=best_enrollment,
        no_mic_present=no_mic_present,
        no_mic_duty_cycle=no_mic_duty,
        no_mic_timestamps=[(int(s), int(e)) for s, e in no_mic_runs if (e - s) >= 3],
        capture_warnings=capture_warnings,
        config={
            "threshold_db": threshold_db,
            "smooth_k": smooth_k,
            "ratio_db": ratio_db,
            "no_mic_threshold_db": no_mic_threshold_db,
            "coincidence_db": coincidence_db,
            "episode": episode,
        },
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--channel",
        action="append",
        required=True,
        help="Channel input as NAME=PATH (e.g. --channel chris=chris.wav). Repeat for multiple channels.",
    )
    ap.add_argument("--preset", choices=list(PRESETS.keys()), default="studio",
                    help="Tuning preset: 'studio' (default, clean recording) or 'hike' (outdoor, lower mic gain).")
    ap.add_argument("--threshold-db", type=float, default=None,
                    help="Override foreground RMS threshold (preset default if unset).")
    ap.add_argument("--ratio-db", type=float, default=None,
                    help="Override min RMS margin vs other channels (preset default if unset).")
    ap.add_argument("--smooth-k", type=int, default=SMOOTH_K,
                    help="Smoothing window in seconds (default 5).")
    ap.add_argument("--no-mic-threshold-db", type=float, default=None,
                    help="Override min RMS for no-mic-speaker detection (preset default if unset).")
    ap.add_argument("--coincidence-db", type=float, default=None,
                    help="Override max spread across channels to count as coincidence (preset default if unset).")
    ap.add_argument("--episode", default="unknown", help="Episode name for the report header.")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of text report.")
    args = ap.parse_args()

    channel_paths = {}
    for spec in args.channel:
        if "=" not in spec:
            sys.exit(f"Bad --channel spec '{spec}'; expected NAME=PATH")
        name, path = spec.split("=", 1)
        path = Path(path)
        if not path.exists():
            sys.exit(f"Channel '{name}' file not found: {path}")
        channel_paths[name] = path

    preset = PRESETS[args.preset]
    speaker_map = infer_speaker_map(
        channel_paths,
        threshold_db=args.threshold_db if args.threshold_db is not None else preset["threshold_db"],
        smooth_k=args.smooth_k,
        ratio_db=args.ratio_db if args.ratio_db is not None else preset["ratio_db"],
        no_mic_threshold_db=args.no_mic_threshold_db if args.no_mic_threshold_db is not None else preset["no_mic_threshold_db"],
        coincidence_db=args.coincidence_db if args.coincidence_db is not None else preset["coincidence_db"],
        episode=args.episode,
    )

    if args.json:
        # Convert dataclasses to dict for JSON serialization
        out = asdict(speaker_map)
        print(json.dumps(out, indent=2))
    else:
        print(render_text_report(speaker_map))


if __name__ == "__main__":
    main()
