"""Phase A supplement: extract listen-test clips from TX01-prefix DJI files that
were never ingested into raw_sources.json. These were discovered while verifying
mic ownership — TX01_MIC020 (pre-hike parallel) and TX01_MIC024 (fills the
13:33-14:03 gap between MIC023 and MIC025).

For each, extract 3 clips: loudest-RMS window + 25% and 75% time positions, so
David can identify the wearer the same way as the existing 21 clips.

Output:
  working/<episode>/mic_audit/{NN}_{source_stem}__{tag}.wav   (NN = 22..27)
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import soundfile as sf


def find_loudest_window(src_path, window_sec, stride_sec, skip_start, skip_end):
    with sf.SoundFile(str(src_path)) as f:
        sr = f.samplerate
        total_sec = len(f) / sr
        window_samples = int(window_sec * sr)
        usable_end = total_sec - skip_end - window_sec
        if usable_end <= skip_start:
            return max(0.0, (total_sec - window_sec) / 2.0), 0.0
        best_start, best_rms = skip_start, -1.0
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
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    out_dir = ep_dir / "mic_audit"
    out_dir.mkdir(exist_ok=True)

    src_root = Path(r"C:\Users\david\Desktop\dji mic3")
    new_files = [
        ("TX01_MIC020_20260426_053310_orig.wav", "2026-04-26T11:33:10"),  # pre-hike
        ("TX01_MIC024_20260426_073311_orig.wav", "2026-04-26T13:33:11"),  # fills the gap
    ]

    manifest_lines = [
        "# Mic-ownership audit — uningested TX01 files",
        "",
        f"Episode: `{ep_dir.name}`",
        "",
        "These TWO files exist in the DJI dump folder but were never added to raw_sources.json.",
        "TX01_MIC020 is parallel to pre-hike (11:33). TX01_MIC024 fills the 13:33-14:03 gap.",
        "",
        "**For each clip, identify the wearer (whose mic is this?) the same way as the 21 prior clips.**",
        "",
        "| Clip | Source filename | BWF origination (UTC) | Position | Clip start (s) | Clip wall time (UTC) | Wearer (you fill in) | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]

    next_num = 22  # we already have 01-21
    for fname, bwf_iso in new_files:
        src_path = src_root / fname
        if not src_path.exists():
            print(f"  MISSING: {src_path}")
            continue

        info = sf.info(str(src_path))
        duration_sec = info.frames / info.samplerate
        bwf_dt = datetime.fromisoformat(bwf_iso)
        print(f"\n{fname}: dur={duration_sec:.0f}s, ch={info.channels}, sr={info.samplerate}")

        # 3 clips: loudest, 25%, 75%
        loudest_start, _ = find_loudest_window(
            src_path, args.snippet_sec, stride_sec=30.0, skip_start=30.0, skip_end=30.0
        )
        positions = [
            ("loud", loudest_start),
            ("pos25", max(0.0, 0.25 * duration_sec - args.snippet_sec / 2.0)),
            ("pos75", max(0.0, 0.75 * duration_sec - args.snippet_sec / 2.0)),
        ]

        for tag, start_sec in positions:
            start_sec = min(start_sec, max(0.0, duration_sec - args.snippet_sec))
            clip_wall = bwf_dt + timedelta(seconds=start_sec)
            clip_wall_str = clip_wall.strftime("%Y-%m-%dT%H:%M:%SZ")
            out_name = f"{next_num:02d}_{src_path.stem}__{tag}.wav"
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
                print(f"  ffmpeg error on {out_name}: {res.stderr[:200]}")
            else:
                print(f"  {out_name}: @ {start_sec:.0f}s ({tag})")
                manifest_lines.append(
                    f"| `{out_name}` | `{src_path.name}` | {bwf_iso} | {tag} | "
                    f"{start_sec:.0f} | {clip_wall_str} | _____ | _____ |"
                )
            next_num += 1

    manifest_lines += [
        "",
        "## Why this matters",
        "",
        "If TX01_MIC024 is **Chris's mic**, then Chris's mic actually recorded the 13:33-14:03",
        "stretch we thought was missing. Combined with MIC036 + MIC037 (also Chris's mic),",
        "Chris's mic might have been recording continuously the whole hike — we just hadn't",
        "ingested all the files. We'd have parallel mics for the entire hike instead of just 16 min.",
        "",
        "If TX01_MIC024 is **Josh's mic**, it's redundant with what we already have. Same for",
        "TX01_MIC020 vs TX02_MIC036.",
        "",
        "Either way: this answers whether RMS-based 2-mic attribution is viable for the full hike.",
    ]

    (out_dir / "MANIFEST_uningested.md").write_text("\n".join(manifest_lines), encoding="utf-8")
    print(f"\nWrote clips 22..{next_num - 1} + MANIFEST_uningested.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
