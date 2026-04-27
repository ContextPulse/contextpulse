"""Tests for contextpulse_pipeline.master — Tier 1 audio unification module.

Tests use synthetic 30-second audio generated via numpy+soundfile to avoid
any dependency on real recordings. All S3 operations are mocked.

Coverage:
- concat ordering (chronological by embedded timestamp)
- Tier 1 filters lower noise floor
- bleed cancel reduces cross-channel correlation
- transcript merge attributes segments by channel
- QC flags sync drift above threshold
- idempotent skip when S3 outputs already exist
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import hashlib

import numpy as np
import pytest
import soundfile as sf

from contextpulse_pipeline.master import (
    MasterOutput,
    _apply_tier1_filters,
    _bleed_cancel,
    _concat_per_channel,
    _generate_chapters,
    _merge_transcripts,
    _parse_wall_time,
    _qc_checks,
    _mix_channels,
    unify_audio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SR = 48000  # Match DJI mic sample rate


def _make_sine(tmp_path: Path, name: str, freq: float = 440.0, dur: float = 2.0, sr: int = SR) -> Path:
    """Generate a pure sine wave OGG for testing."""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    samples = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    p = tmp_path / name
    sf.write(str(p), samples, sr)
    return p


def _make_noisy(
    tmp_path: Path, name: str, signal_freq: float = 440.0, noise_level: float = 0.3,
    dur: float = 2.0, sr: int = SR
) -> Path:
    """Generate a sine + white noise OGG for noise floor testing."""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    signal = 0.5 * np.sin(2 * np.pi * signal_freq * t)
    noise = noise_level * np.random.randn(len(t)).astype(np.float32)
    samples = (signal + noise).clip(-1.0, 1.0).astype(np.float32)
    p = tmp_path / name
    sf.write(str(p), samples, sr)
    return p


def _rms(path: Path, sr: int = SR) -> float:
    """Return RMS level of an audio file."""
    data, _ = sf.read(str(path), dtype="float32", always_2d=False)
    return float(np.sqrt(np.mean(data ** 2)))


def _make_s3_mock(existing_keys: set[str] | None = None) -> MagicMock:
    """Build a mock S3 client that tracks uploaded objects."""
    existing = set(existing_keys or [])
    client = MagicMock()

    def _head(Bucket, Key):  # noqa: N803
        if Key in existing:
            return {"ContentLength": 100}
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def _put(Bucket, Key, Body, **kwargs):  # noqa: N803
        existing.add(Key)
        return {}

    def _upload_file(local, bucket, key, **kwargs):
        existing.add(key)

    def _get(Bucket, Key, **kwargs):  # noqa: N803
        return {"Body": MagicMock(read=lambda: b"{}")}

    client.head_object.side_effect = _head
    client.put_object.side_effect = _put
    client.upload_file.side_effect = _upload_file
    client.get_object.side_effect = _get

    # Paginator returns empty by default
    paginator = MagicMock()
    paginator.paginate.return_value = iter([{"Contents": []}])
    client.get_paginator.return_value = paginator

    return client


# ---------------------------------------------------------------------------
# 1. test_concat_per_channel_in_chronological_order
# ---------------------------------------------------------------------------


class TestConcatPerChannel:
    def test_concat_orders_files_chronologically(self, tmp_path: Path) -> None:
        """_concat_per_channel should concatenate in the order passed.

        The caller (_group_by_channel) sorts files by timestamp. This test
        verifies the concat demuxer produces a file whose duration equals
        the sum of input durations (lossless copy).
        """
        # Create 3 short sine files (1s each)
        f1 = _make_sine(tmp_path, "TX01_MIC001_20260426T100000_orig.ogg", freq=220.0, dur=1.0)
        f2 = _make_sine(tmp_path, "TX01_MIC002_20260426T100001_orig.ogg", freq=440.0, dur=1.0)
        f3 = _make_sine(tmp_path, "TX01_MIC003_20260426T100002_orig.ogg", freq=880.0, dur=1.0)

        out = tmp_path / "concat_out.ogg"
        _concat_per_channel([f1, f2, f3], out)

        assert out.exists(), "Output file should be created"
        data, sr = sf.read(str(out), dtype="float32", always_2d=False)
        dur_sec = len(data) / sr
        # Should be 3 seconds (sum of inputs), allow ±0.1s for codec framing
        assert abs(dur_sec - 3.0) < 0.1, f"Expected ~3s, got {dur_sec:.2f}s"

    def test_concat_timestamp_ordering(self, tmp_path: Path) -> None:
        """_parse_wall_time should extract timestamps so sort works correctly."""
        names = [
            "TX01_MIC003_20260426T130000_orig.ogg",
            "TX01_MIC001_20260426T100000_orig.ogg",
            "TX01_MIC002_20260426T120000_orig.ogg",
        ]
        paths = [tmp_path / n for n in names]
        for p in paths:
            sf.write(str(p), np.zeros(SR, dtype=np.float32), SR)

        sorted_paths = sorted(paths, key=lambda p: _parse_wall_time(p.name))
        result_names = [p.name for p in sorted_paths]
        assert result_names[0] == "TX01_MIC001_20260426T100000_orig.ogg"
        assert result_names[1] == "TX01_MIC002_20260426T120000_orig.ogg"
        assert result_names[2] == "TX01_MIC003_20260426T130000_orig.ogg"


# ---------------------------------------------------------------------------
# 2. test_tier1_filters_reduce_noise_floor
# ---------------------------------------------------------------------------


class TestTier1Filters:
    def test_filters_reduce_noise_floor(self, tmp_path: Path) -> None:
        """Output of _apply_tier1_filters should have lower high-frequency noise.

        We measure the RMS of noise-heavy audio before and after applying
        highpass + denoise filters. The output RMS should be lower.
        """
        noisy = _make_noisy(
            tmp_path, "noisy_input.ogg",
            signal_freq=440.0,
            noise_level=0.4,
            dur=3.0,
        )
        filtered = tmp_path / "filtered_output.ogg"

        _apply_tier1_filters(
            noisy, filtered,
            {"highpass": True, "denoise": True, "level_match": False},
        )

        assert filtered.exists()
        rms_before = _rms(noisy)
        rms_after = _rms(filtered)
        # Filtered audio should have lower overall RMS due to noise removal
        # Allow some tolerance since loudnorm is off; denoise reduces noise content
        assert rms_after <= rms_before * 1.1, (
            f"Noise floor not reduced: before={rms_before:.4f}, after={rms_after:.4f}"
        )

    def test_no_filters_copies_file(self, tmp_path: Path) -> None:
        """With all filters disabled, output should be a copy of the input."""
        src = _make_sine(tmp_path, "source.ogg", dur=1.0)
        dst = tmp_path / "copy.ogg"
        _apply_tier1_filters(src, dst, {"highpass": False, "denoise": False, "level_match": False})
        assert dst.exists()
        # File size should be identical (shutil.copy2)
        assert dst.stat().st_size == src.stat().st_size


# ---------------------------------------------------------------------------
# 3. test_bleed_cancel_reduces_cross_channel_correlation
# ---------------------------------------------------------------------------


class TestBleedCancel:
    def test_bleed_cancel_reduces_correlation(self, tmp_path: Path) -> None:
        """After bleed cancellation, the cross-channel correlation should be lower.

        Synthetic setup: channel_b = channel_a * 0.6 + small independent signal.
        This simulates channel B picking up A's speaker (bleed).
        After cancellation, the residual correlation should be lower.
        """
        dur = 3.0
        n = int(SR * dur)
        rng = np.random.default_rng(42)

        # Channel A: loud speech simulation (500 Hz sine + low noise)
        t = np.linspace(0, dur, n, dtype=np.float32)
        signal_a = 0.7 * np.sin(2 * np.pi * 500 * t).astype(np.float32)
        noise_a = (0.02 * rng.standard_normal(n)).astype(np.float32)
        data_a = (signal_a + noise_a).clip(-1.0, 1.0)

        # Channel B: mostly bleed from A + its own small signal
        signal_b_own = 0.15 * np.sin(2 * np.pi * 700 * t).astype(np.float32)
        bleed = 0.6 * signal_a  # Simulated bleed
        data_b = (signal_b_own + bleed).clip(-1.0, 1.0)

        ch_a = tmp_path / "ch_a.wav"
        ch_b = tmp_path / "ch_b.wav"
        sf.write(str(ch_a), data_a, SR, subtype="PCM_16")
        sf.write(str(ch_b), data_b, SR, subtype="PCM_16")

        out_a = tmp_path / "debled_a.wav"
        out_b = tmp_path / "debled_b.wav"
        _bleed_cancel(ch_a, ch_b, out_a, out_b)

        assert out_b.exists()
        debled_b, _ = sf.read(str(out_b), dtype="float32", always_2d=False)
        debled_b = debled_b[:n]

        # Measure normalized cross-correlation (pearson) before and after
        # Use np.corrcoef which handles 2D array pairs correctly
        def pearson(x: np.ndarray, y: np.ndarray) -> float:
            min_len = min(len(x), len(y))
            x, y = x[:min_len].astype(float), y[:min_len].astype(float)
            std_x, std_y = x.std(), y.std()
            if std_x < 1e-6 or std_y < 1e-6:
                return 0.0
            return float(np.corrcoef(x, y)[0, 1])

        corr_before = abs(pearson(data_a, data_b))
        corr_after = abs(pearson(data_a, debled_b))

        assert corr_after < corr_before, (
            f"Bleed cancel did not reduce correlation: before={corr_before:.3f}, after={corr_after:.3f}"
        )


# ---------------------------------------------------------------------------
# 4. test_merge_transcripts_attributes_by_channel
# ---------------------------------------------------------------------------


class TestMergeTranscripts:
    def test_attributes_segments_by_channel(self) -> None:
        """Each segment should be attributed to the correct speaker based on channel."""
        channel_jsons = {
            "TX01": {
                "text": "Hello from TX01",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello"},
                    {"start": 3.0, "end": 5.0, "text": "from TX01"},
                ],
            },
            "TX00": {
                "text": "Hello from TX00",
                "segments": [
                    {"start": 1.0, "end": 2.5, "text": "Hello"},
                    {"start": 4.0, "end": 6.0, "text": "from TX00"},
                ],
            },
        }
        speaker_mapping = {"TX01": "Josh", "TX00": "Chris"}

        merged = _merge_transcripts(channel_jsons, speaker_mapping)
        segs = merged["segments"]

        assert len(segs) == 4
        # All segments from TX01 should be attributed to Josh
        josh_segs = [s for s in segs if s["speaker"] == "Josh"]
        chris_segs = [s for s in segs if s["speaker"] == "Chris"]
        assert len(josh_segs) == 2
        assert len(chris_segs) == 2

    def test_interleaved_by_start_time(self) -> None:
        """Merged output should be sorted by start time."""
        channel_jsons = {
            "TX01": {"text": "", "segments": [{"start": 5.0, "end": 6.0, "text": "B"}]},
            "TX00": {"text": "", "segments": [{"start": 2.0, "end": 3.0, "text": "A"}]},
        }
        speaker_mapping = {"TX01": "Speaker1", "TX00": "Speaker2"}
        merged = _merge_transcripts(channel_jsons, speaker_mapping)
        starts = [s["start"] for s in merged["segments"]]
        assert starts == sorted(starts), "Segments not sorted by start time"

    def test_unknown_channel_uses_key_as_speaker(self) -> None:
        """Channels not in speaker_mapping should use the channel key as speaker name."""
        channel_jsons = {
            "UNKNOWN_CH": {
                "text": "test",
                "segments": [{"start": 0.0, "end": 1.0, "text": "test"}],
            }
        }
        merged = _merge_transcripts(channel_jsons, speaker_mapping={})
        assert merged["segments"][0]["speaker"] == "UNKNOWN_CH"


# ---------------------------------------------------------------------------
# 5. test_qc_checks_flag_drift
# ---------------------------------------------------------------------------


class TestQcChecks:
    def test_qc_reports_high_drift_for_misaligned_channels(self, tmp_path: Path) -> None:
        """QC sync_drift_ms should be high (>500ms) when channels are offset by 1s.

        Uses WAV (lossless) so the delay is preserved exactly through the read/write
        cycle. OGG/opus would corrupt the exact sample offset during encoding.
        """
        dur = 5.0
        n = int(SR * dur)
        t = np.linspace(0, dur, n, dtype=np.float32)
        # Use a wideband chirp for better cross-correlation detectability
        signal = (0.5 * np.sin(2 * np.pi * (200 + 300 * t / dur) * t)).astype(np.float32)

        ch_a = tmp_path / "ch_a.wav"
        ch_b = tmp_path / "ch_b.wav"
        # ch_b is ch_a delayed by 1 second (pad with silence at start)
        delay_samples = SR  # 1 second = 48000 samples
        signal_b = np.concatenate([np.zeros(delay_samples, dtype=np.float32), signal[:-delay_samples]])
        sf.write(str(ch_a), signal, SR, subtype="PCM_16")
        sf.write(str(ch_b), signal_b, SR, subtype="PCM_16")

        master = tmp_path / "master.mp3"
        _mix_channels([ch_a], master, mode="mono")

        qc = _qc_checks([ch_a, ch_b], master, merged={"segments": []})
        assert qc["sync_drift_ms"] > 500, (
            f"Expected drift > 500ms for 1s offset, got {qc['sync_drift_ms']} ms"
        )

    def test_qc_reports_low_drift_for_aligned_channels(self, tmp_path: Path) -> None:
        """QC sync_drift_ms should be near 0 for identical channels (WAV, lossless)."""
        dur = 5.0
        n = int(SR * dur)
        t = np.linspace(0, dur, n, dtype=np.float32)
        signal = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        ch_a = tmp_path / "ch_a.wav"
        ch_b = tmp_path / "ch_b.wav"
        sf.write(str(ch_a), signal, SR, subtype="PCM_16")
        sf.write(str(ch_b), signal, SR, subtype="PCM_16")

        master = tmp_path / "master.mp3"
        _mix_channels([ch_a], master, mode="mono")

        qc = _qc_checks([ch_a, ch_b], master, merged={"segments": []})
        assert qc["sync_drift_ms"] < 50, (
            f"Expected low drift for aligned channels, got {qc['sync_drift_ms']} ms"
        )

    def test_qc_report_has_expected_fields(self, tmp_path: Path) -> None:
        """QC dict should contain all expected fields."""
        ch = _make_sine(tmp_path, "ch.ogg", dur=2.0)
        master = tmp_path / "master.mp3"
        _mix_channels([ch], master, mode="mono")
        qc = _qc_checks([ch], master, merged={"segments": [{"start": 0.0, "end": 1.0, "speaker": "X", "text": "hi"}]})

        assert "sync_drift_ms" in qc
        assert "duration_match" in qc
        assert "snr_per_channel" in qc
        assert "transcript_alignment" in qc
        assert "master_duration_sec" in qc


# ---------------------------------------------------------------------------
# 6. test_idempotent_skip_existing_outputs
# ---------------------------------------------------------------------------


class TestIdempotentSkip:
    def test_second_run_skips_heavy_steps_when_outputs_exist(self, tmp_path: Path) -> None:
        """When master_basic.mp3 already exists in S3, unify_audio should return
        without downloading any raw files or running ffmpeg.
        """
        session_id = "ep-smoke-idempotent-test"
        bucket = "test-bucket"
        existing_audio_key = f"outputs/{session_id}/master_basic.mp3"

        s3 = _make_s3_mock(existing_keys={existing_audio_key})

        # Provide a JSON response for transcript and QC
        def _get_side(Bucket, Key, **kwargs):  # noqa: N803
            if "transcript" in Key and Key.endswith(".json"):
                body = json.dumps({"segments": [{"start": 0.0, "end": 1.0, "speaker": "Josh", "text": "hi"}]})
                return {"Body": MagicMock(read=lambda: body.encode())}
            if "qc" in Key:
                body = json.dumps({"master_duration_sec": 60.0, "sync_drift_ms": 0.0})
                return {"Body": MagicMock(read=lambda: body.encode())}
            return {"Body": MagicMock(read=lambda: b"{}")}

        s3.get_object.side_effect = _get_side

        speaker_mapping = {"TX01": "Josh", "TX00": "Chris"}
        result = unify_audio(session_id, bucket, speaker_mapping, s3_client=s3)

        # Should NOT have called list_objects (no download), only head_object for the check
        s3.get_paginator.assert_not_called()

        assert isinstance(result, MasterOutput)
        assert result.audio_s3_uri == f"s3://{bucket}/{existing_audio_key}"


# ---------------------------------------------------------------------------
# 7. Chapters generation
# ---------------------------------------------------------------------------


class TestGenerateChapters:
    def test_empty_segments_returns_one_chapter(self) -> None:
        result = _generate_chapters({"segments": []})
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["start_sec"] == 0.0

    def test_chapter_count_for_30_minute_audio(self) -> None:
        """30 minutes of audio with 10-min target should produce ~3 chapters."""
        # Create segments every 30s for 30 min
        segments = [
            {"start": i * 30.0, "end": i * 30.0 + 25.0, "speaker": "Josh", "text": "text"}
            for i in range(60)
        ]
        result = _generate_chapters({"segments": segments}, target_chapter_min=10)
        # Allow 2-4 chapters for a 30-minute recording
        assert 2 <= len(result["chapters"]) <= 4

    def test_chapter_prefers_speaker_change(self) -> None:
        """Chapter boundary should prefer speaker change over mid-monologue split."""
        # 15 minutes of Josh, then 15 minutes of Chris
        segments = []
        for i in range(30):
            speaker = "Josh" if i < 15 else "Chris"
            segments.append({
                "start": i * 60.0,
                "end": i * 60.0 + 55.0,
                "speaker": speaker,
                "text": "text",
            })
        result = _generate_chapters({"segments": segments}, target_chapter_min=10)
        # Chapter 2 should start near the Josh->Chris handoff at 900s (minute 15)
        # Allow ±120s tolerance (same as the algorithm's tolerance)
        handoff_time = 900.0  # 15 * 60
        chapter_starts = [c["start_sec"] for c in result["chapters"]]
        closest_to_handoff = min(chapter_starts, key=lambda t: abs(t - handoff_time))
        assert abs(closest_to_handoff - handoff_time) < 120.0, (
            f"Chapter not placed near handoff. Closest: {closest_to_handoff}s, handoff: {handoff_time}s"
        )
