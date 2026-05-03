# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for unified_transcript (per-source transcripts → wall-clock-aligned timeline)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection
from contextpulse_pipeline.sync_matcher import ResolvedSource, SyncResult
from contextpulse_pipeline.unified_transcript import (
    build_unified_transcript,
    render_unified_markdown,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_transcript(
    path: Path,
    sha256: str,
    segments: list[tuple[float, float, str]],
) -> None:
    """Write a minimal Whisper-style transcript JSON."""
    payload = {
        "source_sha256": sha256,
        "language": "en",
        "model": "whisper-large-v3",
        "duration_sec": max(end for _, end, _ in segments) if segments else 0.0,
        "segments": [
            {"start": s, "end": e, "text": t, "avg_logprob": -0.1} for s, e, t in segments
        ],
        "text": " ".join(t for _, _, t in segments),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_raw_source(sha: str, file_path: str, tier: str = "C") -> RawSource:
    return RawSource(
        sha256=sha,
        file_path=file_path,
        container="test",
        source_tier=tier,
        duration_sec=60.0,
        sample_rate=16000,
        channel_count=1,
        codec="mp3",
    )


def _make_resolved(sha: str, wall_start: datetime, provenance: str = "matched") -> ResolvedSource:
    return ResolvedSource(
        sha256=sha,
        wall_start_utc=wall_start,
        provenance=provenance,
        anchor_count=10,
        offset_from_anchor_sec=0.0,
    )


# ---------------------------------------------------------------------------
# build_unified_transcript
# ---------------------------------------------------------------------------


class TestBuildUnifiedTranscript:
    def test_two_sources_chronological_merge(self, tmp_path: Path) -> None:
        sha_a = "a" * 64
        sha_b = "b" * 64

        # Source A: 10:00 wall start, segments at [0-5] "hello" and [10-15] "world"
        # Source B: 10:00:03 wall start (3 sec later), segment at [0-5] "from B"
        # Expected merge order (by wall time):
        #   10:00:00 hello (from A)
        #   10:00:03 from B (from B)
        #   10:00:10 world (from A)
        anchor_utc = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        b_start = datetime(2026, 4, 26, 10, 0, 3, tzinfo=timezone.utc)

        coll = RawSourceCollection(
            container="test",
            sources=[
                _make_raw_source(sha_a, str(tmp_path / "a.wav")),
                _make_raw_source(sha_b, str(tmp_path / "b.wav")),
            ],
        )
        sync = SyncResult(
            container="test",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor_utc,
            resolved_sources=[
                _make_resolved(sha_a, anchor_utc),
                _make_resolved(sha_b, b_start),
            ],
        )
        # Use shorter sha-prefix filenames
        _write_transcript(
            tmp_path / f"{sha_a[:16]}.json",
            sha_a,
            [(0.0, 5.0, "hello"), (10.0, 15.0, "world")],
        )
        _write_transcript(
            tmp_path / f"{sha_b[:16]}.json",
            sha_b,
            [(0.0, 5.0, "from B")],
        )

        unified = build_unified_transcript(sync, coll, tmp_path)

        assert len(unified.segments) == 3
        # Chronological order
        assert unified.segments[0].text.strip() == "hello"
        assert unified.segments[1].text.strip() == "from B"
        assert unified.segments[2].text.strip() == "world"
        # Wall times correct
        assert unified.segments[0].wall_start_utc == anchor_utc
        assert unified.segments[1].wall_start_utc == b_start
        # Source attribution preserved
        assert unified.segments[0].source_sha256 == sha_a
        assert unified.segments[1].source_sha256 == sha_b

    def test_unreachable_sources_skipped(self, tmp_path: Path) -> None:
        sha_a = "a" * 64
        sha_b = "b" * 64
        anchor_utc = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        coll = RawSourceCollection(
            container="test",
            sources=[
                _make_raw_source(sha_a, str(tmp_path / "a.wav")),
                _make_raw_source(sha_b, str(tmp_path / "b.wav")),
            ],
        )
        sync = SyncResult(
            container="test",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor_utc,
            resolved_sources=[_make_resolved(sha_a, anchor_utc)],
            unreachable_sources=[sha_b],
        )
        _write_transcript(
            tmp_path / f"{sha_a[:16]}.json",
            sha_a,
            [(0.0, 5.0, "only A")],
        )
        _write_transcript(
            tmp_path / f"{sha_b[:16]}.json",
            sha_b,
            [(0.0, 5.0, "B is unreachable")],
        )

        unified = build_unified_transcript(sync, coll, tmp_path)

        assert len(unified.segments) == 1
        assert unified.segments[0].text.strip() == "only A"
        assert sha_b in unified.unreachable_sources

    def test_missing_transcript_logged_not_raised(self, tmp_path: Path) -> None:
        sha_a = "a" * 64
        anchor_utc = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        coll = RawSourceCollection(
            container="test",
            sources=[_make_raw_source(sha_a, str(tmp_path / "a.wav"))],
        )
        sync = SyncResult(
            container="test",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor_utc,
            resolved_sources=[_make_resolved(sha_a, anchor_utc)],
        )
        # NO transcript JSON written

        unified = build_unified_transcript(sync, coll, tmp_path)

        # No crash, just empty
        assert len(unified.segments) == 0
        assert sha_a in unified.missing_transcripts


class TestRenderUnifiedMarkdown:
    def test_markdown_includes_timestamps_and_source(self, tmp_path: Path) -> None:
        sha_a = "a" * 64
        anchor_utc = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        coll = RawSourceCollection(
            container="test",
            sources=[_make_raw_source(sha_a, str(tmp_path / "lavalier_chris.wav"), tier="A")],
        )
        sync = SyncResult(
            container="test",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor_utc,
            resolved_sources=[_make_resolved(sha_a, anchor_utc, provenance="bwf")],
        )
        _write_transcript(
            tmp_path / f"{sha_a[:16]}.json",
            sha_a,
            [(30.0, 35.0, "hello world")],
        )

        unified = build_unified_transcript(sync, coll, tmp_path)
        md = render_unified_markdown(unified)

        # Header references container
        assert "test" in md
        # ISO timestamp + source filename appear
        assert "10:00:30" in md
        assert "lavalier_chris.wav" in md
        # Tier badge
        assert "A" in md
        assert "hello world" in md


# ---------------------------------------------------------------------------
# Real Josh hike integration
# ---------------------------------------------------------------------------


JOSH_RAW = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/raw_sources.json"
)
JOSH_TRANSCRIPTS = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/transcripts"
)
JOSH_REFINED = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/sync_result_refined.json"
)


@pytest.mark.skipif(
    not (JOSH_RAW.exists() and JOSH_TRANSCRIPTS.exists() and JOSH_REFINED.exists()),
    reason="Josh hike data not present",
)
@pytest.mark.slow
class TestJoshHikeUnifiedTranscript:
    def test_produces_unified_timeline(self) -> None:
        coll = RawSourceCollection.from_json(path=JOSH_RAW)
        sync_data = json.loads(JOSH_REFINED.read_text(encoding="utf-8"))
        sync = SyncResult(
            container=sync_data["container"],
            anchor_source_sha256=sync_data["anchor_source_sha256"],
            anchor_origination_utc=datetime.fromisoformat(sync_data["anchor_origination_utc"]),
            resolved_sources=[
                ResolvedSource(
                    sha256=r["sha256"],
                    wall_start_utc=datetime.fromisoformat(r["wall_start_utc"]),
                    provenance=r["provenance"],
                    anchor_count=r["anchor_count"],
                    offset_from_anchor_sec=r["offset_from_anchor_sec"],
                )
                for r in sync_data["resolved_sources"]
            ],
            unreachable_sources=sync_data.get("unreachable_sources", []),
        )

        unified = build_unified_transcript(sync, coll, JOSH_TRANSCRIPTS)

        # Sanity checks on a real ~3.4-hour hike
        assert len(unified.segments) > 1000  # Whisper produces many segments per hour
        # Timeline should be monotonically increasing in wall_start_utc
        for prev, cur in zip(unified.segments, unified.segments[1:]):
            assert prev.wall_start_utc <= cur.wall_start_utc
        # No missing transcripts (we ran A.2 on all 14)
        assert len(unified.missing_transcripts) == 0
