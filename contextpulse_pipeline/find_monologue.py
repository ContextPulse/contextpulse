"""
Find the cleanest monologue window in a single-channel mic recording where
cross-speaker bleed is present.

Strategy: compute RMS in 1-sec windows, smooth, then find the longest run
of windows above a "foreground speech" threshold. Foreground speech (the
wearer talking into their own lavalier) is consistently 15-25 dB louder
than bleed from another nearby speaker.

Usage:
    python find_monologue.py <wav_path> [--clip-seconds 30] [--threshold-db -22]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

WINDOW_SEC = 1.0


def db(x: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(x, 1e-10))


def rms_per_window(path: Path, window_sec: float = WINDOW_SEC) -> tuple[np.ndarray, int]:
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


def smooth(x: np.ndarray, k: int = 5) -> np.ndarray:
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="same")


def longest_run(mask: np.ndarray) -> tuple[int, int, int]:
    """Return (start_idx, end_idx_exclusive, length) of the longest True run."""
    best_start = best_len = 0
    cur_start = cur_len = 0
    in_run = False
    for i, v in enumerate(mask):
        if v:
            if not in_run:
                cur_start = i
                cur_len = 1
                in_run = True
            else:
                cur_len += 1
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
        else:
            in_run = False
    return best_start, best_start + best_len, best_len


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", type=Path)
    ap.add_argument("--clip-seconds", type=int, default=30)
    ap.add_argument("--threshold-db", type=float, default=-22.0,
                    help="RMS dB threshold; windows above this count as foreground speech.")
    ap.add_argument("--smooth-k", type=int, default=5,
                    help="Smoothing window in seconds.")
    ap.add_argument("--top-n", type=int, default=5,
                    help="Show top-N candidate runs.")
    args = ap.parse_args()

    print(f"Reading: {args.wav}")
    rms_db, sr = rms_per_window(args.wav)
    print(f"Duration: {len(rms_db)} sec @ {sr} Hz")
    print(f"RMS distribution: p10={np.percentile(rms_db,10):.1f} "
          f"p50={np.percentile(rms_db,50):.1f} p90={np.percentile(rms_db,90):.1f} "
          f"p99={np.percentile(rms_db,99):.1f} max={rms_db.max():.1f} dB")

    smoothed = smooth(rms_db, k=args.smooth_k)
    mask = smoothed > args.threshold_db

    # Find all runs above threshold, ranked by length
    runs: list[tuple[int, int, int, float]] = []
    in_run = False
    cur_start = 0
    for i, v in enumerate(mask):
        if v and not in_run:
            cur_start = i
            in_run = True
        elif not v and in_run:
            length = i - cur_start
            avg = float(smoothed[cur_start:i].mean())
            runs.append((cur_start, i, length, avg))
            in_run = False
    if in_run:
        length = len(mask) - cur_start
        avg = float(smoothed[cur_start:].mean())
        runs.append((cur_start, len(mask), length, avg))

    runs.sort(key=lambda r: -r[2])
    print(f"\n{len(runs)} runs above {args.threshold_db} dB (smoothed {args.smooth_k}s).")
    print(f"Top {args.top_n} candidate runs (sorted by length):")
    print(f"{'start_mmss':<12} {'end_mmss':<12} {'len_sec':<10} {'mean_dB':<10}")
    for start, end, length, avg in runs[:args.top_n]:
        smm = f"{start//60:02d}:{start%60:02d}"
        emm = f"{end//60:02d}:{end%60:02d}"
        print(f"{smm:<12} {emm:<12} {length:<10} {avg:<10.2f}")

    if not runs:
        print("\nNo runs found. Lower --threshold-db (e.g., -25 or -28) and re-run.")
        sys.exit(1)

    # Recommend a clip from the longest run, centered if possible
    best_start, best_end, best_len, best_avg = runs[0]
    if best_len >= args.clip_seconds:
        clip_start = best_start + (best_len - args.clip_seconds) // 2
    else:
        clip_start = best_start
        print(f"\nWARNING: longest run is {best_len}s < requested {args.clip_seconds}s clip. "
              "Will clip what's available.")
    clip_end = clip_start + args.clip_seconds
    print(f"\nRECOMMENDED CLIP:")
    print(f"  ffmpeg -ss {clip_start} -t {args.clip_seconds} -i \"{args.wav}\" ...")
    print(f"  ({clip_start//60:02d}:{clip_start%60:02d} -> {clip_end//60:02d}:{clip_end%60:02d})")


if __name__ == "__main__":
    main()
