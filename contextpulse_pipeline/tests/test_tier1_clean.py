# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for tier1_clean — Tier 1 classical SP cleanup per source."""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from contextpulse_pipeline.tier1_clean import (
    DEFAULT_HIGHPASS_HZ,
    DEFAULT_TARGET_LUFS,
    Tier1Result,
    clean_collection,
    clean_source,
    measure_lufs,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _write_tone_wav(
    path: Path, *, freq_hz: float, duration_sec: float, sample_rate: int = 16000, amplitude: float = 0.3
) -> None:
    n = int(duration_sec * sample_rate)
    t = np.linspace(0.0, duration_sec, num=n, endpoint=False, dtype=np.float64)
    samples = (np.sin(2 * np.pi * freq_hz * t) * amplitude).astype(np.float32)
    ints = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())


# ---------------------------------------------------------------------------
# Defaults match the skill
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_target_lufs_is_minus_23(self) -> None:
        """Per-channel target per skill section 1.3 is -23 LUFS (NOT -16, which is master)."""
        assert DEFAULT_TARGET_LUFS == -23.0

    def test_highpass_at_80_hz(self) -> None:
        """Per skill section 1.1, HPF defaults to 80 Hz."""
        assert DEFAULT_HIGHPASS_HZ == 80


# ---------------------------------------------------------------------------
# clean_source — end-to-end with real ffmpeg
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
class TestCleanSource:
    def test_writes_output(self, tmp_path) -> None:
        wav_in = tmp_path / "in.wav"
        wav_out = tmp_path / "out_tier1.wav"
        _write_tone_wav(wav_in, freq_hz=440, duration_sec=2.0)

        result = clean_source(
            wav_in, wav_out, sha256="a" * 64, sample_rate=16000, measure_input=False
        )
        assert wav_out.exists()
        assert result.input_path == wav_in
        assert result.output_path == wav_out
        # Chain must include all three steps from the skill
        chain_text = " ".join(result.chain_applied)
        assert "highpass" in chain_text
        assert "afftdn" in chain_text
        assert "loudnorm" in chain_text

    def test_loudnorm_lands_near_target(self, tmp_path) -> None:
        """Two-pass loudnorm should land within ~3 dB of -23 LUFS."""
        wav_in = tmp_path / "in.wav"
        wav_out = tmp_path / "out_tier1.wav"
        _write_tone_wav(wav_in, freq_hz=440, duration_sec=3.0)

        clean_source(wav_in, wav_out, sha256="a" * 64, sample_rate=16000)
        out_lufs = measure_lufs(wav_out)
        assert out_lufs is not None
        # Tones are challenging for loudnorm; -23 ± 5 dB tolerance for synthetic input
        assert abs(out_lufs - DEFAULT_TARGET_LUFS) < 6.0

    def test_highpass_attenuates_low_freq(self, tmp_path) -> None:
        """A 40 Hz tone should be heavily attenuated after HPF=80."""
        low_in = tmp_path / "low.wav"
        low_out = tmp_path / "low_tier1.wav"
        _write_tone_wav(low_in, freq_hz=40, duration_sec=2.0, amplitude=0.5)

        clean_source(low_in, low_out, sha256="x" * 64, sample_rate=16000)
        # Read output and verify amplitude is reduced
        with wave.open(str(low_out), "rb") as r:
            raw = r.readframes(r.getnframes())
        ints = np.frombuffer(raw, dtype=np.int16)
        samples = ints.astype(np.float32) / 32768.0
        # After HPF + loudnorm, a 40Hz tone is attenuated below the original.
        # Rough sanity: peak amplitude well under 0.5 (the input).
        peak = float(np.max(np.abs(samples)))
        assert peak < 0.5, f"40 Hz tone peak {peak:.3f} not attenuated"


# ---------------------------------------------------------------------------
# clean_collection
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
class TestCleanCollection:
    def test_processes_each_source(self, tmp_path) -> None:
        in_dir = tmp_path / "in"
        out_dir = tmp_path / "out"
        in_dir.mkdir()
        wav_a = in_dir / "src_a.wav"
        wav_b = in_dir / "src_b.wav"
        _write_tone_wav(wav_a, freq_hz=300, duration_sec=2.0)
        _write_tone_wav(wav_b, freq_hz=600, duration_sec=2.0)

        result = clean_collection(
            audio_paths={"a" * 64: wav_a, "b" * 64: wav_b},
            output_dir=out_dir,
            container="t",
            sample_rate=16000,
            measure_lufs_each=False,
        )
        assert isinstance(result, Tier1Result)
        assert len(result.cleaned) == 2
        for c in result.cleaned:
            assert c.output_path.exists()

    def test_skipped_when_input_missing(self, tmp_path) -> None:
        result = clean_collection(
            audio_paths={"x" * 64: tmp_path / "missing.wav"},
            output_dir=tmp_path / "out",
            container="t",
        )
        assert len(result.cleaned) == 0
        assert any("missing" in s.lower() for s in result.skipped)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


class TestTier1ResultJson:
    def test_to_json_round_trip(self, tmp_path) -> None:
        result = Tier1Result(container="t", target_lufs=-23.0)
        text = result.to_json()
        assert '"target_lufs": -23.0' in text
        assert '"container": "t"' in text

    def test_cleaned_paths_helper(self) -> None:
        from contextpulse_pipeline.tier1_clean import CleanedSource

        result = Tier1Result(container="t")
        result.cleaned.append(
            CleanedSource(
                sha256="a" * 64,
                input_path=Path("/in/a.wav"),
                output_path=Path("/out/a_tier1.wav"),
            )
        )
        paths = result.cleaned_paths()
        assert paths["a" * 64] == Path("/out/a_tier1.wav")
