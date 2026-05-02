"""Tests for contextpulse_pipeline.ingest — file/directory ingest into RawSource."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextpulse_pipeline.ingest import (
    ingest_directory,
    ingest_file,
    parse_filename_timestamp,
)
from contextpulse_pipeline.raw_source import RawSourceCollection
from contextpulse_pipeline.tests.test_bwf import (
    _build_minimal_wav_with_bext,
    _build_wav_without_bext,
)

# Real Josh hike files for integration tests (skip if absent)
DJI_FIXTURE = Path("C:/Users/david/Desktop/dji mic3/TX00_MIC021_20260426_060311_orig.wav")
TELEGRAM_FIXTURE_DIR = Path("C:/Users/david/AppData/Local/Temp/josh-narrative/telegram")


# ---------------------------------------------------------------------------
# parse_filename_timestamp
# ---------------------------------------------------------------------------


class TestParseFilenameTimestamp:
    def test_dji_pattern_parses(self) -> None:
        result = parse_filename_timestamp("TX00_MIC021_20260426_060311_orig.wav", "America/Denver")
        assert result is not None
        # 06:03:11 MDT (UTC-6) → 12:03:11 UTC
        assert result == datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)

    def test_returns_tz_aware(self) -> None:
        result = parse_filename_timestamp("TX00_MIC021_20260426_060311_orig.wav", "America/Denver")
        assert result is not None
        assert result.tzinfo is not None

    def test_telegram_filename_returns_none(self) -> None:
        # final-1777209787307.mp3 has no parseable timestamp pattern
        assert parse_filename_timestamp("final-1777209787307.mp3", "America/Denver") is None

    def test_no_pattern_returns_none(self) -> None:
        assert parse_filename_timestamp("random_audio.wav", "America/Denver") is None

    def test_invalid_date_returns_none(self) -> None:
        assert parse_filename_timestamp("TX00_MIC021_20261332_060311.wav", "America/Denver") is None


# ---------------------------------------------------------------------------
# ingest_file — synthetic WAV (with bext)
# ---------------------------------------------------------------------------


class TestIngestFileSyntheticWav:
    def test_dji_style_wav_with_bext(self, tmp_path: Path) -> None:
        wav = tmp_path / "TX00_MIC021_20260426_060311_orig.wav"
        _build_minimal_wav_with_bext(wav)
        src = ingest_file(wav, container="ep-test", source_timezone="America/Denver")
        assert src.container == "ep-test"
        assert src.source_tier == "A"  # .wav default → tier A
        assert src.codec.startswith("pcm") or src.codec == "wav"
        assert src.bwf_origination == datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        assert src.filename_origination == datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        assert src.provenance == "bwf"  # bwf wins over filename

    def test_wav_without_bext_falls_back_to_filename(self, tmp_path: Path) -> None:
        wav = tmp_path / "TX00_MIC021_20260426_060311_orig.wav"
        _build_wav_without_bext(wav)
        src = ingest_file(wav, container="ep-test", source_timezone="America/Denver")
        assert src.bwf_origination is None
        assert src.filename_origination == datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        assert src.provenance == "filename"

    def test_wav_no_bext_no_pattern_provenance_none(self, tmp_path: Path) -> None:
        wav = tmp_path / "untimed.wav"
        _build_wav_without_bext(wav)
        src = ingest_file(wav, container="ep-test")
        assert src.bwf_origination is None
        assert src.filename_origination is None
        assert src.provenance == "none"

    def test_explicit_tier_overrides_default(self, tmp_path: Path) -> None:
        wav = tmp_path / "untimed.wav"
        _build_wav_without_bext(wav)
        src = ingest_file(wav, container="ep-test", tier="B")
        assert src.source_tier == "B"

    def test_sha256_is_64_hex_chars(self, tmp_path: Path) -> None:
        wav = tmp_path / "x.wav"
        _build_wav_without_bext(wav)
        src = ingest_file(wav, container="ep-test")
        assert len(src.sha256) == 64
        assert all(c in "0123456789abcdef" for c in src.sha256)


# ---------------------------------------------------------------------------
# ingest_directory
# ---------------------------------------------------------------------------


class TestIngestDirectory:
    def test_empty_directory(self, tmp_path: Path) -> None:
        coll = ingest_directory(tmp_path, container="ep-test")
        assert coll.container == "ep-test"
        assert coll.sources == []

    def test_mixed_directory(self, tmp_path: Path) -> None:
        # 2 WAV files (one with bext) + 1 .txt file (skipped, not audio)
        _build_minimal_wav_with_bext(tmp_path / "TX00_MIC021_20260426_060311_orig.wav")
        _build_wav_without_bext(tmp_path / "untimed.wav")
        (tmp_path / "notes.txt").write_text("ignore me")

        coll = ingest_directory(tmp_path, container="ep-test")
        assert len(coll.sources) == 2  # only the .wav files
        files = sorted(s.file_path for s in coll.sources)
        assert all(f.endswith(".wav") for f in files)


# ---------------------------------------------------------------------------
# Integration tests — real Josh hike files (skip if absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not DJI_FIXTURE.exists(), reason="DJI fixture not present")
class TestIngestRealDji:
    def test_real_dji_file_ingests(self) -> None:
        src = ingest_file(DJI_FIXTURE, container="ep-2026-04-26-josh-cashman")
        assert src.source_tier == "A"
        assert src.bwf_origination == datetime(2026, 4, 26, 12, 3, 11, tzinfo=timezone.utc)
        assert src.provenance == "bwf"
        assert src.duration_sec > 0
        assert src.sample_rate >= 16000
        assert src.channel_count >= 1


@pytest.mark.skipif(
    not TELEGRAM_FIXTURE_DIR.exists() or not any(TELEGRAM_FIXTURE_DIR.glob("*.mp3")),
    reason="Telegram fixtures not present",
)
class TestIngestRealTelegram:
    def test_real_telegram_mp3_ingests(self) -> None:
        mp3 = next(TELEGRAM_FIXTURE_DIR.glob("*.mp3"))
        src = ingest_file(mp3, container="ep-2026-04-26-josh-cashman")
        assert src.source_tier == "C"
        assert src.bwf_origination is None
        assert src.filename_origination is None  # telegram filenames have no timestamp
        assert src.provenance == "none"
        assert src.duration_sec > 0


@pytest.mark.skipif(
    not DJI_FIXTURE.exists() or not TELEGRAM_FIXTURE_DIR.exists(),
    reason="Josh hike fixtures not present",
)
class TestIngestJoshHikeFullFixture:
    """Full Josh hike fixture: assemble both source families into one collection."""

    def test_assembles_collection_from_two_sources(self, tmp_path: Path) -> None:
        # Stage a working dir with one DJI file + telegram dir contents
        # (avoids re-walking the much-larger 'dji mic3' tree)
        staging = tmp_path / "fixture"
        staging.mkdir()
        shutil.copy2(DJI_FIXTURE, staging / DJI_FIXTURE.name)
        for mp3 in TELEGRAM_FIXTURE_DIR.glob("*.mp3"):
            shutil.copy2(mp3, staging / mp3.name)

        coll = ingest_directory(staging, container="ep-2026-04-26-josh-cashman")
        # 1 DJI + 7 Telegram = 8
        assert len(coll.sources) == 8

        dji_entries = [s for s in coll.sources if s.provenance == "bwf"]
        tg_entries = [s for s in coll.sources if s.provenance == "none"]
        assert len(dji_entries) == 1
        assert len(tg_entries) == 7

        # JSON round-trip
        out = tmp_path / "raw_sources.json"
        coll.to_json(path=out)
        loaded = RawSourceCollection.from_json(path=out)
        assert len(loaded.sources) == 8
