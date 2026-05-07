"""Phase D3: extract listen-test snippets per ECAPA cluster.

For each cluster, pick runs whose middle chunks span different sources and
wall-time minutes (avoiding near-duplicate clips). Compute the true source
offset from wall_start_utc - source.bwf_origination, since the chunker's
source_relative_start_sec field is unreliable (resets to 0 on internal
segment boundaries).

Output: working/<episode>/cluster_audit/cluster_<label>_run<i>.wav
        working/<episode>/cluster_audit/MANIFEST.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path


def find_runs(member_indices: list[int]) -> list[list[int]]:
    members = sorted(member_indices)
    runs = []
    cur = [members[0]]
    for idx in members[1:]:
        if idx == cur[-1] + 1:
            cur.append(idx)
        else:
            runs.append(cur)
            cur = [idx]
    runs.append(cur)
    runs.sort(key=len, reverse=True)
    return runs


def pick_diverse_runs(runs: list[list[int]], chunks: list[dict], n: int) -> list[list[int]]:
    """Pick up to n runs with most-diverse (source, wall_minute) keys.

    Iterates from longest to shortest, accepting a run only if its (source, minute)
    is not already covered by an earlier accepted run.
    """
    picked = []
    seen_keys = set()
    for run in runs:
        if len(picked) >= n:
            break
        mid = chunks[run[len(run) // 2]]
        key = (mid["source_sha256"][:8], mid["wall_start_utc"][:16])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        picked.append(run)
    # If we ran out of diverse runs, fill with longest remaining (allow dup keys)
    if len(picked) < n:
        for run in runs:
            if run in picked:
                continue
            picked.append(run)
            if len(picked) >= n:
                break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--snippet-sec", type=float, default=10.0)
    ap.add_argument("--runs-per-cluster", type=int, default=3)
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    fp = json.loads((ep_dir / "fingerprint" / "fingerprint_result.json").read_text())
    raw = json.loads((ep_dir / "raw_sources.json").read_text())

    sha_to_path = {s["sha256"]: s["file_path"] for s in raw["sources"]}
    sha_to_dur = {s["sha256"]: float(s["duration_sec"]) for s in raw["sources"]}

    chunks_all = fp["chunks"]

    def resolve_origin(sha: str, raw_field: str | None) -> datetime | None:
        if raw_field:
            return datetime.fromisoformat(raw_field)
        # Fallback: phone-backup mp3s have no BWF/filename origination —
        # assume the file starts at the earliest chunk's wall time.
        wall_times = [
            datetime.fromisoformat(c["wall_start_utc"])
            for c in chunks_all if c["source_sha256"] == sha
        ]
        return min(wall_times) if wall_times else None

    sha_to_origin = {
        s["sha256"]: resolve_origin(s["sha256"], s.get("bwf_origination"))
        for s in raw["sources"]
    }

    out_dir = ep_dir / "cluster_audit"
    out_dir.mkdir(exist_ok=True)
    # Wipe stale snippets so the buggy ones don't linger
    for f in out_dir.glob("cluster_*.wav"):
        f.unlink()

    chunks = fp["chunks"]
    half = args.snippet_sec / 2.0

    manifest_lines = [
        "# Cluster audit snippets — Phase D3 (re-extracted with offset fix)",
        "",
        f"Episode: `{ep_dir.name}`",
        f"Snippet length: {args.snippet_sec:.0f} sec, centered on the run's middle chunk",
        "",
        "True source offset = `chunk.wall_start_utc - source.bwf_origination`.",
        "(The chunker's `source_relative_start_sec` field is unreliable — resets to 0 on internal segment boundaries.)",
        "",
        "Listen and write the speaker name (David / Chris / Josh / mixed / silence) in the right column.",
        "",
        "| Clip | Cluster | Run len | Wall time (UTC) | Source file | Source offset | Speaker (you fill in) |",
        "|---|---|---|---|---|---|---|",
    ]

    for cluster in fp["clusters"]:
        label = cluster["label"]
        all_runs = find_runs(cluster["member_indices"])
        runs = pick_diverse_runs(all_runs, chunks, args.runs_per_cluster)
        for i, run in enumerate(runs):
            mid_chunk = chunks[run[len(run) // 2]]
            sha = mid_chunk["source_sha256"]
            src_path = sha_to_path.get(sha)
            if src_path is None:
                print(f"  SKIP {label} run{i}: source {sha[:8]} not in raw_sources")
                continue

            wall = datetime.fromisoformat(mid_chunk["wall_start_utc"])
            origin = sha_to_origin[sha]
            true_offset = (wall - origin).total_seconds()
            seek_sec = max(0.0, true_offset + (mid_chunk["duration_sec"] / 2.0) - half)
            # Don't seek past end of file
            seek_sec = min(seek_sec, max(0.0, sha_to_dur[sha] - args.snippet_sec))

            out_name = f"cluster_{label}_run{i}.wav"
            out_path = out_dir / out_name

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{seek_sec:.2f}",
                "-i", src_path,
                "-t", f"{args.snippet_sec:.2f}",
                "-ac", "1", "-ar", "16000",
                str(out_path),
            ]
            print(f"  {out_name}: {Path(src_path).name} @ {seek_sec:.1f}s (run len={len(run)}, wall={wall.strftime('%H:%M:%S')})")
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"    ffmpeg error: {res.stderr[:200]}")
                continue

            manifest_lines.append(
                f"| `{out_name}` | {label} | {len(run)} | "
                f"{mid_chunk['wall_start_utc']} | {Path(src_path).name} | "
                f"{seek_sec:.1f}s | _____ |"
            )

    manifest_lines += [
        "",
        "## How to read results",
        "",
        "- All 3 clips of cluster X same name → cluster X = that person.",
        "- Clips of cluster X show different names → real clustering failure (re-cluster).",
        "- Same name across multiple clusters → speaker got split (merge clusters).",
        "- 'silence' or 'unknown' on cluster C → that cluster is a noise/low-energy bucket, not a real speaker.",
    ]

    (out_dir / "MANIFEST.md").write_text("\n".join(manifest_lines), encoding="utf-8")
    n_wav = sum(1 for f in out_dir.glob("cluster_*.wav"))
    print(f"\nWrote {out_dir / 'MANIFEST.md'}")
    print(f"Total: {n_wav} snippets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
