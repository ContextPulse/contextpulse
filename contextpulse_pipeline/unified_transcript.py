# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.6 — Unified transcript on the refined wall-clock timeline.

Takes per-source Whisper transcripts plus a SyncResult (with wall_start_utc
filled in for each source) and produces a single chronologically-ordered
transcript across all sources. Each segment carries source attribution.

This is the immediately-useful intermediate product after A.3b — gives a
wall-clock view of "what was said when" across the whole recording, even
before per-speaker fingerprinting (Phase 1.5) lands.

When Phase 1.5 ECAPA fingerprinting completes, it adds `speaker_label` to
each UnifiedSegment via a separate enrichment pass.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from contextpulse_pipeline.raw_source import RawSourceCollection
from contextpulse_pipeline.sync_matcher import SyncResult

logger = logging.getLogger(__name__)


@dataclass
class UnifiedSegment:
    """One Whisper segment, projected onto the unified wall-clock timeline."""

    wall_start_utc: datetime
    wall_end_utc: datetime
    source_sha256: str
    source_filename: str
    source_tier: str
    text: str
    avg_logprob: float = 0.0
    speaker_label: str | None = None  # filled by Phase 1.5 ECAPA pass


@dataclass
class UnifiedTranscript:
    """All segments from all sources, merged on wall-clock and chronologically sorted."""

    container: str
    anchor_origination_utc: datetime
    segments: list[UnifiedSegment] = field(default_factory=list)
    unreachable_sources: list[str] = field(default_factory=list)
    missing_transcripts: list[str] = field(default_factory=list)

    @property
    def n_sources(self) -> int:
        return len({s.source_sha256 for s in self.segments})

    def to_json(self, *, path: Path | None = None) -> str:
        payload = {
            "container": self.container,
            "anchor_origination_utc": self.anchor_origination_utc.isoformat(),
            "n_segments": len(self.segments),
            "n_sources": self.n_sources,
            "unreachable_sources": self.unreachable_sources,
            "missing_transcripts": self.missing_transcripts,
            "segments": [
                {
                    "wall_start_utc": s.wall_start_utc.isoformat(),
                    "wall_end_utc": s.wall_end_utc.isoformat(),
                    "source_sha256": s.source_sha256,
                    "source_filename": s.source_filename,
                    "source_tier": s.source_tier,
                    "text": s.text,
                    "avg_logprob": s.avg_logprob,
                    "speaker_label": s.speaker_label,
                }
                for s in self.segments
            ],
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_unified_transcript(
    sync: SyncResult,
    coll: RawSourceCollection,
    transcripts_dir: Path,
) -> UnifiedTranscript:
    """Merge per-source transcripts onto the synced wall-clock timeline.

    Each Whisper segment's relative `start`/`end` is added to the source's
    `wall_start_utc` to produce absolute UTC timestamps. All segments from
    all resolved sources are merged and sorted chronologically.

    Sources marked unreachable in the SyncResult are skipped (cannot be
    placed on the timeline). Sources whose transcript file is missing on
    disk are recorded in `missing_transcripts` but do not raise.
    """
    sha_to_source = {s.sha256: s for s in coll.sources}
    resolved = {r.sha256: r for r in sync.resolved_sources}

    segments: list[UnifiedSegment] = []
    missing: list[str] = []

    for sha, resolved_src in resolved.items():
        rs = sha_to_source.get(sha)
        if rs is None:
            logger.warning("Source %s in SyncResult but not in collection", sha[:8])
            continue
        json_path = transcripts_dir / f"{sha[:16]}.json"
        if not json_path.exists():
            missing.append(sha)
            logger.warning(
                "Transcript missing for %s at %s — skipping in unified output",
                sha[:8],
                json_path,
            )
            continue
        try:
            transcript = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Cannot parse transcript %s: %s", json_path, exc)
            missing.append(sha)
            continue

        wall_start = resolved_src.wall_start_utc
        filename = Path(rs.file_path).name
        for seg in transcript.get("segments", []):
            seg_start_sec = float(seg.get("start", 0.0))
            seg_end_sec = float(seg.get("end", seg_start_sec))
            segments.append(
                UnifiedSegment(
                    wall_start_utc=wall_start + timedelta(seconds=seg_start_sec),
                    wall_end_utc=wall_start + timedelta(seconds=seg_end_sec),
                    source_sha256=sha,
                    source_filename=filename,
                    source_tier=rs.source_tier,
                    text=seg.get("text", ""),
                    avg_logprob=float(seg.get("avg_logprob", 0.0)),
                )
            )

    segments.sort(key=lambda s: (s.wall_start_utc, s.source_sha256))

    return UnifiedTranscript(
        container=sync.container,
        anchor_origination_utc=sync.anchor_origination_utc,
        segments=segments,
        unreachable_sources=list(sync.unreachable_sources),
        missing_transcripts=missing,
    )


# ---------------------------------------------------------------------------
# Render — markdown
# ---------------------------------------------------------------------------


def render_unified_markdown(
    unified: UnifiedTranscript,
    *,
    show_logprob: bool = False,
) -> str:
    """Render a unified transcript as markdown for human review.

    Format:
        # Unified Transcript — <container>

        > <metadata>

        ## HH:MM:SS  [tier-A]  filename
        > segment text

        ## HH:MM:SS  [tier-C]  filename
        > segment text
    """
    lines: list[str] = []
    lines.append(f"# Unified Transcript — {unified.container}")
    lines.append("")
    lines.append(
        f"> Anchor: {unified.anchor_origination_utc.isoformat()}  |  "
        f"{len(unified.segments)} segments from {unified.n_sources} sources"
    )
    if unified.unreachable_sources:
        lines.append(f">  unreachable sources: {len(unified.unreachable_sources)}")
    if unified.missing_transcripts:
        lines.append(f">  missing transcripts: {len(unified.missing_transcripts)}")
    lines.append("")

    for seg in unified.segments:
        ts = seg.wall_start_utc.strftime("%H:%M:%S")
        speaker = f"  **{seg.speaker_label}**" if seg.speaker_label else ""
        logprob = f"  (logprob {seg.avg_logprob:.2f})" if show_logprob else ""
        lines.append(f"## {ts}  [tier-{seg.source_tier}]  {seg.source_filename}{speaker}{logprob}")
        lines.append(f"> {seg.text.strip()}")
        lines.append("")

    return "\n".join(lines)
