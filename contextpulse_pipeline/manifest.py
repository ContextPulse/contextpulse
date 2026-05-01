# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Pydantic schema: AudioEntry, SynthesisRun, Manifest.

Manifest is the source of truth for every container. Every audio file gets
an entry BEFORE any processing — never infer state from S3 prefixes or filenames.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier ordering — lower ordinal = higher quality (A beats B beats C)
# ---------------------------------------------------------------------------

_TIER_ORDER: dict[str, int] = {"A": 0, "B": 1, "C": 2}


def _tier_rank(tier: str) -> int:
    """Return sort key where lower = higher quality (A=0 > B=1 > C=2)."""
    return _TIER_ORDER.get(tier.upper(), 99)


def _overlaps(
    start_a: datetime,
    dur_a: float,
    start_b: datetime,
    dur_b: float,
) -> bool:
    """Return True if two time windows overlap (even by a single second)."""
    end_a = start_a.timestamp() + dur_a
    end_b = start_b.timestamp() + dur_b
    a_s = start_a.timestamp()
    b_s = start_b.timestamp()
    return a_s < end_b and b_s < end_a


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContainerState(str, Enum):
    """Lifecycle state of an audio container."""

    open = "open"
    finalized = "finalized"
    published = "published"
    superseded = "superseded"


# ---------------------------------------------------------------------------
# AudioEntry
# ---------------------------------------------------------------------------


class AudioEntry(BaseModel):
    """One audio file within a container."""

    sha256: str
    source_tier: str
    wall_start_utc: datetime
    duration_sec: float
    file_path: str

    # Optional — populated during processing
    transcript_path: str | None = None
    superseded_by: str | None = None
    device_fingerprint: dict[str, Any] | None = None
    participant: str | None = None

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# SynthesisRun
# ---------------------------------------------------------------------------


class SynthesisRun(BaseModel):
    """Record of one LLM synthesis pass on a container."""

    type: str  # "preview" | "finalize"
    at: datetime
    tier_used: str
    outputs: list[str]
    partial: bool


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class Manifest(BaseModel):
    """Source-of-truth for one audio container."""

    episode: str
    state: ContainerState = ContainerState.open
    audio_entries: list[AudioEntry] = Field(default_factory=list)
    synthesis_runs: list[SynthesisRun] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Internal immutability marker (set by mark_published)
    _published: bool = False

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def _assert_mutable(self, *, force: bool = False) -> None:
        """Raise if container is published and force is not set (Rule #9)."""
        if self.state == ContainerState.published and not force:
            raise ValueError(
                "Container is in 'published' state and is immutable. "
                "Use force=True to override (with audit trail)."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_audio(
        self,
        entry: AudioEntry,
        *,
        episode: str,
        force: bool = False,
    ) -> None:
        """Add an audio entry to the manifest.

        Rules enforced:
        - episode parameter is REQUIRED (Rule #3, #10 — no ambiguous adds)
        - episode must match the manifest's episode
        - Container must not be published (Rule #9) unless force=True
        - SHA256 deduplication: silently skip duplicate adds
        - Automatic tier supersession for overlapping windows
        """
        if episode != self.episode:
            raise ValueError(
                f"Episode mismatch: manifest is for '{self.episode}', "
                f"but add_audio called with episode='{episode}'."
            )
        self._assert_mutable(force=force)

        # SHA256 dedup — idempotent re-add
        existing_shas = {e.sha256 for e in self.audio_entries}
        if entry.sha256 in existing_shas:
            logger.debug("Skipping duplicate audio entry sha256=%s", entry.sha256[:12])
            return

        # Apply tier supersession for overlapping windows
        for existing in self.audio_entries:
            if not _overlaps(
                existing.wall_start_utc,
                existing.duration_sec,
                entry.wall_start_utc,
                entry.duration_sec,
            ):
                continue

            existing_rank = _tier_rank(existing.source_tier)
            new_rank = _tier_rank(entry.source_tier)

            if new_rank < existing_rank:
                # New entry is higher quality — mark existing as superseded
                existing.superseded_by = entry.sha256
                logger.info(
                    "Superseding tier-%s entry %s with tier-%s entry %s",
                    existing.source_tier,
                    existing.sha256[:12],
                    entry.source_tier,
                    entry.sha256[:12],
                )
            elif existing_rank < new_rank:
                # Existing entry is higher quality — new entry is superseded
                entry.superseded_by = existing.sha256
                logger.info(
                    "New tier-%s entry %s superseded by existing tier-%s entry %s",
                    entry.source_tier,
                    entry.sha256[:12],
                    existing.source_tier,
                    existing.sha256[:12],
                )

        self.audio_entries.append(entry)
        self._touch()

    def mark_superseded(self, *, old_sha: str, new_sha: str) -> None:
        """Mark an existing entry as superseded by a new one."""
        for entry in self.audio_entries:
            if entry.sha256 == old_sha:
                entry.superseded_by = new_sha
                self._touch()
                return
        raise KeyError(f"No audio entry found with sha256={old_sha!r}")

    def record_synthesis_run(self, run: SynthesisRun) -> None:
        """Append a synthesis run record to the manifest."""
        self.synthesis_runs.append(run)
        self._touch()

    def mark_published(self) -> None:
        """Transition to published state.

        Container must be in 'finalized' state first (Rule #9).
        Once published, mutations raise ValueError unless force=True.
        """
        if self.state != ContainerState.finalized:
            raise ValueError(
                f"Container must be in 'finalized' state before publishing. "
                f"Current state: '{self.state.value}'."
            )
        self.state = ContainerState.published
        self._touch()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self, *, path: Path | None = None, indent: int = 2) -> str:
        """Serialize manifest to JSON string (and optionally write to file)."""
        data = self.model_dump(mode="json")
        serialized = json.dumps(data, indent=indent, default=str)
        if path is not None:
            path.write_text(serialized, encoding="utf-8")
        return serialized

    @classmethod
    def from_json(cls, data: str | None = None, *, path: Path | None = None) -> "Manifest":
        """Deserialize manifest from JSON string or file."""
        if path is not None:
            data = path.read_text(encoding="utf-8")
        if data is None:
            raise ValueError("Either data or path must be provided.")
        parsed = json.loads(data)
        return cls.model_validate(parsed)
