# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Multi-source audio ingest — produces RawSource / RawSourceCollection.

ingest_file(path) reads a single audio file, computes streaming SHA256, probes
format via ffprobe, parses BWF bext (if present) and filename timestamp (if
parseable), and returns a RawSource.

ingest_directory walks a directory and returns a RawSourceCollection.

Timezone: BWF bext and filename timestamps are written in the recording
device's local time (no embedded tz). The caller passes source_timezone
(default 'America/Denver') and ingest converts to tz-aware UTC.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from contextpulse_pipeline.bwf import read_bext
from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_TIMEZONE = "America/Denver"
SHA256_CHUNK_BYTES = 65536  # 64 KB streaming chunks

# Default tier mapping by extension — overridable via tier kwarg
_TIER_BY_EXT: dict[str, str] = {
    ".wav": "A",
    ".m4a": "B",
    ".mp3": "C",
}

_AUDIO_EXTENSIONS = frozenset(_TIER_BY_EXT.keys())

# Filename pattern for embedded timestamps: ...YYYYMMDD_HHMMSS...
# Matches DJI naming (TX00_MIC021_20260426_060311_orig.wav) and similar.
_FILENAME_TIMESTAMP_RE = re.compile(r"(?P<date>\d{8})_(?P<time>\d{6})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_filename_timestamp(filename: str, source_timezone: str) -> datetime | None:
    """Parse `YYYYMMDD_HHMMSS` pattern from filename → tz-aware UTC datetime.

    Returns None if no parseable timestamp is found.
    """
    match = _FILENAME_TIMESTAMP_RE.search(filename)
    if match is None:
        return None
    raw_date = match.group("date")
    raw_time = match.group("time")
    try:
        naive = datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H%M%S")
    except ValueError:
        return None
    try:
        tz = ZoneInfo(source_timezone)
    except Exception:
        logger.warning("Unknown source_timezone %r, falling back to UTC", source_timezone)
        tz = timezone.utc
    local = naive.replace(tzinfo=tz)
    return local.astimezone(timezone.utc)


def _streaming_sha256(path: Path) -> str:
    """SHA256 a file in fixed-size chunks (no full-file load)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(SHA256_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _ffprobe_audio(path: Path) -> dict[str, object]:
    """Run ffprobe and return duration, sample_rate, channels, codec, bit_depth.

    Raises RuntimeError if ffprobe fails or returns no audio stream.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-select_streams",
        "a:0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path.name}: {result.stderr.decode(errors='replace')[:200]}"
        )
    parsed = json.loads(result.stdout)
    streams = parsed.get("streams", [])
    fmt = parsed.get("format", {})
    if not streams:
        raise RuntimeError(f"ffprobe returned no audio stream for {path.name}")
    s = streams[0]
    duration = float(s.get("duration") or fmt.get("duration") or 0.0)

    bit_depth_raw = s.get("bits_per_raw_sample") or s.get("bits_per_sample")
    bit_depth: int | None
    try:
        bit_depth = int(bit_depth_raw) if bit_depth_raw else None
    except (TypeError, ValueError):
        bit_depth = None
    if bit_depth == 0:
        bit_depth = None

    return {
        "duration_sec": duration,
        "sample_rate": int(s.get("sample_rate", 0)),
        "channel_count": int(s.get("channels", 0)),
        "codec": str(s.get("codec_name", "")),
        "bit_depth": bit_depth,
    }


def _default_tier_from_ext(path: Path) -> str:
    return _TIER_BY_EXT.get(path.suffix.lower(), "C")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ingest_file(
    path: Path,
    container: str,
    *,
    tier: str | None = None,
    source_timezone: str = DEFAULT_SOURCE_TIMEZONE,
) -> RawSource:
    """Ingest a single audio file → RawSource.

    Reads sha256 (streaming), ffprobe format/duration/sr/channels, BWF bext
    (if WAV with bext chunk), and parses filename timestamp pattern
    `YYYYMMDD_HHMMSS` if present.
    """
    sha = _streaming_sha256(path)
    probe = _ffprobe_audio(path)

    bwf_origination_utc: datetime | None = None
    if path.suffix.lower() == ".wav":
        bwf = read_bext(path)
        if bwf is not None:
            try:
                tz = ZoneInfo(source_timezone)
            except Exception:
                logger.warning("Unknown source_timezone %r, falling back to UTC", source_timezone)
                tz = timezone.utc
            bwf_origination_utc = bwf.naive_datetime.replace(tzinfo=tz).astimezone(timezone.utc)

    filename_origination_utc = parse_filename_timestamp(path.name, source_timezone)

    if bwf_origination_utc is not None:
        provenance = "bwf"
    elif filename_origination_utc is not None:
        provenance = "filename"
    else:
        provenance = "none"

    return RawSource(
        sha256=sha,
        file_path=str(path),
        container=container,
        source_tier=tier or _default_tier_from_ext(path),
        duration_sec=float(probe["duration_sec"]),
        sample_rate=int(probe["sample_rate"]),  # type: ignore[arg-type]
        channel_count=int(probe["channel_count"]),  # type: ignore[arg-type]
        codec=str(probe["codec"]),
        bit_depth=probe["bit_depth"],  # type: ignore[arg-type]
        bwf_origination=bwf_origination_utc,
        filename_origination=filename_origination_utc,
        provenance=provenance,
    )


def ingest_directory(
    directory: Path,
    container: str,
    *,
    pattern: str = "*",
    source_timezone: str = DEFAULT_SOURCE_TIMEZONE,
) -> RawSourceCollection:
    """Walk a directory and ingest each audio file → RawSourceCollection.

    Only files with known audio extensions (.wav, .m4a, .mp3) are ingested;
    non-audio files are silently skipped.
    """
    sources: list[RawSource] = []
    for entry in sorted(directory.glob(pattern)):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        try:
            sources.append(ingest_file(entry, container, source_timezone=source_timezone))
        except Exception as e:
            logger.error("Failed to ingest %s: %s", entry, e)
            raise
    return RawSourceCollection(container=container, sources=sources)
