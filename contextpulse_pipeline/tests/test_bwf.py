"""Tests for contextpulse_pipeline.bwf — BWF bext chunk reader."""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import pytest

from contextpulse_pipeline.bwf import BWFMetadata, read_bext

# ---------------------------------------------------------------------------
# Helpers — synthetic WAV with bext chunk for hermetic unit tests
# ---------------------------------------------------------------------------


def _build_pcm_fmt_chunk(
    *,
    channels: int = 1,
    sample_rate: int = 48000,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a valid 16-byte PCM fmt chunk that ffprobe will accept."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    body = struct.pack(
        "<HHIIHH",
        1,  # AudioFormat = PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    return b"fmt " + struct.pack("<I", len(body)) + body


def _build_minimal_wav_with_bext(
    path: Path,
    *,
    description: str = "ver:02.00.11.01",
    originator: str = "MIC 3",
    reference: str = "",
    orig_date: str = "2026-04-26",
    orig_time: str = "06:03:11",
    bext_size: int = 602,
) -> None:
    """Write a minimal valid RIFF/WAVE file with a bext chunk at known offsets."""
    fmt_chunk = _build_pcm_fmt_chunk()

    payload = bytearray(bext_size)
    payload[0 : len(description)] = description.encode("latin-1")
    payload[256 : 256 + len(originator)] = originator.encode("latin-1")
    payload[288 : 288 + len(reference)] = reference.encode("latin-1")
    payload[320 : 320 + len(orig_date)] = orig_date.encode("ascii")
    payload[330 : 330 + len(orig_time)] = orig_time.encode("ascii")
    bext_chunk = b"bext" + struct.pack("<I", bext_size) + bytes(payload)

    # Minimal valid data chunk: 1 sample of silence (2 bytes for 16-bit mono)
    data_payload = b"\x00\x00"
    data_chunk = b"data" + struct.pack("<I", len(data_payload)) + data_payload

    body = fmt_chunk + bext_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE"
    path.write_bytes(riff + body)


def _build_wav_without_bext(path: Path) -> None:
    fmt_chunk = _build_pcm_fmt_chunk()
    data_payload = b"\x00\x00"
    data_chunk = b"data" + struct.pack("<I", len(data_payload)) + data_payload
    body = fmt_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE"
    path.write_bytes(riff + body)


# ---------------------------------------------------------------------------
# read_bext — happy path
# ---------------------------------------------------------------------------


class TestReadBextHappyPath:
    def test_returns_bwf_metadata_for_valid_file(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav)
        result = read_bext(wav)
        assert result is not None
        assert isinstance(result, BWFMetadata)

    def test_originator_field_extracted(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, originator="MIC 3")
        result = read_bext(wav)
        assert result is not None
        assert result.originator == "MIC 3"

    def test_description_field_extracted(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, description="ver:02.00.11.01")
        result = read_bext(wav)
        assert result is not None
        assert result.description == "ver:02.00.11.01"

    def test_origination_date_and_time_extracted(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, orig_date="2026-04-26", orig_time="06:03:11")
        result = read_bext(wav)
        assert result is not None
        assert result.raw_date == "2026-04-26"
        assert result.raw_time == "06:03:11"

    def test_naive_datetime_parsed(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, orig_date="2026-04-26", orig_time="06:03:11")
        result = read_bext(wav)
        assert result is not None
        assert result.naive_datetime == datetime(2026, 4, 26, 6, 3, 11)
        assert result.naive_datetime.tzinfo is None  # naive — caller attaches tz


# ---------------------------------------------------------------------------
# read_bext — edge cases / error paths
# ---------------------------------------------------------------------------


class TestReadBextEdgeCases:
    def test_missing_bext_chunk_returns_none(self, tmp_path: Path) -> None:
        wav = tmp_path / "no_bext.wav"
        _build_wav_without_bext(wav)
        result = read_bext(wav)
        assert result is None

    def test_non_wav_file_returns_none(self, tmp_path: Path) -> None:
        not_wav = tmp_path / "fake.mp3"
        not_wav.write_bytes(b"\xff\xfb" + b"\x00" * 1024)  # rough MP3 frame header
        result = read_bext(not_wav)
        assert result is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.wav"
        empty.write_bytes(b"")
        result = read_bext(empty)
        assert result is None

    def test_truncated_riff_returns_none(self, tmp_path: Path) -> None:
        truncated = tmp_path / "truncated.wav"
        truncated.write_bytes(b"RIFF\x00\x00\x00\x00")  # header but no body
        result = read_bext(truncated)
        assert result is None

    def test_iso_date_with_colons_parses(self, tmp_path: Path) -> None:
        # BWF spec actually mandates "YYYY:MM:DD" with colons; many tools write
        # hyphens. Both should parse.
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, orig_date="2026:04:26", orig_time="06:03:11")
        result = read_bext(wav)
        assert result is not None
        assert result.naive_datetime == datetime(2026, 4, 26, 6, 3, 11)

    def test_unparseable_date_returns_none(self, tmp_path: Path) -> None:
        wav = tmp_path / "test.wav"
        _build_minimal_wav_with_bext(wav, orig_date="invalid!!", orig_time="06:03:11")
        result = read_bext(wav)
        assert result is None


# ---------------------------------------------------------------------------
# Integration test — real DJI file (skip if absent)
# ---------------------------------------------------------------------------

DJI_FIXTURE = Path("C:/Users/david/Desktop/dji mic3/TX00_MIC021_20260426_060311_orig.wav")


@pytest.mark.skipif(not DJI_FIXTURE.exists(), reason="DJI fixture not present")
class TestReadBextIntegration:
    def test_real_dji_file_parses(self) -> None:
        result = read_bext(DJI_FIXTURE)
        assert result is not None
        assert result.originator.startswith("MIC 3")
        assert result.raw_date == "2026-04-26"
        assert result.raw_time == "06:03:11"
        assert result.naive_datetime == datetime(2026, 4, 26, 6, 3, 11)
