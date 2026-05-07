"""Voice-separability diagnostic.

Pulls chunk embeddings from fingerprint_result_dji_only.json and groups them
by David's listen-test labels. Computes pairwise cosine distance between
group centroids to answer: can ECAPA tell speakers apart across mics?

Anchor groups (from David's labels):
  chris_on_MIC037_a   ← run1 area (~12:16:52 wall, 5 min Chris on his own mic)
  chris_on_MIC037_b   ← run2 area (~12:18:34 wall, 3.7 min Chris on his own mic)
  chris_on_MIC036     ← C0/C1/C2 area on MIC036 (~11:40-41 wall, Chris on a different mic)
  josh_on_MIC025      ← B0 run area (~14:20 wall, 15 min Josh on his own mic)
  david_on_MIC036     ← first ~30 sec of MIC036 (David testing the mic)

Read the diagonal as same-speaker-same-mic (should be ~0.0-0.2).
Read off-diagonals as:
  - same speaker different mic (e.g. chris_MIC037 vs chris_MIC036)  ← KEY TEST
  - different speaker different mic (e.g. chris_MIC037 vs josh_MIC025)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


EP_DIR = Path("working/ep-2026-04-26-josh-cashman")

# Source SHAs from raw_sources.json
SHA_MIC037 = "a2fbc25993f55bd5cf4ea1a03f398e359108e234824e3b92aaffe071e678d6d0"
SHA_MIC036 = "eedec03ad67a5759fe48523031ad7d1600ad1f269c1d15631f106cab4c2b2864"
SHA_MIC025 = "49c3cd844e9225d47e801f8f387ce7532c08a76e58d55c4ebdf98c56ade7c58f"

# Anchor windows: (sha, center_wall_utc, half_width_sec)
ANCHORS = [
    ("chris_on_MIC037_a", SHA_MIC037, "2026-04-26T12:16:52+00:00", 90),
    ("chris_on_MIC037_b", SHA_MIC037, "2026-04-26T12:18:34+00:00", 90),
    ("chris_on_MIC036",   SHA_MIC036, "2026-04-26T11:40:43+00:00", 60),
    ("josh_on_MIC025",    SHA_MIC025, "2026-04-26T14:20:05+00:00", 90),
    ("david_on_MIC036",   SHA_MIC036, "2026-04-26T11:34:15+00:00", 15),  # first 30 sec
]


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def main() -> None:
    fp = json.loads((EP_DIR / "fingerprint" / "fingerprint_result_dji_only.json").read_text())
    chunks = fp["chunks"]

    # Pre-parse wall times once
    chunk_walls = [parse_iso(c["wall_start_utc"]) for c in chunks]

    centroids = {}
    sample_counts = {}
    for name, sha, center_iso, half in ANCHORS:
        center = parse_iso(center_iso)
        lo = center - timedelta(seconds=half)
        hi = center + timedelta(seconds=half)
        member_idx = [
            i for i, c in enumerate(chunks)
            if c["source_sha256"] == sha and lo <= chunk_walls[i] <= hi
        ]
        if not member_idx:
            print(f"  {name}: NO chunks in window — skipping")
            continue
        embs = np.array([chunks[i]["embedding"] for i in member_idx], dtype=np.float32)
        centroid = embs.mean(axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-12)
        centroids[name] = centroid
        sample_counts[name] = len(member_idx)
        print(f"  {name}: {len(member_idx)} chunks (~{len(member_idx)*4} sec audio)")

    # Pairwise cosine distance matrix
    names = list(centroids.keys())
    print()
    print("Cosine distance matrix (lower = more similar; ECAPA same-speaker ~0.1-0.3):")
    print()
    header = "                          " + "  ".join(f"{n[:14]:>14}" for n in names)
    print(header)
    for n1 in names:
        row = [f"{n1[:24]:24}"]
        for n2 in names:
            if n1 == n2:
                row.append(f"{'-':>14}")
            else:
                # cosine distance = 1 - cosine similarity, both unit-norm so just dot
                cos_sim = float(np.dot(centroids[n1], centroids[n2]))
                cos_dist = 1.0 - cos_sim
                row.append(f"{cos_dist:>14.3f}")
        print("  ".join(row))

    print()
    print("Interpretation:")
    print("  chris_on_MIC037_a  vs  chris_on_MIC037_b  → same-speaker same-mic baseline")
    print("  chris_on_MIC037_a  vs  chris_on_MIC036    → same-speaker DIFFERENT mic (KEY TEST)")
    print("  chris_on_MIC037_a  vs  josh_on_MIC025     → different speaker different mic")
    print("  david_on_MIC036    vs  chris_on_MIC036    → same-mic different speaker (interesting)")
    print()
    print("If same-speaker-different-mic ≤ different-speaker-different-mic, voice enrollment will work.")
    print("If they're comparable, mic acoustics dominate and voice enrollment alone won't separate speakers.")


if __name__ == "__main__":
    main()
