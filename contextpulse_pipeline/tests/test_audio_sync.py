# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for audio_sync (A.3b — audio cross-correlation refinement of pair offsets)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from contextpulse_pipeline.audio_sync import (
    cross_correlate_lag,
    load_audio_window,
    refine_pair_offset,
    refine_sync_result,
)
from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection
from contextpulse_pipeline.sync_matcher import (
    AnchorPair,
    ResolvedSource,
    SyncResult,
)

# ----------------------------------------------------------------------------
# Synthetic audio helpers
# ----------------------------------------------------------------------------


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    """Write a mono float32 array as 16-bit PCM WAV."""
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def _impulse_train(
    duration_sec: float,
    sample_rate: int = 16000,
    impulse_times: tuple[float, ...] = (1.0, 5.0, 10.0, 17.5, 23.0),
    seed: int = 0,
) -> np.ndarray:
    """Audio: low-amplitude noise + sharp impulses at given times.

    Cross-correlation of two such signals (offset by a known amount) has a
    very sharp peak; ideal for testing sub-sample lag estimation.
    """
    n = int(duration_sec * sample_rate)
    rng = np.random.default_rng(seed)
    audio = (rng.standard_normal(n) * 0.01).astype(np.float32)
    for t in impulse_times:
        idx = int(t * sample_rate)
        if 0 <= idx < n:
            audio[idx] = 1.0
    return audio


# ----------------------------------------------------------------------------
# load_audio_window
# ----------------------------------------------------------------------------


class TestLoadAudioWindow:
    def test_extracts_correct_duration(self, tmp_path: Path) -> None:
        sr = 16000
        audio = _impulse_train(duration_sec=30.0, sample_rate=sr)
        wav = tmp_path / "a.wav"
        _write_wav(wav, audio, sr)

        window = load_audio_window(wav, start_sec=5.0, duration_sec=4.0, sample_rate=sr)

        assert window.dtype == np.float32
        assert len(window) == int(4.0 * sr)

    def test_returns_correct_content(self, tmp_path: Path) -> None:
        """Window should contain the impulse if it falls inside the requested range."""
        sr = 16000
        audio = _impulse_train(duration_sec=30.0, sample_rate=sr, impulse_times=(7.5,))
        wav = tmp_path / "a.wav"
        _write_wav(wav, audio, sr)

        # Window [6.0, 10.0] should include impulse at 7.5
        window = load_audio_window(wav, start_sec=6.0, duration_sec=4.0, sample_rate=sr)
        assert np.max(np.abs(window)) > 0.5  # impulse is normalized to 1.0

        # Window [10.0, 14.0] should NOT include the impulse
        window2 = load_audio_window(wav, start_sec=10.0, duration_sec=4.0, sample_rate=sr)
        assert np.max(np.abs(window2)) < 0.1

    def test_clips_at_start(self, tmp_path: Path) -> None:
        """Negative start_sec should be clipped to 0 (or at least not crash)."""
        sr = 16000
        audio = _impulse_train(duration_sec=30.0, sample_rate=sr)
        wav = tmp_path / "a.wav"
        _write_wav(wav, audio, sr)

        window = load_audio_window(wav, start_sec=-2.0, duration_sec=4.0, sample_rate=sr)
        # Should return a window — possibly shorter, but not error
        assert window.dtype == np.float32
        assert len(window) > 0


# ----------------------------------------------------------------------------
# cross_correlate_lag
# ----------------------------------------------------------------------------


class TestCrossCorrelateLag:
    def test_zero_lag_for_identical_signals(self) -> None:
        sr = 16000
        a = _impulse_train(duration_sec=4.0, sample_rate=sr)
        b = a.copy()

        lag_sec, conf = cross_correlate_lag(a, b, max_lag_sec=1.0, sample_rate=sr)

        assert abs(lag_sec) < 1e-3  # within 1ms
        assert conf > 0.9  # identical signals → near-1.0 normalized correlation

    def test_known_lag_recovered(self) -> None:
        """Shift b by a known number of samples; cross-correlation should recover it.

        scipy convention: peak at lag k means a[t] ~= b[t - k]. If b[t] = a[t - 100]
        (b is shifted RIGHT, i.e. delayed by 100 samples), then a[t] = b[t + 100],
        so peak lag = -100.
        """
        sr = 16000
        a = _impulse_train(duration_sec=8.0, sample_rate=sr)
        b = np.zeros_like(a)
        b[100:] = a[:-100]

        lag_sec, conf = cross_correlate_lag(a, b, max_lag_sec=1.0, sample_rate=sr)
        assert abs(lag_sec - (-100 / sr)) < 1e-4
        assert conf > 0.5

    def test_negative_lag_recovered(self) -> None:
        """If b is shifted LEFT (leading a), peak lag is positive (scipy convention)."""
        sr = 16000
        a = _impulse_train(duration_sec=8.0, sample_rate=sr)
        b = np.zeros_like(a)
        b[:-200] = a[200:]

        lag_sec, conf = cross_correlate_lag(a, b, max_lag_sec=1.0, sample_rate=sr)
        assert abs(lag_sec - (200 / sr)) < 1e-4
        assert conf > 0.5

    def test_low_confidence_for_uncorrelated_noise(self) -> None:
        """Two independent noise streams should produce low-confidence (unreliable) lag."""
        sr = 16000
        rng_a = np.random.default_rng(1)
        rng_b = np.random.default_rng(2)
        a = rng_a.standard_normal(sr * 4).astype(np.float32) * 0.1
        b = rng_b.standard_normal(sr * 4).astype(np.float32) * 0.1

        _lag, conf = cross_correlate_lag(a, b, max_lag_sec=1.0, sample_rate=sr)
        assert conf < 0.1  # uncorrelated noise → tiny normalized peak


# ----------------------------------------------------------------------------
# refine_pair_offset
# ----------------------------------------------------------------------------


class TestRefinePairOffset:
    def test_synthetic_pair_recovers_true_offset(self, tmp_path: Path) -> None:
        """Two synthetic audio files, B starts 100s after A. After refinement,
        pair offset should match exactly even with jittered seed anchors."""
        sr = 16000
        # A starts at wall t=0, has impulses at 30, 60, 90, 120 (in A's clock)
        # B starts at wall t=100, so the same impulses appear at A.t=30 → B.t=-70 (before B starts)
        # Use impulses at A.t = 110, 130, 150 → those map to B.t = 10, 30, 50
        a_impulses = (110.0, 130.0, 150.0, 170.0, 190.0)
        # B's audio is just the second half of A, starting at A's wall-time 100
        true_offset_sec = 100.0
        a = _impulse_train(duration_sec=210.0, sample_rate=sr, impulse_times=a_impulses)
        b_impulses = tuple(t - true_offset_sec for t in a_impulses)
        b = _impulse_train(duration_sec=110.0, sample_rate=sr, impulse_times=b_impulses, seed=1)
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_wav(wav_a, a, sr)
        _write_wav(wav_b, b, sr)

        # Anchors with Whisper-style jitter (~+/-0.5s on each side)
        rng = np.random.default_rng(42)
        anchors: list[AnchorPair] = []
        for a_t, b_t in zip(a_impulses, b_impulses):
            jitter_a = float(rng.uniform(-0.5, 0.5))
            jitter_b = float(rng.uniform(-0.5, 0.5))
            anchors.append(
                AnchorPair(
                    ngram=f"impulse_{a_t}",
                    source_a="sha_a",
                    start_a_sec=a_t + jitter_a,
                    source_b="sha_b",
                    start_b_sec=b_t + jitter_b,
                )
            )

        refined = refine_pair_offset(
            audio_a_path=wav_a,
            audio_b_path=wav_b,
            anchors=anchors,
            sample_rate=sr,
            window_sec=4.0,
            min_confidence=0.1,
        )

        assert refined is not None
        # offset_sec convention: B.t - A.t = -true_offset (since B's clock starts 100s after A's)
        # for an impulse at A.t=110 == B.t=10: delta = 10 - 110 = -100
        assert abs(refined.offset_sec - (-true_offset_sec)) < 0.001  # within 1ms
        assert refined.std_dev_sec < 0.001  # essentially zero — synthetic perfect alignment
        assert refined.n_anchors >= 4  # at least 4 of 5 anchors retained

    def test_returns_none_when_all_low_confidence(self, tmp_path: Path) -> None:
        """If every anchor is in noise (no signal), refinement should refuse."""
        sr = 16000
        # Two pure-noise files (different seeds → uncorrelated)
        a = (np.random.default_rng(1).standard_normal(sr * 30) * 0.1).astype(np.float32)
        b = (np.random.default_rng(2).standard_normal(sr * 30) * 0.1).astype(np.float32)
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_wav(wav_a, a, sr)
        _write_wav(wav_b, b, sr)

        anchors = [
            AnchorPair(
                ngram=f"n{i}",
                source_a="sha_a",
                start_a_sec=5.0 + i * 5.0,
                source_b="sha_b",
                start_b_sec=5.0 + i * 5.0,
            )
            for i in range(5)
        ]

        refined = refine_pair_offset(
            audio_a_path=wav_a,
            audio_b_path=wav_b,
            anchors=anchors,
            sample_rate=sr,
            window_sec=2.0,
            min_confidence=0.2,  # well above noise floor (~0.02 typical)
        )

        assert refined is None or refined.n_anchors == 0


# ----------------------------------------------------------------------------
# refine_sync_result — synthetic end-to-end (no transcripts)
# ----------------------------------------------------------------------------


class TestRefineSyncResult:
    def test_passes_through_bwf_provenance_unchanged(self, tmp_path: Path) -> None:
        """Sources resolved via BWF (not pair-offset propagation) shouldn't be re-refined."""
        from datetime import datetime, timezone

        # Single BWF source — nothing to refine
        coll = RawSourceCollection(
            container="test",
            sources=[
                RawSource(
                    sha256="a" * 64,
                    file_path=str(tmp_path / "doesnt-exist.wav"),
                    container="test",
                    source_tier="A",
                    duration_sec=10.0,
                    sample_rate=16000,
                    channel_count=1,
                    codec="pcm_s16le",
                    bwf_origination=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    provenance="bwf",
                )
            ],
        )
        coarse = SyncResult(
            container="test",
            anchor_source_sha256="a" * 64,
            anchor_origination_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            resolved_sources=[
                ResolvedSource(
                    sha256="a" * 64,
                    wall_start_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=0.0,
                ),
            ],
            unreachable_sources=[],
            pair_offsets=[],
        )

        refined = refine_sync_result(coarse, coll, transcripts_dir=tmp_path)

        assert len(refined.resolved_sources) == 1
        assert refined.resolved_sources[0].provenance == "bwf"
        assert refined.resolved_sources[0].wall_start_utc == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )


# ----------------------------------------------------------------------------
# Real Josh hike integration — skip if data missing
# ----------------------------------------------------------------------------


JOSH_TRANSCRIPTS = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/transcripts"
)
JOSH_RAW_JSON = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/raw_sources.json"
)


@pytest.mark.skipif(
    not JOSH_TRANSCRIPTS.exists() or not JOSH_RAW_JSON.exists(),
    reason="A.2 transcription has not produced Josh hike transcripts yet",
)
@pytest.mark.slow
@pytest.mark.timeout(120)
class TestJoshHikeRefinement:
    def test_refinement_tightens_pair_offsets(self) -> None:
        from contextpulse_pipeline.sync_matcher import resolve_timeline

        coll = RawSourceCollection.from_json(path=JOSH_RAW_JSON)
        coarse = resolve_timeline(coll, JOSH_TRANSCRIPTS)
        coarse_stds = [p.std_dev_sec for p in coarse.pair_offsets]

        refined = refine_sync_result(
            coarse,
            coll,
            transcripts_dir=JOSH_TRANSCRIPTS,
            max_anchors_per_pair=20,
        )
        refined_stds = [p.std_dev_sec for p in refined.pair_offsets]

        # All pairs should still be present (no losses)
        assert len(refined.pair_offsets) == len(coarse.pair_offsets)
        # Median std should drop by at least 5x
        coarse_median = sorted(coarse_stds)[len(coarse_stds) // 2]
        refined_median = sorted(refined_stds)[len(refined_stds) // 2]
        assert refined_median < coarse_median / 5
        # And in absolute terms, well under 100ms
        assert refined_median < 0.1
        # 14/14 still resolve
        assert len(refined.resolved_sources) == len(coarse.resolved_sources)
        assert len(refined.unreachable_sources) == 0
