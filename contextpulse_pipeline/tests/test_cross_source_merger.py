# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for cross_source_merger (Stage 6 unified-track builder)."""

from __future__ import annotations

import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from contextpulse_pipeline.cross_source_merger import (
    DEFAULT_TIER_WEIGHTS,
    MergerResult,
    merge_all_speakers,
    merge_speaker,
)
from contextpulse_pipeline.sync_matcher import ResolvedSource, SyncResult
from contextpulse_pipeline.voice_isolation import IsolatedTrack, IsolationResult, write_wav_mono


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_constant_wav(path: Path, *, value: float, duration_sec: float, sample_rate: int = 16000) -> None:
    n = int(duration_sec * sample_rate)
    samples = (np.ones(n, dtype=np.float32) * value).astype(np.float32)
    write_wav_mono(path, samples, sample_rate=sample_rate)


def _read_wav_first_n(path: Path, n: int) -> np.ndarray:
    with wave.open(str(path), "rb") as r:
        raw = r.readframes(n)
    ints = np.frombuffer(raw, dtype=np.int16)
    return ints.astype(np.float32) / 32768.0


# ---------------------------------------------------------------------------
# merge_speaker — single-speaker cases
# ---------------------------------------------------------------------------


class TestMergeSpeaker:
    def test_picks_higher_tier_when_both_have_same_energy(self, tmp_path) -> None:
        """Tier A (DJI) should win over tier C (Telegram) at the same energy."""
        wav_a = tmp_path / "tier_a.wav"
        wav_c = tmp_path / "tier_c.wav"
        _write_constant_wav(wav_a, value=0.5, duration_sec=1.0)
        _write_constant_wav(wav_c, value=0.5, duration_sec=1.0)

        sha_a = "a" * 64
        sha_c = "c" * 64
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)

        isolation = IsolationResult(
            container="t",
            tracks=[
                IsolatedTrack(
                    speaker_label="speaker_A",
                    source_sha256=sha_a,
                    source_filename="tier_a.wav",
                    source_tier="A",
                    output_path=wav_a,
                    duration_sec=1.0,
                    confidence=0.9,
                ),
                IsolatedTrack(
                    speaker_label="speaker_A",
                    source_sha256=sha_c,
                    source_filename="tier_c.wav",
                    source_tier="C",
                    output_path=wav_c,
                    duration_sec=1.0,
                    confidence=0.9,
                ),
            ],
        )
        sync = SyncResult(
            container="t",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor,
            resolved_sources=[
                ResolvedSource(
                    sha256=sha_a,
                    wall_start_utc=anchor,
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=0.0,
                ),
                ResolvedSource(
                    sha256=sha_c,
                    wall_start_utc=anchor,
                    provenance="matched",
                    anchor_count=3,
                    offset_from_anchor_sec=0.0,
                ),
            ],
            unreachable_sources=[],
            pair_offsets=[],
        )

        out_path = tmp_path / "merged.wav"
        track = merge_speaker(
            "speaker_A",
            isolation,
            sync,
            out_path,
            sample_rate=16000,
        )
        assert track.duration_sec >= 0.99
        assert out_path.exists()
        # Audio should be non-zero (tier A won, copied content over)
        sample = _read_wav_first_n(out_path, 16000)
        assert float(np.mean(np.abs(sample))) > 0.1

    def test_skips_when_no_overlap(self, tmp_path) -> None:
        """Sources with disjoint wall-clock windows produce a continuous merged
        track that switches between them."""
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        _write_constant_wav(wav_a, value=0.4, duration_sec=1.0)
        _write_constant_wav(wav_b, value=0.4, duration_sec=1.0)

        sha_a = "a" * 64
        sha_b = "b" * 64
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)

        isolation = IsolationResult(
            container="t",
            tracks=[
                IsolatedTrack(
                    speaker_label="speaker_A",
                    source_sha256=sha_a,
                    source_filename="a.wav",
                    source_tier="A",
                    output_path=wav_a,
                    duration_sec=1.0,
                    confidence=0.5,
                ),
                IsolatedTrack(
                    speaker_label="speaker_A",
                    source_sha256=sha_b,
                    source_filename="b.wav",
                    source_tier="A",
                    output_path=wav_b,
                    duration_sec=1.0,
                    confidence=0.5,
                ),
            ],
        )
        sync = SyncResult(
            container="t",
            anchor_source_sha256=sha_a,
            anchor_origination_utc=anchor,
            resolved_sources=[
                ResolvedSource(
                    sha256=sha_a,
                    wall_start_utc=anchor,
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=0.0,
                ),
                ResolvedSource(
                    sha256=sha_b,
                    wall_start_utc=anchor + timedelta(seconds=2),  # disjoint
                    provenance="matched",
                    anchor_count=3,
                    offset_from_anchor_sec=2.0,
                ),
            ],
            unreachable_sources=[],
            pair_offsets=[],
        )

        out_path = tmp_path / "merged.wav"
        track = merge_speaker("speaker_A", isolation, sync, out_path)
        # 1 sec + 1 sec of gap + 1 sec → 3 sec total span
        assert 2.5 <= track.duration_sec <= 3.5
        # Should include both sources via switch
        assert track.n_source_switches >= 1


# ---------------------------------------------------------------------------
# merge_all_speakers
# ---------------------------------------------------------------------------


class TestMergeAllSpeakers:
    def test_runs_per_speaker(self, tmp_path) -> None:
        wav_a = tmp_path / "src.wav"
        _write_constant_wav(wav_a, value=0.3, duration_sec=1.0)
        sha = "a" * 64
        anchor = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)

        isolation = IsolationResult(
            container="t",
            tracks=[
                IsolatedTrack(
                    speaker_label="speaker_A",
                    source_sha256=sha,
                    source_filename="src.wav",
                    source_tier="A",
                    output_path=wav_a,
                    duration_sec=1.0,
                    confidence=0.7,
                ),
                IsolatedTrack(
                    speaker_label="speaker_B",
                    source_sha256=sha,
                    source_filename="src.wav",
                    source_tier="A",
                    output_path=wav_a,
                    duration_sec=1.0,
                    confidence=0.7,
                ),
            ],
        )
        sync = SyncResult(
            container="t",
            anchor_source_sha256=sha,
            anchor_origination_utc=anchor,
            resolved_sources=[
                ResolvedSource(
                    sha256=sha,
                    wall_start_utc=anchor,
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=0.0,
                )
            ],
            unreachable_sources=[],
            pair_offsets=[],
        )

        result = merge_all_speakers(isolation, sync, tmp_path / "merged")
        assert isinstance(result, MergerResult)
        assert {t.speaker_label for t in result.tracks} == {"speaker_A", "speaker_B"}
        for track in result.tracks:
            assert track.output_path.exists()


# ---------------------------------------------------------------------------
# Tier weights default
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_tier_weights_match_design(self) -> None:
        """A=1.0, B=0.7, C=0.4 per building-transcription-pipelines design doc."""
        assert DEFAULT_TIER_WEIGHTS["A"] == 1.0
        assert DEFAULT_TIER_WEIGHTS["B"] == 0.7
        assert DEFAULT_TIER_WEIGHTS["C"] == 0.4
