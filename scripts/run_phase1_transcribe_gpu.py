"""Entry point: transcribe Josh hike (or any container) via Phase 1 GPU spot pipeline.

Usage:
    python scripts/run_phase1_transcribe_gpu.py
    python scripts/run_phase1_transcribe_gpu.py --container ep-2026-04-26-josh-cashman --no-launch
    python scripts/run_phase1_transcribe_gpu.py --smoke-test  # 1 source only
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phase1-runner")

from contextpulse_pipeline.ingest import ingest_file  # noqa: E402
from contextpulse_pipeline.raw_source import RawSourceCollection  # noqa: E402

CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_DIR = Path(f"C:/Users/david/Projects/ContextPulse/working/{CONTAINER}")

DJI_DIR = Path("C:/Users/david/Desktop/dji mic3")
DJI_FILES = [
    DJI_DIR / "TX00_MIC021_20260426_060311_orig.wav",
    DJI_DIR / "TX00_MIC022_20260426_063311_orig.wav",
    DJI_DIR / "TX00_MIC023_20260426_070311_orig.wav",
    DJI_DIR / "TX00_MIC025_20260426_080311_orig.wav",
    DJI_DIR / "TX00_MIC026_20260426_083311_orig.wav",
    DJI_DIR / "TX00_MIC037_20260426_060400_orig.wav",
    DJI_DIR / "TX02_MIC036_20260426_053400_orig.wav",
]
TELEGRAM_DIR = Path("C:/Users/david/AppData/Local/Temp/josh-narrative/telegram")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--smoke-test", action="store_true", help="Run on 1 small source only")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--no-poll", action="store_true")
    args = parser.parse_args()

    WORKING_DIR.mkdir(parents=True, exist_ok=True)

    sources = []
    if args.smoke_test:
        # Use the smallest Telegram chunk for fast smoke validation
        smoke_file = TELEGRAM_DIR / "final-1777215323203.mp3"  # ~14 min audio
        logger.info("Smoke test mode: ingesting only %s", smoke_file.name)
        sources.append(ingest_file(smoke_file, container=args.container))
    else:
        for f in DJI_FILES:
            if not f.exists():
                logger.warning("Missing DJI file: %s", f)
                continue
            logger.info("Ingesting DJI: %s", f.name)
            sources.append(ingest_file(f, container=args.container))
        for f in sorted(TELEGRAM_DIR.glob("*.mp3")):
            logger.info("Ingesting Telegram: %s", f.name)
            sources.append(ingest_file(f, container=args.container))

    coll = RawSourceCollection(container=args.container, sources=sources)
    raw_json = WORKING_DIR / "raw_sources.json"
    coll.to_json(path=raw_json)
    logger.info(
        "Wrote %s (%d sources, total %.1f hr audio)",
        raw_json,
        len(sources),
        sum(s.duration_sec for s in sources) / 3600,
    )

    # Delegate to submit.py
    cmd = [
        sys.executable,
        "-m",
        "contextpulse_pipeline.pipelines.phase1_transcribe.submit",
        "--raw-sources",
        str(raw_json),
        "--container",
        args.container,
        "--output-dir",
        str(WORKING_DIR / "transcripts"),
    ]
    if args.no_launch:
        cmd.append("--no-launch")
    if args.no_poll:
        cmd.append("--no-poll")
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
