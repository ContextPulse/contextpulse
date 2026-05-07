"""Filter to DJI-only sources and re-run ECAPA clustering.

The original Phase 1.5 fingerprinting ingested 14 sources, but 7 of them
(under Temp\\josh-narrative\\telegram\\) were leftover output from a prior
pipeline attempt — not real source audio. They contaminated 63% of chunks
and 67% of transcript segments, distorting the cluster boundaries.

This script:
  1. Filters fingerprint_result.json chunks to DJI-only (TX00/TX02 .wav)
  2. Re-clusters the filtered embeddings (same Agglomerative + cosine + 0.5)
  3. Filters unified_transcript_labeled.json to matching DJI-only segments
     (re-stamping speaker_label from the new clusters where possible)
  4. Saves to fingerprint/fingerprint_result_dji_only.json + matching transcript

Output is a parallel pair of files; original artifacts are untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.cluster import AgglomerativeClustering


DJI_PATH_HINT = "dji mic3"  # case-insensitive substring on file_path
TELEGRAM_PATH_HINT = "telegram"
DISTANCE_THRESHOLD = 0.5  # match speaker_fingerprint.DEFAULT_DISTANCE_THRESHOLD


def is_dji_source(file_path: str) -> bool:
    p = file_path.lower()
    if TELEGRAM_PATH_HINT in p:
        return False
    return DJI_PATH_HINT in p


def cluster_embeddings(emb_matrix: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative clustering, returns list-of-member-index-lists sorted by size desc."""
    if len(emb_matrix) < 2:
        return [list(range(len(emb_matrix)))]
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=threshold,
    )
    labels = clusterer.fit_predict(emb_matrix)
    by_label: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        by_label.setdefault(int(lab), []).append(i)
    return sorted(by_label.values(), key=len, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--threshold", type=float, default=DISTANCE_THRESHOLD)
    args = ap.parse_args()

    ep = Path(args.episode_dir)
    raw = json.loads((ep / "raw_sources.json").read_text())
    fp = json.loads((ep / "fingerprint" / "fingerprint_result.json").read_text())
    ut = json.loads((ep / "fingerprint" / "unified_transcript_labeled.json").read_text())

    # Identify DJI sources
    dji_shas = {s["sha256"] for s in raw["sources"] if is_dji_source(s["file_path"])}
    tg_shas = {s["sha256"] for s in raw["sources"] if not is_dji_source(s["file_path"])}
    print(f"Sources: {len(dji_shas)} DJI / {len(tg_shas)} Telegram")
    if not dji_shas:
        print("No DJI sources found — abort.")
        return 1

    # Filter chunks
    chunks_all = fp["chunks"]
    keep_idx = [i for i, c in enumerate(chunks_all) if c["source_sha256"] in dji_shas]
    drop_idx = [i for i, c in enumerate(chunks_all) if c["source_sha256"] in tg_shas]
    print(f"Chunks: {len(keep_idx)} keep / {len(drop_idx)} drop (of {len(chunks_all)} total)")
    if not keep_idx:
        print("No DJI chunks — abort.")
        return 1

    kept_chunks = [chunks_all[i] for i in keep_idx]
    embeddings = np.array([c["embedding"] for c in kept_chunks], dtype=np.float32)
    print(f"Embedding matrix: {embeddings.shape}")

    # Re-cluster
    member_lists = cluster_embeddings(embeddings, args.threshold)
    print(f"Re-cluster (threshold={args.threshold}): {len(member_lists)} clusters")
    new_labels = [f"speaker_{chr(ord('A') + i)}" for i in range(len(member_lists))]

    # Build output clusters (member indices are into kept_chunks)
    new_clusters = []
    chunk_to_label: dict[int, str] = {}  # global chunk index -> new label
    for label, members in zip(new_labels, member_lists):
        global_members = [keep_idx[m] for m in members]  # back to global chunk index
        for gi in global_members:
            chunk_to_label[gi] = label
        # cluster members in OUTPUT file are indices into the FILTERED chunks list
        new_clusters.append({
            "label": label,
            "size": len(members),
            "member_indices": members,
        })
        # Per-source distribution within cluster
        src_counter = Counter(chunks_all[gi]["source_sha256"][:8] for gi in global_members)
        top_src = ", ".join(f"{k}={v}" for k, v in src_counter.most_common(4))
        print(f"  {label}: {len(members)} chunks  sources: {top_src}")

    # Save filtered fingerprint result
    out_fp = {
        "n_chunks": len(kept_chunks),
        "n_clusters": len(new_clusters),
        "filter": "dji-only",
        "distance_threshold": args.threshold,
        "chunks": kept_chunks,
        "clusters": new_clusters,
    }
    out_path_fp = ep / "fingerprint" / "fingerprint_result_dji_only.json"
    out_path_fp.write_text(json.dumps(out_fp))
    print(f"\nWrote {out_path_fp}")

    # Re-stamp speaker labels on transcript segments using the new mapping.
    # Strategy: for each segment, find the chunk with matching source_sha256
    # whose wall_start_utc is closest to the segment's wall_start_utc.
    # (Re-uses the same "closest same-source chunk" logic as assign_speakers_to_unified.)
    from datetime import datetime
    def parse_iso(s):
        return datetime.fromisoformat(s)

    # Bucket chunks by source for fast same-source lookup
    by_source: dict[str, list[tuple[float, int]]] = {}  # sha -> [(wall_start_epoch, global_idx)]
    for gi, c in enumerate(chunks_all):
        if gi in chunk_to_label:
            ts = parse_iso(c["wall_start_utc"]).timestamp()
            by_source.setdefault(c["source_sha256"], []).append((ts, gi))
    for v in by_source.values():
        v.sort()

    new_segments = []
    skipped = 0
    relabeled = 0
    for seg in ut["segments"]:
        if seg["source_sha256"] not in dji_shas:
            skipped += 1
            continue
        seg_ts = parse_iso(seg["wall_start_utc"]).timestamp()
        candidates = by_source.get(seg["source_sha256"], [])
        if not candidates:
            new_segments.append(seg)
            continue
        # Binary search would be faster but linear is fine at this scale
        best = min(candidates, key=lambda x: abs(x[0] - seg_ts))
        new_label = chunk_to_label.get(best[1])
        if new_label and new_label != seg.get("speaker_label"):
            relabeled += 1
        seg2 = dict(seg)
        seg2["speaker_label"] = new_label or seg.get("speaker_label")
        new_segments.append(seg2)

    out_ut = {
        "container": ut["container"],
        "anchor_origination_utc": ut["anchor_origination_utc"],
        "n_segments": len(new_segments),
        "n_sources": len(dji_shas),
        "filter": "dji-only",
        "segments": new_segments,
    }
    out_path_ut = ep / "fingerprint" / "unified_transcript_labeled_dji_only.json"
    out_path_ut.write_text(json.dumps(out_ut))
    print(f"Wrote {out_path_ut}")
    print(f"Transcript: kept {len(new_segments)} / dropped {skipped} / re-stamped {relabeled} segments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
