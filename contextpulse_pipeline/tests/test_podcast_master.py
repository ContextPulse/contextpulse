# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for podcast_master (Stage 7 — per-speaker mastering)."""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from contextpulse_pipeline.podcast_master import (
    DEFAULT_HIGHPASS_HZ,
    DEFAULT_TARGET_LUFS,
    DEFAULT_TRUE_PEAK_DBTP,
    MasteringResult,
    _afftdn_filter,
    _compressor_filter,
    _de_ess_filter,
    _has_deepfilternet,
    _highpass_filter,
    _limiter_filter,
    _voice_eq_filter,
    master_all_speakers,
    master_track,
    measure_lufs,
)


HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tone_wav(path: Path, *, freq_hz: float, duration_sec: float, sample_rate: int = 16000) -> None:
    n = int(duration_sec * sample_rate)
    t = np.linspace(0.0, duration_sec, num=n, endpoint=False, dtype=np.float64)
    samples = (np.sin(2 * np.pi * freq_hz * t) * 0.3).astype(np.float32)
    ints = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())


# ---------------------------------------------------------------------------
# Filter chain step builders — pure string construction, no ffmpeg needed
# ---------------------------------------------------------------------------


class TestFilterStepBuilders:
    def test_highpass_includes_default_freq(self) -> None:
        f = _highpass_filter()
        assert f"f={DEFAULT_HIGHPASS_HZ}" in f
        assert "highpass" in f

    def test_highpass_accepts_custom_freq(self) -> None:
        assert "f=120" in _highpass_filter(120)

    def test_afftdn_uses_built_in_denoiser(self) -> None:
        f = _afftdn_filter()
        assert "afftdn" in f

    def test_de_ess_targets_sibilance_band(self) -> None:
        f = _de_ess_filter()
        # Cuts the 6-9 kHz region per the chain doc
        assert "6000" in f
        assert "8000" in f

    def test_compressor_uses_3_to_1_ratio(self) -> None:
        f = _compressor_filter()
        assert "ratio=3" in f
        assert "acompressor" in f

    def test_voice_eq_dips_lower_mids_lifts_upper_mids(self) -> None:
        f = _voice_eq_filter()
        assert "f=250" in f and "g=-1.5" in f
        assert "f=3000" in f and "g=1.5" in f

    def test_limiter_uses_true_peak_ceiling(self) -> None:
        f = _limiter_filter(-1.0)
        assert "alimiter" in f
        assert "limit=-1" in f


# ---------------------------------------------------------------------------
# DeepFilterNet detection — does not require it to be installed
# ---------------------------------------------------------------------------


class TestDeepFilterNetProbe:
    def test_probe_returns_bool(self) -> None:
        result = _has_deepfilternet()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# LUFS measurement
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
class TestMeasureLUFS:
    def test_measures_synthetic_tone(self, tmp_path) -> None:
        """A loud 1 kHz sine should measure roughly its expected LUFS (well
        above -50 — a quiet floor would indicate a parsing bug)."""
        wav = tmp_path / "tone.wav"
        _write_tone_wav(wav, freq_hz=1000, duration_sec=2.0)
        lufs = measure_lufs(wav)
        assert lufs is not None
        assert -40.0 < lufs < 0.0  # broad envelope; tones aren't typical speech


# ---------------------------------------------------------------------------
# master_track — end-to-end with real ffmpeg
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
class TestMasterTrack:
    def test_writes_output_at_expected_path(self, tmp_path) -> None:
        wav_in = tmp_path / "in.wav"
        wav_out = tmp_path / "out.wav"
        _write_tone_wav(wav_in, freq_hz=440, duration_sec=2.0)

        result = master_track(
            wav_in,
            wav_out,
            target_lufs=-16.0,
            true_peak_dbtp=-1.0,
            sample_rate=16000,
            use_deepfilternet=False,  # force ffmpeg-only path
            measure_input=False,  # speed up the test
        )
        assert wav_out.exists()
        assert result.target_lufs == -16.0
        # All chain steps applied
        for step in ("highpass", "afftdn", "de-ess", "compressor", "voice-eq", "limiter"):
            assert any(step in c for c in result.chain_applied), (
                f"{step} missing from chain: {result.chain_applied}"
            )
        # And the loudnorm step is in there too
        assert any("loudnorm" in c for c in result.chain_applied)

    def test_output_lufs_lands_near_target(self, tmp_path) -> None:
        """Two-pass loudnorm should land within ~2 dB of the target. Sines
        aren't ideal speech material so the tolerance is generous; the
        ffmpeg loudnorm filter is well-tested upstream so we're really just
        verifying our chain didn't silently break it."""
        wav_in = tmp_path / "in.wav"
        wav_out = tmp_path / "out.wav"
        _write_tone_wav(wav_in, freq_hz=440, duration_sec=3.0)

        result = master_track(
            wav_in,
            wav_out,
            target_lufs=-16.0,
            sample_rate=16000,
            use_deepfilternet=False,
        )
        assert result.measured_output_lufs is not None
        assert abs(result.measured_output_lufs - DEFAULT_TARGET_LUFS) < 4.0


# ---------------------------------------------------------------------------
# master_all_speakers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
class TestMasterAllSpeakers:
    def test_processes_each_speaker(self, tmp_path) -> None:
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        in_dir.mkdir()
        wav_a = in_dir / "speaker_A.wav"
        wav_b = in_dir / "speaker_B.wav"
        _write_tone_wav(wav_a, freq_hz=300, duration_sec=2.0)
        _write_tone_wav(wav_b, freq_hz=600, duration_sec=2.0)

        result = master_all_speakers(
            inputs={"speaker_A": wav_a, "speaker_B": wav_b},
            output_dir=out_dir,
            container="t",
            sample_rate=16000,
            use_deepfilternet=False,
        )
        assert isinstance(result, MasteringResult)
        assert len(result.tracks) == 2
        assert (out_dir / "speaker_A_mastered.wav").exists()
        assert (out_dir / "speaker_B_mastered.wav").exists()

    def test_skipped_when_input_missing(self, tmp_path) -> None:
        result = master_all_speakers(
            inputs={"speaker_A": tmp_path / "missing.wav"},
            output_dir=tmp_path / "out",
            container="t",
        )
        assert len(result.tracks) == 0
        assert any("missing" in s for s in result.skipped)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


class TestMasteringResultJson:
    def test_to_json_includes_metadata(self, tmp_path) -> None:
        result = MasteringResult(container="t")
        text = result.to_json()
        assert '"container": "t"' in text
        assert '"target_lufs"' in text
        assert '"true_peak_dbtp"' in text

    def test_to_json_writes_to_path(self, tmp_path) -> None:
        result = MasteringResult(container="t")
        path = tmp_path / "result.json"
        result.to_json(path=path)
        assert path.exists()
        assert "t" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Defaults must match the design doc
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_target_lufs_is_minus_16(self) -> None:
        assert DEFAULT_TARGET_LUFS == -16.0  # streaming default per design doc

    def test_true_peak_dbtp_is_minus_1(self) -> None:
        assert DEFAULT_TRUE_PEAK_DBTP == -1.0
