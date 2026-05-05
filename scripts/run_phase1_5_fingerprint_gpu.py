"""Entry point: ECAPA fingerprint a container via Phase 1.5 GPU spot pipeline.

Reads the existing raw_sources.json + unified_transcript.json from the
container's working/ directory and dispatches them to the GPU spot worker.
Assumes Phase 1 + 1.6 already ran (they produce these files locally).

Usage:
    python scripts/run_phase1_5_fingerprint_gpu.py
    python scripts/run_phase1_5_fingerprint_gpu.py --container ep-2026-04-26-josh-cashman --max-clusters 3
    python scripts/run_phase1_5_fingerprint_gpu.py --no-launch  # smoke: upload only

Pre-flight expectation:
    working/<container>/raw_sources.json
    working/<container>/unified_transcript.json
    (and the audio files referenced in raw_sources.json must still exist locally)
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phase1-5-runner")

DEFAULT_CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_ROOT = Path("C:/Users/david/Projects/ContextPulse/working")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help="Soft cap on cluster count (e.g. 3 for Josh hike: David, Chris, Josh)",
    )
    parser.add_argument("--distance-threshold", type=float, default=0.5)
    parser.add_argument("--min-chunk-sec", type=float, default=2.0)
    parser.add_argument("--target-chunk-sec", type=float, default=4.0)
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    working = WORKING_ROOT / args.container
    raw_sources = working / "raw_sources.json"
    unified_transcript = working / "unified_transcript.json"

    if not raw_sources.exists():
        logger.error("Missing %s — run Phase 1 transcribe first", raw_sources)
        return 1
    if not unified_transcript.exists():
        logger.error(
            "Missing %s — run Phase 1.6 unified transcript build first",
            unified_transcript,
        )
        return 1

    logger.info("Container=%s", args.container)
    logger.info("raw_sources=%s", raw_sources)
    logger.info("unified_transcript=%s", unified_transcript)

    cmd = [
        sys.executable,
        "-m",
        "contextpulse_pipeline.pipelines.phase1_5_fingerprint.submit",
        "--raw-sources",
        str(raw_sources),
        "--unified-transcript",
        str(unified_transcript),
        "--container",
        args.container,
        "--output-dir",
        str(working / "fingerprint"),
        "--distance-threshold",
        str(args.distance_threshold),
        "--min-chunk-sec",
        str(args.min_chunk_sec),
        "--target-chunk-sec",
        str(args.target_chunk_sec),
    ]
    if args.max_clusters is not None:
        cmd += ["--max-clusters", str(args.max_clusters)]
    if args.no_launch:
        cmd.append("--no-launch")
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
