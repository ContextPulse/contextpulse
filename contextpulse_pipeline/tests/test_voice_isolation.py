# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for voice_isolation (Stage 6 — target speaker extraction)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from contextpulse_pipeline.speaker_fingerprint import (
    FingerprintResult,
    SpeakerCluster,
)
from contextpulse_pipeline.voice_isolation import (
    IsolationResult,
    StubTargetSpeakerExtractor,
    WeSepExtractor,
    extract_per_speaker_tracks,
    write_wav_mono,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tone_wav(path: Path, *, freq_hz: float, duration_sec: float, sample_rate: int = 16000) -> None:
    n = int(duration_sec * sample_rate)
    t = np.linspace(0.0, duration_sec, num=n, endpoint=False, dtype=np.float64)
    samples = (np.sin(2 * np.pi * freq_hz * t) * 0.3).astype(np.float32)
    write_wav_mono(path, samples, sample_rate=sample_rate)


# ---------------------------------------------------------------------------
# WeSepExtractor lazy-load contract
# ---------------------------------------------------------------------------


class TestWeSepExtractorLazyLoad:
    def test_construct_does_not_import_wesep(self) -> None:
        """Constructing WeSepExtractor must not trigger the wesep import."""
        import sys

        before = "wesep" in sys.modules
        ext = WeSepExtractor()
        after = "wesep" in sys.modules
        assert before == after, "Constructing WeSepExtractor must not import wesep"
        assert ext._model is None

    def test_extract_raises_actionable_error_when_wesep_missing(self, monkeypatch) -> None:
        """If wesep isn't installed, .extract() must raise RuntimeError with
        a message that explains how to fix it."""
        import importlib.abc
        import importlib.machinery
        import sys

        for k in [k for k in sys.modules if k.startswith("wesep")]:
            monkeypatch.delitem(sys.modules, k, raising=False)

        class _BlockWesep(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.startswith("wesep"):
                    raise ImportError("wesep blocked for test")
                return None

        finder = _BlockWesep()
        sys.meta_path.insert(0, finder)
        try:
            ext = WeSepExtractor()
            audio = np.zeros(16000, dtype=np.float32)
            emb = np.zeros(192, dtype=np.float32)
            try:
                ext.extract(audio, emb, sample_rate=16000)
            except RuntimeError as exc:
                msg = str(exc).lower()
                assert "wesep" in msg
                assert "install" in msg or "voice_isolation" in msg
                return
            raise AssertionError("Expected RuntimeError")
        finally:
            sys.meta_path.remove(finder)

    def test_extract_rejects_multidim_audio(self) -> None:
        ext = WeSepExtractor()
        bad = np.zeros((2, 16000), dtype=np.float32)
        try:
            ext.extract(bad, np.zeros(192, dtype=np.float32), sample_rate=16000)
        except ValueError as exc:
            assert "mono" in str(exc).lower() or "1-d" in str(exc).lower()
            return
        raise AssertionError("Expected ValueError")

    def test_extract_rejects_wrong_sample_rate(self) -> None:
        ext = WeSepExtractor()
        try:
            ext.extract(
                np.zeros(48000, dtype=np.float32),
                np.zeros(192, dtype=np.float32),
                sample_rate=48000,
            )
        except ValueError as exc:
            assert "16000" in str(exc) or "Hz" in str(exc)
            return
        raise AssertionError("Expected ValueError on non-16k sample rate")


# ---------------------------------------------------------------------------
# StubTargetSpeakerExtractor contract
# ---------------------------------------------------------------------------


class TestStubExtractor:
    def test_pass_through_preserves_length(self) -> None:
        ext = StubTargetSpeakerExtractor()
        audio = np.random.randn(16000).astype(np.float32)
        emb = np.array([0.5], dtype=np.float32)
        clean, conf = ext.extract(audio, emb, sample_rate=16000)
        assert len(clean) == len(audio)
        assert clean.dtype == np.float32
        # Confidence is in [0, 1]
        assert 0.0 <= conf <= 1.0

    def test_confidence_varies_with_enrollment(self) -> None:
        ext = StubTargetSpeakerExtractor()
        audio = np.random.randn(16000).astype(np.float32)
        _, conf_high = ext.extract(audio, np.array([1.0]), sample_rate=16000)
        _, conf_low = ext.extract(audio, np.array([-1.0]), sample_rate=16000)
        assert conf_high != conf_low


# ---------------------------------------------------------------------------
# extract_per_speaker_tracks orchestrator
# ---------------------------------------------------------------------------


class TestExtractPerSpeakerTracks:
    def test_produces_one_track_per_speaker_source_pair(self, tmp_path) -> None:
        """Two speakers × two sources → 4 isolated tracks."""
        # Two synthetic audio sources
        wav_a = tmp_path / "src_a.wav"
        wav_b = tmp_path / "src_b.wav"
        _write_tone_wav(wav_a, freq_hz=300, duration_sec=2.0)
        _write_tone_wav(wav_b, freq_hz=600, duration_sec=2.0)

        sha_a = "a" * 64
        sha_b = "b" * 64

        # Two speakers from Phase 1.5
        cluster_a = SpeakerCluster(
            label="speaker_A",
            member_indices=[0],
            centroid=np.array([0.5, 0.1, 0.2], dtype=np.float32),
        )
        cluster_b = SpeakerCluster(
            label="speaker_B",
            member_indices=[1],
            centroid=np.array([-0.3, 0.7, 0.1], dtype=np.float32),
        )
        fingerprint = FingerprintResult(chunks=[], clusters=[cluster_a, cluster_b])

        result = extract_per_speaker_tracks(
            fingerprint=fingerprint,
            audio_paths={sha_a: wav_a, sha_b: wav_b},
            extractor=StubTargetSpeakerExtractor(),
            output_dir=tmp_path / "out",
            container="test-container",
            source_tiers={sha_a: "A", sha_b: "C"},
            source_filenames={sha_a: "src_a.wav", sha_b: "src_b.wav"},
        )

        assert isinstance(result, IsolationResult)
        assert result.n_tracks == 4  # 2 speakers × 2 sources
        assert result.speakers == ["speaker_A", "speaker_B"]
        # Each output WAV exists on disk and is non-empty
        for track in result.tracks:
            assert track.output_path.exists()
            with wave.open(str(track.output_path), "rb") as r:
                assert r.getnchannels() == 1
                assert r.getnframes() > 0

    def test_returns_empty_when_no_clusters(self, tmp_path) -> None:
        fingerprint = FingerprintResult(chunks=[], clusters=[])
        result = extract_per_speaker_tracks(
            fingerprint=fingerprint,
            audio_paths={},
            extractor=StubTargetSpeakerExtractor(),
            output_dir=tmp_path / "out",
            container="test-container",
        )
        assert result.n_tracks == 0
        assert result.skipped  # has at least one reason

    def test_isolation_result_to_json_roundtrip(self, tmp_path) -> None:
        wav = tmp_path / "src.wav"
        _write_tone_wav(wav, freq_hz=400, duration_sec=1.0)
        sha = "c" * 64
        cluster = SpeakerCluster(
            label="speaker_A",
            member_indices=[0],
            centroid=np.array([0.1], dtype=np.float32),
        )
        fingerprint = FingerprintResult(chunks=[], clusters=[cluster])

        result = extract_per_speaker_tracks(
            fingerprint=fingerprint,
            audio_paths={sha: wav},
            extractor=StubTargetSpeakerExtractor(),
            output_dir=tmp_path / "out",
            container="test-container",
            source_tiers={sha: "A"},
        )

        json_path = tmp_path / "isolation.json"
        text = result.to_json(path=json_path)
        assert "test-container" in text
        assert "speaker_A" in text
        assert json_path.exists()
