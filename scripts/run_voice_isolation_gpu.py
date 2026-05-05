"""Entry point: run Stage 6 voice isolation on a container's audio.

Pre-flight expectation:
    working/<container>/raw_sources.json
    working/<container>/fingerprint/fingerprint_result.json   (Phase 1.5 output)

Usage:
    python scripts/run_voice_isolation_gpu.py
    python scripts/run_voice_isolation_gpu.py --container ep-2026-04-26-josh-cashman
    python scripts/run_voice_isolation_gpu.py --no-launch  # smoke: upload only
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voice-isolation-runner")

DEFAULT_CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_ROOT = Path("C:/Users/david/Projects/ContextPulse/working")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument(
        "--model-source",
        default="Wespeaker/wespeaker-voxceleb-resnet34",
    )
    parser.add_argument(
        "--top-k-sources-per-speaker",
        type=int,
        default=3,
        help="Only extract on the K sources with the most chunks per speaker (default 3, set to 0 for all)",
    )
    args = parser.parse_args()

    working = WORKING_ROOT / args.container
    raw_sources = working / "raw_sources.json"
    fingerprint = working / "fingerprint" / "fingerprint_result.json"
    output_dir = working / "voice_isolation"

    if not raw_sources.exists():
        logger.error("Missing %s — run Phase 1 transcribe first", raw_sources)
        return 1
    if not fingerprint.exists():
        logger.error("Missing %s — run Phase 1.5 fingerprinting first", fingerprint)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "contextpulse_pipeline.pipelines.voice_isolation.submit",
        "--raw-sources",
        str(raw_sources),
        "--fingerprint-result",
        str(fingerprint),
        "--container",
        args.container,
        "--output-dir",
        str(output_dir),
        "--model-source",
        args.model_source,
    ]
    if args.top_k_sources_per_speaker > 0:
        cmd += ["--top-k-sources-per-speaker", str(args.top_k_sources_per_speaker)]
    if args.no_launch:
        cmd.append("--no-launch")
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
