# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""BWF (Broadcast Wave Format) bext chunk reader.

The bext chunk embeds origination metadata (date, time, originator) inside a
RIFF/WAVE file. DJI Mic 3 receivers write BWF v1; we read OriginationDate +
OriginationTime as the canonical recording wall-clock anchor for sync.

Spec: EBU Tech 3285 (Broadcast Wave Format Specification).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# bext chunk fixed-area field offsets (within chunk payload, after 8-byte chunk header)
_BEXT_DESCRIPTION_OFFSET = 0
_BEXT_DESCRIPTION_LEN = 256
_BEXT_ORIGINATOR_OFFSET = 256
_BEXT_ORIGINATOR_LEN = 32
_BEXT_REFERENCE_OFFSET = 288
_BEXT_REFERENCE_LEN = 32
_BEXT_ORIG_DATE_OFFSET = 320
_BEXT_ORIG_DATE_LEN = 10
_BEXT_ORIG_TIME_OFFSET = 330
_BEXT_ORIG_TIME_LEN = 8

_RIFF_HEADER_LEN = 12  # 4 'RIFF' + 4 size + 4 'WAVE'
_BEXT_MIN_PAYLOAD = 338  # description + originator + reference + date + time fields


@dataclass(frozen=True)
class BWFMetadata:
    """Parsed BWF bext chunk fields (raw, no timezone applied)."""

    description: str
    originator: str
    reference: str
    raw_date: str  # "YYYY-MM-DD" or "YYYY:MM:DD" or "YYYY/MM/DD"
    raw_time: str  # "HH:MM:SS"
    naive_datetime: datetime  # combined date+time, no tzinfo


def _decode_field(raw: bytes) -> str:
    """Decode a fixed-width text field: strip nulls, latin-1 decode."""
    return raw.rstrip(b"\x00").decode("latin-1", errors="replace").strip()


def _parse_naive_datetime(raw_date: str, raw_time: str) -> datetime | None:
    """Parse BWF OriginationDate + OriginationTime into a naive datetime.

    Spec mandates 'YYYY:MM:DD' but real-world tools (including DJI Mic 3) write
    'YYYY-MM-DD' or 'YYYY/MM/DD'. Normalize separators before parsing.
    """
    normalized_date = raw_date.replace(":", "-").replace("/", "-")
    try:
        return datetime.strptime(f"{normalized_date} {raw_time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.debug("Could not parse BWF datetime: date=%r time=%r", raw_date, raw_time)
        return None


def read_bext(path: Path) -> BWFMetadata | None:
    """Read the BWF bext chunk from a WAV file.

    Returns None if no bext chunk is found, or if the date/time fields cannot
    be parsed. Caller attaches timezone separately.
    """
    try:
        with path.open("rb") as f:
            header = f.read(_RIFF_HEADER_LEN)
            if len(header) < _RIFF_HEADER_LEN:
                return None
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return None

            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    return None  # walked off the end without finding bext
                chunk_id = chunk_header[:4]
                chunk_size = struct.unpack("<I", chunk_header[4:8])[0]

                if chunk_id != b"bext":
                    # Skip past this chunk's payload (RIFF chunks are 16-bit aligned)
                    skip = chunk_size + (chunk_size & 1)
                    f.seek(skip, 1)
                    continue

                if chunk_size < _BEXT_MIN_PAYLOAD:
                    logger.warning(
                        "bext chunk smaller than expected: %d < %d",
                        chunk_size,
                        _BEXT_MIN_PAYLOAD,
                    )
                    return None

                payload = f.read(chunk_size)
                if len(payload) < _BEXT_MIN_PAYLOAD:
                    return None

                description = _decode_field(
                    payload[
                        _BEXT_DESCRIPTION_OFFSET : _BEXT_DESCRIPTION_OFFSET + _BEXT_DESCRIPTION_LEN
                    ]
                )
                originator = _decode_field(
                    payload[
                        _BEXT_ORIGINATOR_OFFSET : _BEXT_ORIGINATOR_OFFSET + _BEXT_ORIGINATOR_LEN
                    ]
                )
                reference = _decode_field(
                    payload[_BEXT_REFERENCE_OFFSET : _BEXT_REFERENCE_OFFSET + _BEXT_REFERENCE_LEN]
                )
                raw_date = (
                    payload[_BEXT_ORIG_DATE_OFFSET : _BEXT_ORIG_DATE_OFFSET + _BEXT_ORIG_DATE_LEN]
                    .decode("ascii", errors="replace")
                    .strip()
                )
                raw_time = (
                    payload[_BEXT_ORIG_TIME_OFFSET : _BEXT_ORIG_TIME_OFFSET + _BEXT_ORIG_TIME_LEN]
                    .decode("ascii", errors="replace")
                    .strip()
                )

                naive = _parse_naive_datetime(raw_date, raw_time)
                if naive is None:
                    return None

                return BWFMetadata(
                    description=description,
                    originator=originator,
                    reference=reference,
                    raw_date=raw_date,
                    raw_time=raw_time,
                    naive_datetime=naive,
                )
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)
        return None
