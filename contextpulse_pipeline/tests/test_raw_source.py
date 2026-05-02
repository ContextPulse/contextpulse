"""Tests for contextpulse_pipeline.raw_source — RawSource + RawSourceCollection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    *,
    sha256: str = "a" * 64,
    file_path: str = "audio/test.wav",
    container: str = "ep-test",
    source_tier: str = "A",
    duration_sec: float = 60.0,
    sample_rate: int = 48000,
    channel_count: int = 1,
    codec: str = "pcm_s24le",
    bit_depth: int | None = 24,
    bwf_origination: datetime | None = None,
    filename_origination: datetime | None = None,
    provenance: str = "none",
) -> RawSource:
    return RawSource(
        sha256=sha256,
        file_path=file_path,
        container=container,
        source_tier=source_tier,
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        channel_count=channel_count,
        codec=codec,
        bit_depth=bit_depth,
        bwf_origination=bwf_origination,
        filename_origination=filename_origination,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# RawSource schema
# ---------------------------------------------------------------------------


class TestRawSourceSchema:
    def test_minimal_construction(self) -> None:
        src = _make_source()
        assert src.sha256 == "a" * 64
        assert src.source_tier == "A"
        assert src.bwf_origination is None
        assert src.filename_origination is None
        assert src.provenance == "none"

    def test_all_fields_round_trip(self) -> None:
        ts = datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        src = _make_source(bwf_origination=ts, provenance="bwf")
        assert src.bwf_origination == ts
        assert src.provenance == "bwf"

    def test_invalid_provenance_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_source(provenance="garbage")

    def test_missing_required_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RawSource(  # type: ignore[call-arg]
                sha256="a" * 64,
                file_path="x.wav",
                container="ep",
                source_tier="A",
                duration_sec=1.0,
                # missing sample_rate, channel_count, codec
            )


# ---------------------------------------------------------------------------
# RawSource.best_origination_utc
# ---------------------------------------------------------------------------


class TestBestOrigination:
    def test_bwf_wins_when_both_present(self) -> None:
        bwf = datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        fn = datetime(2026, 4, 26, 12, 3, 12, tzinfo=timezone.utc)
        src = _make_source(bwf_origination=bwf, filename_origination=fn, provenance="bwf")
        assert src.best_origination_utc == bwf

    def test_filename_used_when_no_bwf(self) -> None:
        fn = datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        src = _make_source(filename_origination=fn, provenance="filename")
        assert src.best_origination_utc == fn

    def test_none_when_neither_present(self) -> None:
        src = _make_source(provenance="none")
        assert src.best_origination_utc is None


# ---------------------------------------------------------------------------
# RawSourceCollection
# ---------------------------------------------------------------------------


class TestRawSourceCollection:
    def test_empty_collection(self) -> None:
        coll = RawSourceCollection(container="ep-test")
        assert coll.container == "ep-test"
        assert coll.sources == []

    def test_collection_with_sources(self) -> None:
        coll = RawSourceCollection(
            container="ep-test",
            sources=[_make_source(), _make_source(sha256="b" * 64)],
        )
        assert len(coll.sources) == 2

    def test_json_round_trip_in_memory(self) -> None:
        ts = datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        original = RawSourceCollection(
            container="ep-test",
            sources=[
                _make_source(bwf_origination=ts, provenance="bwf"),
                _make_source(sha256="b" * 64, source_tier="C", codec="mp3"),
            ],
        )
        emitted = original.to_json()
        loaded = RawSourceCollection.from_json(emitted)
        assert loaded.container == "ep-test"
        assert len(loaded.sources) == 2
        assert loaded.sources[0].bwf_origination == ts
        assert loaded.sources[1].source_tier == "C"

    def test_json_round_trip_to_disk(self, tmp_path: Path) -> None:
        out = tmp_path / "raw_sources.json"
        original = RawSourceCollection(
            container="ep-test",
            sources=[_make_source()],
        )
        original.to_json(path=out)
        assert out.exists()
        loaded = RawSourceCollection.from_json(path=out)
        assert loaded.container == "ep-test"
        assert len(loaded.sources) == 1
        assert loaded.sources[0].sha256 == "a" * 64

    def test_from_json_requires_data_or_path(self) -> None:
        with pytest.raises(ValueError, match="data or path"):
            RawSourceCollection.from_json()
