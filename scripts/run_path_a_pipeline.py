"""Path A end-to-end finish: Tier 1 -> speaker-gated split -> cross-source merge -> Stage 7 master.

Per ``enhancing-audio-for-podcasts`` skill, this is the standard recommended
podcast workflow:
  1. Tier 1 per source: HPF @ 80 Hz + afftdn (-25 dB nf) + loudnorm to -23 LUFS
  2. Speaker-gated split per (speaker, source) using Phase 1.5's labeled transcript
  3. Cross-source merge with tier weights (DJI A=1.0 > phone B=0.7 > Telegram C=0.4)
  4. Stage 7 master at -16 LUFS with the full chain (HPF/denoise/de-ess/comp/EQ/limiter/loudnorm)

No GPU. No spot. Runs locally on CPU. Uses Phase 1.5 outputs already on disk.

Pre-flight expectation:
    working/<container>/raw_sources.json
    working/<container>/sync_result_refined.json     (A.3b output)
    working/<container>/fingerprint/unified_transcript_labeled.json  (Phase 1.5)

Usage:
    python scripts/run_path_a_pipeline.py
    python scripts/run_path_a_pipeline.py --container ep-2026-04-26-josh-cashman
    python scripts/run_path_a_pipeline.py --skip-tier1   # if tier1 already cached
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("path-a")

DEFAULT_CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_ROOT = Path("C:/Users/david/Projects/ContextPulse/working")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--top-k", type=int, default=3, help="Top-k sources per speaker")
    parser.add_argument("--target-lufs", type=float, default=-16.0, help="Master target LUFS")
    parser.add_argument("--tier1-target-lufs", type=float, default=-23.0)
    parser.add_argument("--skip-tier1", action="store_true")
    parser.add_argument("--skip-isolation", action="store_true")
    parser.add_argument("--skip-merge", action="store_true")
    parser.add_argument("--skip-master", action="store_true")
    args = parser.parse_args()

    working = WORKING_ROOT / args.container
    raw_sources_path = working / "raw_sources.json"
    labeled_ut = working / "fingerprint" / "unified_transcript_labeled.json"
    sync_refined = working / "sync_result_refined.json"

    for p, name in [(raw_sources_path, "raw_sources.json"), (labeled_ut, "unified_transcript_labeled.json (Phase 1.5)")]:
        if not p.exists():
            logger.error("Missing %s — aborting", p)
            return 1

    rs = json.loads(raw_sources_path.read_text(encoding="utf-8"))
    raw_paths: dict[str, Path] = {}
    for s in rs["sources"]:
        p = Path(s["file_path"])
        if p.exists():
            raw_paths[s["sha256"]] = p
        else:
            logger.warning("Audio missing on disk: %s", p)
    logger.info("Resolved %d/%d raw audio sources locally", len(raw_paths), len(rs["sources"]))

    tier1_dir = working / "tier1"
    iso_dir = working / "voice_isolation_path_a"
    merge_dir = working / "merged_path_a"
    master_dir = working / "mastered_path_a"
    summary_path = working / "path_a_summary.json"
    summary: dict[str, object] = {"container": args.container, "stages": {}}

    # ===== Stage 1: Tier 1 cleanup per source =====
    cleaned_paths: dict[str, Path] = {}
    if not args.skip_tier1:
        from contextpulse_pipeline.tier1_clean import clean_collection

        logger.info("=== Tier 1: HPF + afftdn + loudnorm to %.1f LUFS per source ===", args.tier1_target_lufs)
        t0 = time.time()
        tier1_result = clean_collection(
            audio_paths=raw_paths,
            output_dir=tier1_dir,
            container=args.container,
            target_lufs=args.tier1_target_lufs,
            sample_rate=16000,
            measure_lufs_each=False,  # speed: skip per-file LUFS measurement
        )
        tier1_result.to_json(path=tier1_dir / "tier1_result.json")
        cleaned_paths = tier1_result.cleaned_paths()
        elapsed = time.time() - t0
        logger.info(
            "Tier 1 done: %d cleaned, %d skipped in %.1f sec",
            len(tier1_result.cleaned),
            len(tier1_result.skipped),
            elapsed,
        )
        summary["stages"]["tier1"] = {
            "cleaned_count": len(tier1_result.cleaned),
            "skipped_count": len(tier1_result.skipped),
            "elapsed_sec": elapsed,
        }
    else:
        # Reload from disk
        if (tier1_dir / "tier1_result.json").exists():
            cached = json.loads((tier1_dir / "tier1_result.json").read_text(encoding="utf-8"))
            for c in cached["cleaned"]:
                cleaned_paths[c["sha256"]] = Path(c["output_path"])
            logger.info("Tier 1 skipped — using %d cached cleaned sources", len(cleaned_paths))
        else:
            logger.error("--skip-tier1 set but no cached tier1_result.json found")
            return 2

    # ===== Stage 2: speaker-gated split (cleaned audio in, per-(speaker,source) tracks out) =====
    if not args.skip_isolation:
        from contextpulse_pipeline.voice_isolation import (
            extract_per_speaker_tracks_from_timeline,
        )

        logger.info("=== Stage 2: speaker-gated split (top_k=%d) on Tier-1 cleaned audio ===", args.top_k)
        t0 = time.time()
        # NOTE: passing CLEANED audio paths, not raw. The skill's Tier 1 ran first.
        iso = extract_per_speaker_tracks_from_timeline(
            audio_paths=cleaned_paths,
            labeled_unified_transcript_path=labeled_ut,
            raw_sources_path=raw_sources_path,
            sync_result_path=sync_refined if sync_refined.exists() else None,
            output_dir=iso_dir,
            container=args.container,
            top_k_sources_per_speaker=args.top_k if args.top_k > 0 else None,
        )
        iso.to_json(path=iso_dir / "isolation_result.json")
        elapsed = time.time() - t0
        logger.info(
            "Speaker-gated split: %d tracks across %d speakers in %.1f sec",
            iso.n_tracks,
            len(iso.speakers),
            elapsed,
        )
        summary["stages"]["speaker_gated_split"] = {
            "n_tracks": iso.n_tracks,
            "speakers": iso.speakers,
            "elapsed_sec": elapsed,
        }
    else:
        logger.info("Speaker-gated split skipped — using cached isolation_result.json")

    # ===== Stage 3: cross-source merge =====
    if not args.skip_merge:
        from contextpulse_pipeline.cross_source_merger import merge_all_speakers
        from contextpulse_pipeline.sync_matcher import SyncResult
        from contextpulse_pipeline.voice_isolation import IsolatedTrack, IsolationResult

        logger.info("=== Stage 3: cross-source merge (tier weights A=1.0/B=0.7/C=0.4) ===")
        t0 = time.time()
        # Reload IsolationResult from disk (works whether we just wrote it or skipped iso)
        iso_data = json.loads((iso_dir / "isolation_result.json").read_text(encoding="utf-8"))
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
        sync_path = sync_refined if sync_refined.exists() else (working / "sync_result.json")
        sync_obj = SyncResult.from_json(path=sync_path)
        merger = merge_all_speakers(iso, sync_obj, merge_dir)
        merger.to_json(path=merge_dir / "merger_result.json")
        elapsed = time.time() - t0
        logger.info("Merge done: %d unified tracks in %.1f sec", len(merger.tracks), elapsed)
        summary["stages"]["merge"] = {
            "tracks": [
                {
                    "speaker": t.speaker_label,
                    "duration_sec": t.duration_sec,
                    "n_regions": t.n_regions,
                    "n_source_switches": t.n_source_switches,
                }
                for t in merger.tracks
            ],
            "elapsed_sec": elapsed,
        }

    # ===== Stage 4: Stage 7 mastering at -16 LUFS =====
    if not args.skip_master:
        from contextpulse_pipeline.podcast_master import master_all_speakers

        logger.info("=== Stage 4: master to %.1f LUFS ===", args.target_lufs)
        merge_inputs = {
            p.stem.replace("_unified", ""): p
            for p in merge_dir.glob("speaker_*_unified.wav")
        }
        if not merge_inputs:
            logger.error("No merged tracks found in %s", merge_dir)
            return 3
        t0 = time.time()
        mastering = master_all_speakers(
            inputs=merge_inputs,
            output_dir=master_dir,
            container=args.container,
            target_lufs=args.target_lufs,
            use_deepfilternet=False,  # afftdn fallback per skill 2026-05-02 rule
        )
        mastering.to_json(path=master_dir / "mastering_result.json")
        elapsed = time.time() - t0
        logger.info("Master done: %d tracks in %.1f sec", len(mastering.tracks), elapsed)
        for t in mastering.tracks:
            logger.info(
                "  %s: in_LUFS=%s out_LUFS=%s -> %s",
                t.speaker_label,
                f"{t.measured_input_lufs:.2f}" if t.measured_input_lufs is not None else "?",
                f"{t.measured_output_lufs:.2f}" if t.measured_output_lufs is not None else "?",
                t.output_path.name,
            )
        summary["stages"]["master"] = {
            "tracks": [
                {
                    "speaker": t.speaker_label,
                    "in_lufs": t.measured_input_lufs,
                    "out_lufs": t.measured_output_lufs,
                    "target_lufs": t.target_lufs,
                    "output_path": str(t.output_path),
                }
                for t in mastering.tracks
            ],
            "elapsed_sec": elapsed,
        }

    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info("=== Path A complete for %s ===", args.container)
    logger.info("  Summary: %s", summary_path)
    logger.info("  Tier 1: %s", tier1_dir)
    logger.info("  Speaker-gated: %s", iso_dir / "voice_isolation")
    logger.info("  Merged: %s", merge_dir)
    logger.info("  Mastered: %s", master_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
