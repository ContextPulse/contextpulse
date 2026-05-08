"""Phase A: extract one clean 10-sec listen-test clip per real DJI source.

Names clips by SOURCE FILENAME (not cluster ID) so David can label which mic was on
which speaker. Picks the highest-RMS window per file (skipping the first/last 30s of
mic-handling noise) so the clip lands on clear voice activity rather than silence.

Sources are filtered to those with a real BWF origination timestamp — Telegram
contamination (final-1777*.mp3 under Temp/josh-narrative/) has bwf_origination=null
and is excluded.

Output:
  working/<episode>/mic_audit/<source_stem>.wav   (one per DJI source)
  working/<episode>/mic_audit/MANIFEST.md         (label this column)
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def find_loudest_window(
    src_path: Path,
    window_sec: float,
    stride_sec: float,
    skip_start: float,
    skip_end: float,
) -> tuple[float, float]:
    """Scan strided windows, return (start_sec, rms) of highest-RMS window."""
    with sf.SoundFile(str(src_path)) as f:
        sr = f.samplerate
        total_sec = len(f) / sr
        window_samples = int(window_sec * sr)

        usable_end = total_sec - skip_end - window_sec
        if usable_end <= skip_start:
            # File too short for skip margins — use the middle window
            mid = max(0.0, (total_sec - window_sec) / 2.0)
            f.seek(int(mid * sr))
            block = f.read(window_samples, dtype="float32", always_2d=True)
            mono = block.mean(axis=1) if block.shape[1] > 1 else block[:, 0]
            return mid, float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))

        best_start = skip_start
        best_rms = -1.0
        t = skip_start
        while t <= usable_end:
            f.seek(int(t * sr))
            block = f.read(window_samples, dtype="float32", always_2d=True)
            if len(block) < window_samples:
                break
            mono = block.mean(axis=1) if block.shape[1] > 1 else block[:, 0]
            rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
            if rms > best_rms:
                best_rms = rms
                best_start = t
            t += stride_sec

        return best_start, best_rms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--snippet-sec", type=float, default=10.0)
    ap.add_argument("--stride-sec", type=float, default=30.0,
                    help="RMS-scan stride (smaller = more candidate windows, slower)")
    ap.add_argument("--skip-start", type=float, default=30.0,
                    help="Seconds to skip at file start (mic-placement noise)")
    ap.add_argument("--skip-end", type=float, default=30.0,
                    help="Seconds to skip at file end (mic-handling noise)")
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    raw = json.loads((ep_dir / "raw_sources.json").read_text(encoding="utf-8"))

    # Filter to real DJI sources: must have BWF origination (Telegram contamination is null).
    dji_sources = [s for s in raw["sources"] if s.get("bwf_origination")]
    contam_count = len(raw["sources"]) - len(dji_sources)

    out_dir = ep_dir / "mic_audit"
    out_dir.mkdir(exist_ok=True)
    # Wipe any stale audit clips so the listen-test folder is unambiguous
    for f in out_dir.glob("*.wav"):
        f.unlink()

    print(f"Sources: {len(dji_sources)} DJI / {contam_count} contamination (excluded)")
    print(f"Output:  {out_dir}")
    print()

    manifest_lines = [
        "# Mic-ownership audit — Phase A",
        "",
        f"Episode: `{ep_dir.name}`",
        f"Snippet: {args.snippet_sec:.0f}s, picked at the highest-RMS window of each file",
        f"(skipping first {args.skip_start:.0f}s + last {args.skip_end:.0f}s to avoid mic-handling noise).",
        "",
        "**For each clip, fill in who you hear** — Chris / Josh / David / mixed / silence / unknown.",
        "Note any side-content (e.g. \"Chris talking, Josh in background\").",
        "",
        "| Clip | Source filename | BWF origination (UTC) | Clip start (s into file) | Clip wall time (UTC) | Speaker (you fill in) | Notes |",
        "|---|---|---|---|---|---|---|",
    ]

    rows = []
    sorted_sources = sorted(dji_sources, key=lambda x: x["bwf_origination"])
    for order_idx, s in enumerate(sorted_sources, start=1):
        src_path = Path(s["file_path"])
        if not src_path.exists():
            print(f"  SKIP {src_path.name}: file not found")
            continue

        bwf = s["bwf_origination"]
        print(f"  Scanning {src_path.name} ({float(s['duration_sec']):.0f}s)...", end=" ", flush=True)
        start_sec, rms = find_loudest_window(
            src_path,
            window_sec=args.snippet_sec,
            stride_sec=args.stride_sec,
            skip_start=args.skip_start,
            skip_end=args.skip_end,
        )
        print(f"loudest @ {start_sec:.0f}s (rms={rms:.4f})")

        # Compute the wall-clock time of the clip start
        from datetime import datetime, timedelta
        bwf_dt = datetime.fromisoformat(bwf)
        clip_wall = bwf_dt + timedelta(seconds=start_sec)
        clip_wall_str = clip_wall.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Zero-padded order prefix so the folder sorts in listen-order
        out_name = f"{order_idx:02d}_{src_path.stem}.wav"
        out_path = out_dir / out_name
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start_sec:.2f}",
            "-i", str(src_path),
            "-t", f"{args.snippet_sec:.2f}",
            "-ac", "1", "-ar", "16000",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"    ffmpeg error: {res.stderr[:200]}")
            continue

        rows.append((bwf, out_name, src_path.name, start_sec, clip_wall_str))
        manifest_lines.append(
            f"| `{out_name}` | `{src_path.name}` | {bwf[:19]} | {start_sec:.0f} | "
            f"{clip_wall_str} | _____ | _____ |"
        )

    manifest_lines += [
        "",
        "## Why we're asking",
        "",
        "ECAPA voice clustering on this episode collapsed into mic-segregated buckets",
        "(same-speaker-cross-mic cosine distance 1.143 vs different-speaker-cross-mic 0.680",
        "— voice enrollment is dead on this data). The fallback per `feedback_audio_signal_first.md`",
        "is RMS-based attribution: the dominant mic at each instant identifies the speaker on it.",
        "",
        "Phase A nails down WHO was wearing WHICH mic. That mapping (`mic_owner_map.json`) is the",
        "input to Phase B (calibrate the RMS margin) and Phase C (per-segment attribution).",
        "",
        "## Notes on the mic set",
        "",
        "- Filenames carry their original BWF origination as the wall time stamp.",
        "- TX02_MIC036 is the pre-hike file (David's 30-sec mic check is at the very start —",
        "  the loudest-window picker should land later, on whoever held it for the rest of the file).",
        "- TX00_MIC037 starts ~12:04, overlapping with TX00_MIC021 — these are concurrent sources,",
        "  almost certainly two DJI receivers running in parallel.",
    ]

    (out_dir / "MANIFEST.md").write_text("\n".join(manifest_lines), encoding="utf-8")
    print()
    print(f"Wrote {len(rows)} clips + MANIFEST.md")
    print(f"  Folder: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
