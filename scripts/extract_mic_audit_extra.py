"""Phase A verification: extract 2 additional clips per DJI source at 25% and 75%
of each file's duration. Combined with the loudest-window clip already in mic_audit/,
this gives 3 samples per file so David can judge wearer-stability.

Numbering picks up at 08 (after the 7 loudest-window clips numbered 01-07). Order
is grouped by source: 08+09 = the two extras for the file labeled 01, 10+11 for
file 02, etc., so each row of the manifest stays adjacent.

Output:
  working/<episode>/mic_audit/{NN}_{source_stem}__{pos}.wav   (NN = 08..21)
  working/<episode>/mic_audit/MANIFEST_extra.md               (label these too)
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--snippet-sec", type=float, default=10.0)
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    raw = json.loads((ep_dir / "raw_sources.json").read_text(encoding="utf-8"))
    dji_sources = sorted(
        [s for s in raw["sources"] if s.get("bwf_origination")],
        key=lambda x: x["bwf_origination"],
    )

    out_dir = ep_dir / "mic_audit"
    out_dir.mkdir(exist_ok=True)
    # Wipe stale "extra" clips only (don't touch the 01-07 loudest-window clips)
    for f in list(out_dir.glob("*__pos25.wav")) + list(out_dir.glob("*__pos75.wav")):
        f.unlink()

    manifest_lines = [
        "# Mic-ownership verification — extra clips at 25% / 75% of each file",
        "",
        f"Episode: `{ep_dir.name}`",
        f"Snippet: {args.snippet_sec:.0f}s, picked at 25% and 75% time positions of each file.",
        "Combined with the loudest-window clips (01-07), each file has 3 samples for wearer-stability check.",
        "",
        "**Goal:** for each file, do all 3 samples sound like the same speaker (wearer-stable)",
        "or do they show different speakers within the file (mixed)?",
        "",
        "| Clip | Source filename | BWF origination (UTC) | Position | Clip start (s) | Clip wall time (UTC) | Speaker (you fill in) | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]

    next_num = 8  # we already have 01..07
    for s in dji_sources:
        src_path = Path(s["file_path"])
        if not src_path.exists():
            continue
        bwf = s["bwf_origination"]
        bwf_dt = datetime.fromisoformat(bwf)
        duration_sec = float(s["duration_sec"])

        for pos_label, frac in [("pos25", 0.25), ("pos75", 0.75)]:
            start_sec = max(0.0, frac * duration_sec - args.snippet_sec / 2.0)
            start_sec = min(start_sec, max(0.0, duration_sec - args.snippet_sec))
            clip_wall = bwf_dt + timedelta(seconds=start_sec)
            clip_wall_str = clip_wall.strftime("%Y-%m-%dT%H:%M:%SZ")

            out_name = f"{next_num:02d}_{src_path.stem}__{pos_label}.wav"
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
                next_num += 1
                continue

            print(f"  {out_name}: {src_path.name} @ {start_sec:.0f}s ({pos_label})")
            manifest_lines.append(
                f"| `{out_name}` | `{src_path.name}` | {bwf[:19]} | {pos_label} | "
                f"{start_sec:.0f} | {clip_wall_str} | _____ | _____ |"
            )
            next_num += 1

    manifest_lines += [
        "",
        "## What we're testing",
        "",
        "ECAPA cluster analysis on the DJI-only re-clustering shows:",
        "- **MIC021 (12:03 hike start)** splits across 3 clusters (58% / 28% / 9%) — multiple acoustic signatures within one file.",
        "- **MIC022 (12:33)** splits 64% / 30% — also multi-acoustic.",
        "- **MIC023, MIC025, MIC037** are single-cluster (clean) — likely wearer-stable.",
        "- **MIC036 (pre-hike), MIC026** are mostly-single-cluster (90% / 87%) — minor secondary content.",
        "",
        "If a file's 3 clips all sound like the same speaker → wearer-stable, multiple clusters",
        "are just acoustic variation (wind, body movement, environmental change).",
        "",
        "If a file's 3 clips show different speakers → file contains mixed audio (e.g., one phone",
        "received from both transmitters via Bluetooth, or some kind of merging happened).",
        "That outcome pushes us off RMS-based attribution and onto Path B (content-only).",
    ]

    (out_dir / "MANIFEST_extra.md").write_text("\n".join(manifest_lines), encoding="utf-8")
    print(f"\nWrote {next_num - 8} extra clips + MANIFEST_extra.md")
    print(f"  Folder: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
