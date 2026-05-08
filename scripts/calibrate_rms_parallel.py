"""Phase B.1 — calibrate the dB margin for parallel-mic dominant-speaker decisions.

Reads mic_owner_map.json, finds parallel-mic windows, computes per-mic RMS at
100ms hops on each mic over those windows, then analyzes the empirical
distribution of (chris_mic_dBFS - josh_mic_dBFS) on voiced instants.

Outputs:
  working/<episode>/rms_calibration.json  — thresholds + summary stats
  working/<episode>/rms_curves.npz        — per-mic RMS curves for Phase C re-use
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

# Force UTF-8 stdout so we can print non-ASCII safely on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


HOP_SEC = 0.1
VAD_THRESHOLD_DBFS = -45.0
DEFAULT_MARGIN_DB = 6.0
DJI_DUMP = Path(r"C:\Users\david\Desktop\dji mic3")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_rms_curve(audio_path: Path, hop_sec: float = HOP_SEC) -> np.ndarray:
    """Return RMS-dBFS array at hop_sec resolution. Floor at -120 dBFS for silence."""
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        audio = f.read(dtype="float32", always_2d=True)
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1)
    else:
        audio = audio[:, 0]

    hop_samples = int(round(hop_sec * sr))
    n_hops = len(audio) // hop_samples
    audio_trim = audio[: n_hops * hop_samples].reshape(n_hops, hop_samples).astype(np.float64)
    rms = np.sqrt(np.mean(audio_trim ** 2, axis=1))
    with np.errstate(divide="ignore"):
        rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    return rms_db.astype(np.float32)


def find_files_for_window(mom: dict, win_start: datetime, win_end: datetime) -> dict[str, dict]:
    """Find each wearer's BEST-OVERLAPPING file with [win_start, win_end].
    A wearer can have multiple intervals overlapping the window (e.g., a file
    rollover within the window); pick the one with the largest intersection."""
    out = {}
    for wearer, info in mom["wearers"].items():
        best_overlap_sec = 0.0
        for interval in info["intervals"]:
            f_start = parse_iso(interval["start_utc"])
            f_end = parse_iso(interval["end_utc"])
            if f_start >= win_end or f_end <= win_start:
                continue
            overlap_sec = (min(f_end, win_end) - max(f_start, win_start)).total_seconds()
            if overlap_sec > best_overlap_sec:
                best_overlap_sec = overlap_sec
                out[wearer] = {
                    "filename": interval["source_file"],
                    "start_utc": f_start,
                    "end_utc": f_end,
                    "path": DJI_DUMP / interval["source_file"],
                }
    return out


def slice_window(rms_db: np.ndarray, file_start: datetime, win_start: datetime,
                 win_end: datetime, hop_sec: float) -> np.ndarray:
    """Slice the RMS curve to the window, indexed by hop offset from file start."""
    offset_start_sec = (win_start - file_start).total_seconds()
    offset_end_sec = (win_end - file_start).total_seconds()
    i0 = max(0, int(round(offset_start_sec / hop_sec)))
    i1 = min(len(rms_db), int(round(offset_end_sec / hop_sec)))
    return rms_db[i0:i1]


def text_histogram(arr: np.ndarray, lo: float, hi: float, step: float, max_width: int = 50) -> str:
    """ASCII histogram of arr in [lo, hi] with bins of width step."""
    bins = np.arange(lo, hi + step, step)
    hist, edges = np.histogram(arr, bins=bins)
    if hist.max() == 0:
        return "(empty)"
    lines = []
    for i in range(len(hist)):
        bar_len = int(hist[i] / hist.max() * max_width)
        lines.append(f"  {edges[i]:+6.1f} to {edges[i+1]:+6.1f}: {hist[i]:6d}  {'#' * bar_len}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--vad-dbfs", type=float, default=VAD_THRESHOLD_DBFS)
    ap.add_argument("--margin-db", type=float, default=DEFAULT_MARGIN_DB)
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    mom = json.loads((ep_dir / "mic_owner_map.json").read_text(encoding="utf-8"))

    parallel_windows = [w for w in mom["windows"] if w["strategy"] == "parallel_rms"]
    print(f"Episode: {mom['session_id']}")
    print(f"Parallel windows: {len(parallel_windows)}")
    print()

    rms_cache = {}  # filename -> rms_dB array
    chris_chunks, josh_chunks = [], []
    timestamps_chunks = []  # wall-clock seconds since first window start
    window_summaries = []

    for win in parallel_windows:
        win_start = parse_iso(win["start_utc"])
        win_end = parse_iso(win["end_utc"])
        win_dur = (win_end - win_start).total_seconds()
        print(f"Window: {win['label']}  ({win['start_utc']} -> {win['end_utc']}, {win_dur:.0f}s)")

        files = find_files_for_window(mom, win_start, win_end)
        if "chris" not in files or "josh" not in files:
            print(f"  SKIP: missing wearer file (have: {list(files.keys())})")
            continue

        # Actual processing range is the intersection of window and both files
        actual_start = max(win_start, files["chris"]["start_utc"], files["josh"]["start_utc"])
        actual_end   = min(win_end,   files["chris"]["end_utc"],   files["josh"]["end_utc"])
        actual_dur = (actual_end - actual_start).total_seconds()
        if actual_dur <= 0:
            print(f"  SKIP: empty intersection")
            continue
        if actual_start != win_start or actual_end != win_end:
            print(f"  Intersection: {actual_start.isoformat()} -> {actual_end.isoformat()} ({actual_dur:.0f}s)")

        for wearer, info in files.items():
            if not info["path"].exists():
                print(f"  MISSING audio: {info['path']}")
                continue
            if info["filename"] not in rms_cache:
                print(f"  Computing RMS for {info['filename']} ({info['path'].stat().st_size/1e6:.0f} MB)...", end=" ", flush=True)
                rms_cache[info["filename"]] = compute_rms_curve(info["path"])
                print(f"done ({len(rms_cache[info['filename']])} hops)")

        chris_db = slice_window(
            rms_cache[files["chris"]["filename"]], files["chris"]["start_utc"],
            actual_start, actual_end, HOP_SEC,
        )
        josh_db = slice_window(
            rms_cache[files["josh"]["filename"]], files["josh"]["start_utc"],
            actual_start, actual_end, HOP_SEC,
        )
        n = min(len(chris_db), len(josh_db))
        chris_db, josh_db = chris_db[:n], josh_db[:n]
        print(f"  Aligned: {n} hops ({n * HOP_SEC:.0f}s of parallel data)")

        chris_chunks.append(chris_db)
        josh_chunks.append(josh_db)
        window_summaries.append({
            "label": win["label"],
            "actual_start_utc": actual_start.isoformat(),
            "actual_end_utc": actual_end.isoformat(),
            "n_hops": n,
        })

    if not chris_chunks:
        print("No parallel data could be processed.")
        return 1

    chris_dB = np.concatenate(chris_chunks)
    josh_dB = np.concatenate(josh_chunks)
    delta_dB = chris_dB - josh_dB  # positive = Chris-mic louder
    n_total = len(chris_dB)

    # VAD gate: at least one mic above threshold
    voiced = (chris_dB > args.vad_dbfs) | (josh_dB > args.vad_dbfs)
    n_voiced = int(voiced.sum())
    print(f"\nTotal hops across windows: {n_total}  ({n_total * HOP_SEC / 60:.1f} min)")
    print(f"Voiced (≥{args.vad_dbfs:.0f} dBFS on either mic): {n_voiced} ({100 * n_voiced / n_total:.1f}%)")

    delta_voiced = delta_dB[voiced]
    chris_voiced = chris_dB[voiced]
    josh_voiced = josh_dB[voiced]

    print("\n=== Per-mic RMS dBFS distribution (voiced hops) ===")
    print(f"             p10    p25    p50    p75    p90    p95")
    for name, arr in [("Chris-mic", chris_voiced), ("Josh-mic", josh_voiced)]:
        ps = np.percentile(arr, [10, 25, 50, 75, 90, 95])
        print(f"  {name:9s}  " + "  ".join(f"{v:+5.1f}" for v in ps))

    print("\n=== Delta dB (Chris - Josh) distribution (voiced hops) ===")
    print(f"  pct: " + "  ".join(f"p{p:02d}" for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]))
    pcts = np.percentile(delta_voiced, [1, 5, 10, 25, 50, 75, 90, 95, 99])
    print(f"  val: " + "  ".join(f"{v:+5.1f}" for v in pcts))

    print("\n=== Delta dB histogram (Chris-dominant > 0, Josh-dominant < 0) ===")
    print(text_histogram(delta_voiced, -40, 40, 2.0))

    margin = args.margin_db
    chris_dom = (delta_voiced > margin).sum()
    josh_dom = (delta_voiced < -margin).sum()
    ambig = n_voiced - int(chris_dom) - int(josh_dom)
    print(f"\n=== Classification with ±{margin:.0f} dB margin ===")
    print(f"  Chris-dominant: {chris_dom:6d}  ({100 * chris_dom / n_voiced:5.1f}% of voiced)")
    print(f"  Josh-dominant:  {josh_dom:6d}  ({100 * josh_dom / n_voiced:5.1f}% of voiced)")
    print(f"  Ambiguous:      {ambig:6d}  ({100 * ambig / n_voiced:5.1f}% of voiced — David / both / quiet bleed)")

    # Test multiple candidate margins so we can see the trade-off
    print("\n=== Margin sweep (% of voiced classified as ambiguous) ===")
    for m in [3.0, 4.0, 5.0, 6.0, 7.5, 9.0, 12.0, 15.0]:
        c = (delta_voiced > m).sum()
        j = (delta_voiced < -m).sum()
        a = n_voiced - int(c) - int(j)
        print(f"  ±{m:4.1f} dB: Chris={100*c/n_voiced:5.1f}%  Josh={100*j/n_voiced:5.1f}%  Ambiguous={100*a/n_voiced:5.1f}%")

    out = {
        "session_id": mom["session_id"],
        "schema_version": "v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "hop_sec": HOP_SEC,
        "vad_threshold_dBFS": args.vad_dbfs,
        "selected_margin_dB": margin,
        "windows_used": window_summaries,
        "stats": {
            "total_hops": int(n_total),
            "voiced_hops": int(n_voiced),
            "voiced_pct": round(100 * n_voiced / n_total, 2),
            "chris_dominant_hops": int(chris_dom),
            "josh_dominant_hops": int(josh_dom),
            "ambiguous_hops": int(ambig),
        },
        "delta_dB_percentiles": {
            f"p{p}": float(np.percentile(delta_voiced, p))
            for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]
        },
        "chris_mic_dBFS_percentiles": {
            f"p{p}": float(np.percentile(chris_voiced, p))
            for p in [10, 25, 50, 75, 90, 95]
        },
        "josh_mic_dBFS_percentiles": {
            f"p{p}": float(np.percentile(josh_voiced, p))
            for p in [10, 25, 50, 75, 90, 95]
        },
    }

    out_path = ep_dir / "rms_calibration.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Save per-instant RMS arrays for Phase C re-use
    npz_path = ep_dir / "rms_curves_parallel.npz"
    np.savez_compressed(npz_path, chris_dB=chris_dB, josh_dB=josh_dB, delta_dB=delta_dB)
    print(f"Wrote {npz_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
