"""One-shot script: transcribe full Josh hike via A.2.

Builds RawSourceCollection from the canonical Josh hike sources
(Desktop/dji mic3 _orig.wav + Temp/josh-narrative/telegram MP3),
then runs per-source transcription with whisper-large-v3 local CPU.

Outputs:
    working/ep-2026-04-26-josh-cashman/raw_sources.json
    working/ep-2026-04-26-josh-cashman/transcripts/{sha16}.json + .txt

Usage:
    python scripts/run_a2_josh_hike.py [--model large-v3]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("a2-josh")

from contextpulse_pipeline.ingest import ingest_file  # noqa: E402
from contextpulse_pipeline.raw_source import RawSourceCollection  # noqa: E402
from contextpulse_pipeline.transcribe_per_source import transcribe_collection  # noqa: E402

CONTAINER = "ep-2026-04-26-josh-cashman"
WORKING_DIR = Path(f"C:/Users/david/Projects/ContextPulse/working/{CONTAINER}")

# Canonical raw sources for the 4-26 hike
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
    parser.add_argument(
        "--model",
        default="large-v3",
        help="Whisper model (large-v3 default; tiny/medium for faster dev)",
    )
    parser.add_argument(
        "--telegram-only",
        action="store_true",
        help="Skip DJI sources (faster smoke test)",
    )
    args = parser.parse_args()

    WORKING_DIR.mkdir(parents=True, exist_ok=True)

    # Ingest all sources
    sources = []
    if not args.telegram_only:
        for f in DJI_FILES:
            if not f.exists():
                logger.warning("Missing DJI file: %s", f)
                continue
            logger.info("Ingesting DJI: %s", f.name)
            sources.append(ingest_file(f, container=CONTAINER))

    for f in sorted(TELEGRAM_DIR.glob("*.mp3")):
        logger.info("Ingesting Telegram: %s", f.name)
        sources.append(ingest_file(f, container=CONTAINER))

    coll = RawSourceCollection(container=CONTAINER, sources=sources)
    raw_json = WORKING_DIR / "raw_sources.json"
    coll.to_json(path=raw_json)
    logger.info("Wrote %s (%d sources)", raw_json, len(sources))

    total_audio_sec = sum(s.duration_sec for s in sources)
    logger.info(
        "Total audio: %.1f hr across %d sources. Starting Whisper-%s on CPU...",
        total_audio_sec / 3600,
        len(sources),
        args.model,
    )

    t0 = time.time()
    transcripts = transcribe_collection(
        coll,
        WORKING_DIR / "transcripts",
        model=args.model,
        skip_existing=True,
    )
    elapsed = time.time() - t0
    logger.info(
        "Done. %d transcripts in %.1f min (RTF %.3f)",
        len(transcripts),
        elapsed / 60,
        elapsed / total_audio_sec if total_audio_sec else 0,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
