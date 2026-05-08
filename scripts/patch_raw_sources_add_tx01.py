"""One-shot patch: add the two TX01-prefix DJI files (TX01_MIC020 and TX01_MIC024)
to raw_sources.json. They were silently dropped by the original ingest filter
which only matched TX00 + TX02 prefixes.

Backs up the existing raw_sources.json before patching.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf


CONTAINER = "ep-2026-04-26-josh-cashman"
RAW_SOURCES = Path(f"working/{CONTAINER}/raw_sources.json")
BACKUP = Path(f"working/{CONTAINER}/raw_sources.before-tx01-patch.json")
DJI_DUMP = Path(r"C:\Users\david\Desktop\dji mic3")

NEW_FILES = [
    # (filename, bwf_origination_iso, filename_origination_iso)
    ("TX01_MIC020_20260426_053310_orig.wav", "2026-04-26T11:33:10Z", "2026-04-26T11:33:10Z"),
    ("TX01_MIC024_20260426_073311_orig.wav", "2026-04-26T13:33:11Z", "2026-04-26T13:33:11Z"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    raw = json.loads(RAW_SOURCES.read_text(encoding="utf-8"))
    existing_paths = {s["file_path"] for s in raw["sources"]}
    existing_sha = {s["sha256"] for s in raw["sources"]}

    print(f"Loaded {len(raw['sources'])} existing sources")
    shutil.copy2(RAW_SOURCES, BACKUP)
    print(f"Backup: {BACKUP}")

    added = 0
    for fname, bwf_iso, fn_iso in NEW_FILES:
        path = DJI_DUMP / fname
        if not path.exists():
            print(f"  MISSING: {path}")
            continue
        if str(path) in existing_paths:
            print(f"  SKIP (already in sources): {fname}")
            continue

        print(f"  + {fname}: hashing...", end=" ", flush=True)
        sha = sha256_file(path)
        if sha in existing_sha:
            print(f"already present under different path (sha {sha[:8]}); skipping")
            continue

        info = sf.info(str(path))
        # All DJI WAVs we've seen are pcm_f32le 32-bit
        codec = "pcm_f32le" if info.subtype == "FLOAT" else info.subtype.lower()
        bit_depth = 32 if info.subtype == "FLOAT" else 16

        new_src = {
            "sha256": sha,
            "file_path": str(path),
            "container": CONTAINER,
            "source_tier": "A",
            "duration_sec": info.frames / info.samplerate,
            "sample_rate": info.samplerate,
            "channel_count": info.channels,
            "codec": codec,
            "bit_depth": bit_depth,
            "bwf_origination": bwf_iso,
            "filename_origination": fn_iso,
            "provenance": "bwf",
        }
        raw["sources"].append(new_src)
        added += 1
        print(f"sha={sha[:8]} dur={new_src['duration_sec']:.0f}s ch={info.channels}")

    if added > 0:
        RAW_SOURCES.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(f"\nWrote {RAW_SOURCES} (now {len(raw['sources'])} sources, +{added} added)")
    else:
        print("\nNo additions made; raw_sources.json unchanged")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
