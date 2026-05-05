"""End-to-end local finish: timeline-gated isolation -> cross-source merge -> mastering.

Skips the GPU pipelines (Phase 1.5 + Stage 6 GPU) — assumes those have already run
and `working/<container>/fingerprint/unified_transcript_labeled.json` exists.

Usage:
    python scripts/run_local_pipeline_finish.py
    python scripts/run_local_pipeline_finish.py --container ep-2026-04-26-josh-cashman --top-k 3
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("local-finish")

DEFAULT_CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_ROOT = Path("C:/Users/david/Projects/ContextPulse/working")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--top-k", type=int, default=3, help="Top-k sources per speaker for isolation")
    parser.add_argument("--target-lufs", type=float, default=-16.0)
    parser.add_argument("--skip-isolation", action="store_true")
    parser.add_argument("--skip-merge", action="store_true")
    parser.add_argument("--skip-master", action="store_true")
    args = parser.parse_args()

    working = WORKING_ROOT / args.container
    raw_sources_path = working / "raw_sources.json"
    labeled_ut = working / "fingerprint" / "unified_transcript_labeled.json"
    sync_refined = working / "sync_result_refined.json"

    if not labeled_ut.exists():
        logger.error("Missing %s — run Phase 1.5 GPU pipeline first", labeled_ut)
        return 1
    if not raw_sources_path.exists():
        logger.error("Missing %s", raw_sources_path)
        return 1

    # Resolve audio paths from raw_sources.json
    import json

    rs = json.loads(raw_sources_path.read_text(encoding="utf-8"))
    audio_paths: dict[str, Path] = {}
    for s in rs["sources"]:
        p = Path(s["file_path"])
        if p.exists():
            audio_paths[s["sha256"]] = p
        else:
            logger.warning("Audio missing on disk: %s", p)
    logger.info("Resolved %d/%d audio sources locally", len(audio_paths), len(rs["sources"]))

    iso_dir = working / "voice_isolation_local"
    merge_dir = working / "merged"
    master_dir = working / "mastered"

    # ===== Stage 6: timeline-gated isolation =====
    if not args.skip_isolation:
        from contextpulse_pipeline.voice_isolation import (
            extract_per_speaker_tracks_from_timeline,
        )

        logger.info("=== Stage 6: timeline-gated isolation (top_k=%d) ===", args.top_k)
        t0 = time.time()
        iso = extract_per_speaker_tracks_from_timeline(
            audio_paths=audio_paths,
            labeled_unified_transcript_path=labeled_ut,
            raw_sources_path=raw_sources_path,
            sync_result_path=sync_refined if sync_refined.exists() else None,
            output_dir=iso_dir,
            container=args.container,
            top_k_sources_per_speaker=args.top_k if args.top_k > 0 else None,
        )
        iso.to_json(path=iso_dir / "isolation_result.json")
        logger.info(
            "Isolation: %d tracks across %d speakers in %.1f sec (skipped %d)",
            iso.n_tracks,
            len(iso.speakers),
            time.time() - t0,
            len(iso.skipped),
        )
    else:
        from contextpulse_pipeline.voice_isolation import IsolationResult

        iso = IsolationResult.from_json(path=iso_dir / "isolation_result.json") if False else None
        logger.info("Skipping isolation (using prior results from %s)", iso_dir)

    # ===== Cross-source merge =====
    if not args.skip_merge:
        from contextpulse_pipeline.cross_source_merger import merge_all_speakers
        from contextpulse_pipeline.sync_matcher import SyncResult
        from contextpulse_pipeline.voice_isolation import IsolationResult

        logger.info("=== Cross-source merge ===")
        t0 = time.time()
        # Reload IsolationResult if we skipped isolation
        if args.skip_isolation:
            iso_data = json.loads((iso_dir / "isolation_result.json").read_text(encoding="utf-8"))
            from contextpulse_pipeline.voice_isolation import IsolatedTrack

            iso = IsolationResult(
                container=iso_data["container"],
                tracks=[
                    IsolatedTrack(
                        speaker_label=t["speaker_label"],
                        source_sha256=t["source_sha256"],
                        source_filename=t["source_filename"],
                        source_tier=t["source_tier"],
                        output_path=Path(t["output_path"]),
                        duration_sec=float(t["duration_sec"]),
                        confidence=float(t.get("confidence", 0.0)),
                    )
                    for t in iso_data["tracks"]
                ],
                skipped=list(iso_data.get("skipped", [])),
            )
        # Load sync result for wall-clock anchors
        sync_path = sync_refined if sync_refined.exists() else (working / "sync_result.json")
        sync_obj = SyncResult.from_json(path=sync_path)
        merger = merge_all_speakers(iso, sync_obj, merge_dir)
        merger.to_json(path=merge_dir / "merger_result.json")
        logger.info(
            "Merge: %d unified tracks in %.1f sec",
            len(merger.tracks),
            time.time() - t0,
        )

    # ===== Stage 7: mastering =====
    if not args.skip_master:
        from contextpulse_pipeline.podcast_master import master_all_speakers

        logger.info("=== Stage 7: mastering (target=-%.1f LUFS) ===", abs(args.target_lufs))
        merge_inputs = {
            p.stem.replace("_unified", ""): p
            for p in merge_dir.glob("speaker_*_unified.wav")
        }
        if not merge_inputs:
            logger.error("No merged tracks found in %s — cannot master", merge_dir)
            return 2

        t0 = time.time()
        mastering = master_all_speakers(
            inputs=merge_inputs,
            output_dir=master_dir,
            container=args.container,
            target_lufs=args.target_lufs,
            use_deepfilternet=False,
        )
        mastering.to_json(path=master_dir / "mastering_result.json")
        logger.info("Master: %d tracks in %.1f sec", len(mastering.tracks), time.time() - t0)
        for t in mastering.tracks:
            logger.info(
                "  %s: in_LUFS=%s out_LUFS=%s -> %s",
                t.speaker_label,
                f"{t.measured_input_lufs:.2f}" if t.measured_input_lufs else "?",
                f"{t.measured_output_lufs:.2f}" if t.measured_output_lufs else "?",
                t.output_path.name,
            )

    logger.info("=== End-to-end finish complete for %s ===", args.container)
    logger.info("  isolation -> %s", iso_dir / "voice_isolation")
    logger.info("  merged -> %s", merge_dir)
    logger.info("  mastered -> %s", master_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
