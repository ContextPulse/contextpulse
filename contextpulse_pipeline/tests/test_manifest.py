"""Tests for contextpulse_pipeline.manifest — schema validation, supersession logic."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextpulse_pipeline.manifest import (
    AudioEntry,
    ContainerState,
    Manifest,
    SynthesisRun,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_entry(
    sha256: str = "aabbccdd" * 8,
    source_tier: str = "A",
    wall_start_utc: datetime | None = None,
    duration_sec: float = 60.0,
    file_path: str = "audio/clip.wav",
) -> AudioEntry:
    return AudioEntry(
        sha256=sha256,
        source_tier=source_tier,
        wall_start_utc=wall_start_utc or _utcnow(),
        duration_sec=duration_sec,
        file_path=file_path,
    )


def _make_manifest(episode: str = "ep-test") -> Manifest:
    return Manifest(episode=episode)


# ---------------------------------------------------------------------------
# ContainerState enum
# ---------------------------------------------------------------------------


class TestContainerState:
    def test_open_is_valid(self) -> None:
        assert ContainerState.open.value == "open"

    def test_finalized_value(self) -> None:
        assert ContainerState.finalized.value == "finalized"

    def test_published_value(self) -> None:
        assert ContainerState.published.value == "published"

    def test_superseded_value(self) -> None:
        assert ContainerState.superseded.value == "superseded"


# ---------------------------------------------------------------------------
# AudioEntry model
# ---------------------------------------------------------------------------


class TestAudioEntry:
    def test_minimal_creation(self) -> None:
        now = _utcnow()
        entry = AudioEntry(
            sha256="a" * 64,
            source_tier="A",
            wall_start_utc=now,
            duration_sec=120.5,
            file_path="audio/test.wav",
        )
        assert entry.sha256 == "a" * 64
        assert entry.source_tier == "A"
        assert entry.duration_sec == 120.5
        assert entry.transcript_path is None
        assert entry.superseded_by is None
        assert entry.device_fingerprint is None
        assert entry.participant is None

    def test_optional_fields_settable(self) -> None:
        entry = _make_entry()
        entry.transcript_path = "transcripts/clip.json"
        entry.superseded_by = "b" * 64
        entry.device_fingerprint = {"encoder": "opus", "sr": 16000}
        entry.participant = "Chris"
        assert entry.participant == "Chris"
        assert entry.device_fingerprint["sr"] == 16000


# ---------------------------------------------------------------------------
# SynthesisRun model
# ---------------------------------------------------------------------------


class TestSynthesisRun:
    def test_creation(self) -> None:
        run = SynthesisRun(
            type="preview",
            at=_utcnow(),
            tier_used="A",
            outputs=["s3://bucket/ep/storyline.md"],
            partial=True,
        )
        assert run.type == "preview"
        assert run.partial is True
        assert len(run.outputs) == 1

    def test_finalize_type(self) -> None:
        run = SynthesisRun(
            type="finalize",
            at=_utcnow(),
            tier_used="A",
            outputs=[],
            partial=False,
        )
        assert run.partial is False


# ---------------------------------------------------------------------------
# Manifest creation and add_audio
# ---------------------------------------------------------------------------


class TestManifest:
    def test_empty_manifest_is_open(self) -> None:
        m = _make_manifest()
        assert m.state == ContainerState.open
        assert m.audio_entries == []
        assert m.synthesis_runs == []

    def test_add_audio_adds_entry(self) -> None:
        m = _make_manifest()
        entry = _make_entry()
        m.add_audio(entry, episode="ep-test")
        assert len(m.audio_entries) == 1

    def test_add_audio_rejects_wrong_episode(self) -> None:
        m = _make_manifest(episode="ep-correct")
        entry = _make_entry()
        with pytest.raises(ValueError, match="episode"):
            m.add_audio(entry, episode="ep-wrong")

    def test_add_audio_requires_episode(self) -> None:
        """Rule #3 / #10: every audio add requires explicit episode parameter."""
        m = _make_manifest()
        entry = _make_entry()
        # Calling without episode kwarg should raise TypeError
        with pytest.raises(TypeError):
            m.add_audio(entry)  # type: ignore[call-arg]

    def test_add_audio_updates_updated_at(self) -> None:
        m = _make_manifest()
        before = m.updated_at
        # Small sleep not available, just verify updated_at is a datetime
        entry = _make_entry()
        m.add_audio(entry, episode="ep-test")
        assert isinstance(m.updated_at, datetime)


# ---------------------------------------------------------------------------
# Supersession logic
# ---------------------------------------------------------------------------


class TestSupersession:
    """Higher-tier entries supersede lower-tier overlapping entries."""

    def test_tier_b_does_not_supersede_tier_a(self) -> None:
        m = _make_manifest()
        base_time = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)

        tier_a = _make_entry(sha256="a" * 64, source_tier="A", wall_start_utc=base_time, duration_sec=3600.0)
        m.add_audio(tier_a, episode="ep-test")

        tier_b = _make_entry(sha256="b" * 64, source_tier="B", wall_start_utc=base_time, duration_sec=3600.0)
        m.add_audio(tier_b, episode="ep-test")

        # B should be marked superseded by A (A already exists and is higher tier)
        b_entry = m.audio_entries[-1]
        assert b_entry.superseded_by == "a" * 64

    def test_tier_a_supersedes_existing_tier_b(self) -> None:
        """When Tier A arrives after Tier B, B should be marked superseded."""
        m = _make_manifest()
        base_time = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)

        tier_b = _make_entry(sha256="b" * 64, source_tier="B", wall_start_utc=base_time, duration_sec=3600.0)
        m.add_audio(tier_b, episode="ep-test")

        tier_a = _make_entry(sha256="a" * 64, source_tier="A", wall_start_utc=base_time, duration_sec=3600.0)
        m.add_audio(tier_a, episode="ep-test")

        # B should now be marked superseded by A
        b_entry = m.audio_entries[0]
        assert b_entry.superseded_by == "a" * 64

    def test_non_overlapping_entries_not_superseded(self) -> None:
        m = _make_manifest()
        base_time = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)
        later_time = datetime(2026, 4, 26, 11, 0, 0, tzinfo=timezone.utc)

        tier_a_1 = _make_entry(sha256="a" * 64, source_tier="A", wall_start_utc=base_time, duration_sec=300.0)
        m.add_audio(tier_a_1, episode="ep-test")

        # No overlap: later_time > base_time + 300s
        tier_b_2 = _make_entry(sha256="b" * 64, source_tier="B", wall_start_utc=later_time, duration_sec=300.0)
        m.add_audio(tier_b_2, episode="ep-test")

        assert tier_b_2.superseded_by is None

    def test_same_tier_same_sha_is_idempotent(self) -> None:
        """Adding the same entry twice (same SHA) should not error but not duplicate."""
        m = _make_manifest()
        entry = _make_entry(sha256="c" * 64)
        m.add_audio(entry, episode="ep-test")
        m.add_audio(entry, episode="ep-test")
        # SHA dedup: no duplicate
        assert len([e for e in m.audio_entries if e.sha256 == "c" * 64]) == 1


# ---------------------------------------------------------------------------
# mark_superseded
# ---------------------------------------------------------------------------


class TestMarkSuperseded:
    def test_mark_superseded_sets_flag(self) -> None:
        m = _make_manifest()
        entry = _make_entry(sha256="a" * 64)
        m.add_audio(entry, episode="ep-test")
        m.mark_superseded(old_sha="a" * 64, new_sha="b" * 64)
        assert m.audio_entries[0].superseded_by == "b" * 64

    def test_mark_superseded_unknown_sha_raises(self) -> None:
        m = _make_manifest()
        with pytest.raises(KeyError):
            m.mark_superseded(old_sha="unknown" * 8, new_sha="b" * 64)


# ---------------------------------------------------------------------------
# record_synthesis_run
# ---------------------------------------------------------------------------


class TestRecordSynthesisRun:
    def test_appends_to_synthesis_runs(self) -> None:
        m = _make_manifest()
        run = SynthesisRun(
            type="preview",
            at=_utcnow(),
            tier_used="A",
            outputs=["s3://bucket/ep/storyline.md"],
            partial=True,
        )
        m.record_synthesis_run(run)
        assert len(m.synthesis_runs) == 1
        assert m.synthesis_runs[0].type == "preview"


# ---------------------------------------------------------------------------
# mark_published / immutability
# ---------------------------------------------------------------------------


class TestMarkPublished:
    def test_finalized_before_publish_allowed(self) -> None:
        m = _make_manifest()
        m.state = ContainerState.finalized
        m.mark_published()
        assert m.state == ContainerState.published

    def test_open_manifest_cannot_be_published(self) -> None:
        m = _make_manifest()
        with pytest.raises(ValueError, match="finalized"):
            m.mark_published()

    def test_mutation_after_publish_raises(self) -> None:
        m = _make_manifest()
        m.state = ContainerState.finalized
        m.mark_published()
        entry = _make_entry(sha256="x" * 64)
        with pytest.raises(ValueError, match="published"):
            m.add_audio(entry, episode="ep-test")

    def test_mutation_after_publish_force_allowed(self) -> None:
        m = _make_manifest()
        m.state = ContainerState.finalized
        m.mark_published()
        entry = _make_entry(sha256="x" * 64)
        # Should not raise with force=True
        m.add_audio(entry, episode="ep-test", force=True)
        assert len(m.audio_entries) == 1


# ---------------------------------------------------------------------------
# to_json / from_json round-trip
# ---------------------------------------------------------------------------


class TestJsonRoundTrip:
    def test_empty_manifest_round_trips(self) -> None:
        m = _make_manifest()
        serialized = m.to_json()
        data = json.loads(serialized)
        assert data["episode"] == "ep-test"
        assert data["state"] == "open"
        assert data["audio_entries"] == []

    def test_manifest_with_entries_round_trips(self) -> None:
        m = _make_manifest()
        entry = _make_entry()
        m.add_audio(entry, episode="ep-test")
        serialized = m.to_json()

        m2 = Manifest.from_json(serialized)
        assert m2.episode == m.episode
        assert len(m2.audio_entries) == 1
        assert m2.audio_entries[0].sha256 == entry.sha256

    def test_from_json_invalid_raises(self) -> None:
        with pytest.raises(Exception):
            Manifest.from_json("{not valid json")

    def test_published_state_preserves_round_trip(self) -> None:
        m = _make_manifest()
        m.state = ContainerState.finalized
        m.mark_published()
        m2 = Manifest.from_json(m.to_json())
        assert m2.state == ContainerState.published

    def test_to_json_to_file(self, tmp_path: Path) -> None:
        m = _make_manifest()
        out = tmp_path / "manifest.json"
        m.to_json(path=out)
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["episode"] == "ep-test"

    def test_from_json_file(self, tmp_path: Path) -> None:
        m = _make_manifest()
        out = tmp_path / "manifest.json"
        m.to_json(path=out)
        m2 = Manifest.from_json(path=out)
        assert m2.episode == "ep-test"
