# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Pydantic schemas for pre-sync raw audio sources.

A RawSource represents a file on disk — its content hash, format, and any
embedded origination metadata — but NOT its position on a synced session
timeline. Pre-sync entries live here. Once the cross-source matcher computes
sync offsets, RawSources are converted into AudioEntry instances inside a
Manifest with their wall_start_utc filled in.

This separation preserves the Manifest invariant: every AudioEntry has a
timestamp on the unified timeline. Pre-sync raw inputs cannot accidentally
reach downstream mixing/extraction stages.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Provenance values for the chosen origination timestamp
Provenance = Literal["bwf", "filename", "none"]


class RawSource(BaseModel):
    """One audio file on disk, pre-sync.

    bwf_origination and filename_origination are tz-aware UTC datetimes
    (converted at ingest time from the file's source timezone).
    """

    sha256: str
    file_path: str
    container: str
    source_tier: str  # "A" (broadcast WAV), "B" (phone WAV/m4a), "C" (transport-compressed mp3)
    duration_sec: float
    sample_rate: int
    channel_count: int
    codec: str  # "pcm_s24le", "mp3", etc.
    bit_depth: int | None = None
    bwf_origination: datetime | None = None  # tz-aware UTC if BWF bext present
    filename_origination: datetime | None = None  # tz-aware UTC if parseable from filename
    provenance: Provenance = "none"

    model_config = {"arbitrary_types_allowed": True}

    @property
    def best_origination_utc(self) -> datetime | None:
        """Highest-priority origination timestamp: BWF > filename > None."""
        return self.bwf_origination or self.filename_origination


class RawSourceCollection(BaseModel):
    """All raw sources for one container, pre-sync. Persistable to JSON."""

    container: str
    sources: list[RawSource] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def to_json(self, *, path: Path | None = None, indent: int = 2) -> str:
        """Serialize to JSON string (and optionally write to file)."""
        data = self.model_dump(mode="json")
        serialized = json.dumps(data, indent=indent, default=str)
        if path is not None:
            path.write_text(serialized, encoding="utf-8")
        return serialized

    @classmethod
    def from_json(cls, data: str | None = None, *, path: Path | None = None) -> RawSourceCollection:
        """Deserialize from JSON string or file."""
        if path is not None:
            data = path.read_text(encoding="utf-8")
        if data is None:
            raise ValueError("Either data or path must be provided.")
        parsed = json.loads(data)
        return cls.model_validate(parsed)
