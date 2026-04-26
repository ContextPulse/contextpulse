# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for the orphan-recovery flow.

Covers:
  - find_orphan_recordings: skips in-flight files, returns old ones
  - transcribe_orphan: writes a .txt sidecar, returns text
  - recover_all: summary, --delete, error preservation
"""

from __future__ import annotations

import io
import os
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np


def _make_wav(path: Path, seconds: float = 0.5) -> None:
    n = int(seconds * 16000)
    audio = np.zeros((n, 1), dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(audio.tobytes())
    path.write_bytes(buf.getvalue())


def _age(path: Path, seconds: float) -> None:
    """Backdate mtime so the file appears `seconds` old."""
    target = time.time() - seconds
    os.utime(path, (target, target))


# ── find_orphan_recordings ────────────────────────────────────────────


class TestFindOrphans:
    def test_returns_files_older_than_threshold(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import find_orphan_recordings

        old = tmp_path / "old.wav"
        _make_wav(old)
        _age(old, 600)  # 10 min old

        recent = tmp_path / "recent.wav"
        _make_wav(recent)
        # mtime is now-ish — should be skipped at min_age_seconds=120

        result = find_orphan_recordings(tmp_path, min_age_seconds=120)
        names = {p.name for p in result}
        assert "old.wav" in names
        assert "recent.wav" not in names

    def test_skips_in_flight_files(self, tmp_path: Path):
        """Files younger than min_age_seconds are NOT returned —
        they may still be being written to disk by an active
        transcription thread."""
        from contextpulse_voice.orphan_recovery import find_orphan_recordings

        f = tmp_path / "in-flight.wav"
        _make_wav(f)
        result = find_orphan_recordings(tmp_path, min_age_seconds=120)
        assert result == []

    def test_returns_empty_for_missing_dir(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import find_orphan_recordings

        missing = tmp_path / "nope"
        assert find_orphan_recordings(missing) == []

    def test_only_returns_wav_files(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import find_orphan_recordings

        wav = tmp_path / "a.wav"
        _make_wav(wav)
        _age(wav, 600)
        txt = tmp_path / "a.txt"
        txt.write_text("hello")
        _age(txt, 600)

        names = {p.name for p in find_orphan_recordings(tmp_path)}
        assert "a.wav" in names
        assert "a.txt" not in names


# ── transcribe_orphan ─────────────────────────────────────────────────


class TestTranscribeOrphan:
    def test_writes_txt_sidecar_with_transcribed_text(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import transcribe_orphan

        wav = tmp_path / "orphan.wav"
        _make_wav(wav)
        transcriber = MagicMock()
        transcriber.transcribe.return_value = "recovered text from orphan"

        result = transcribe_orphan(wav, transcriber)
        assert result is not None
        assert result[0] == "recovered text from orphan"
        sidecar = wav.with_suffix(".txt")
        assert sidecar.exists()
        assert "recovered text from orphan" in sidecar.read_text(encoding="utf-8")

    def test_returns_none_on_empty_transcript(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import transcribe_orphan

        wav = tmp_path / "orphan.wav"
        _make_wav(wav)
        transcriber = MagicMock()
        transcriber.transcribe.return_value = ""

        result = transcribe_orphan(wav, transcriber)
        assert result is None
        # No empty .txt left around
        assert not wav.with_suffix(".txt").exists()

    def test_returns_none_on_transcriber_exception(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import transcribe_orphan

        wav = tmp_path / "orphan.wav"
        _make_wav(wav)
        transcriber = MagicMock()
        transcriber.transcribe.side_effect = RuntimeError("model crashed")

        result = transcribe_orphan(wav, transcriber)
        assert result is None
        # Original WAV must still be on disk for retry
        assert wav.exists()


# ── recover_all ───────────────────────────────────────────────────────


class TestRecoverAll:
    def test_summary_counts(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import recover_all

        for i in range(3):
            f = tmp_path / f"o{i}.wav"
            _make_wav(f)
            _age(f, 600)

        transcriber = MagicMock()
        transcriber.transcribe.return_value = "ok"

        summary = recover_all(
            tmp_path,
            transcriber=transcriber,
            min_age_seconds=120,
        )
        assert summary["recovered"] == 3
        assert summary["failed"] == 0
        assert summary["skipped"] == 0
        # WAVs remain by default (delete=False)
        assert len(list(tmp_path.glob("*.wav"))) == 3
        assert len(list(tmp_path.glob("*.txt"))) == 3

    def test_delete_removes_wav_after_success(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import recover_all

        wav = tmp_path / "o.wav"
        _make_wav(wav)
        _age(wav, 600)
        transcriber = MagicMock()
        transcriber.transcribe.return_value = "recovered"

        recover_all(
            tmp_path,
            transcriber=transcriber,
            min_age_seconds=120,
            delete_on_success=True,
        )
        assert not wav.exists()
        assert wav.with_suffix(".txt").exists()

    def test_delete_preserves_wav_on_failure(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import recover_all

        wav = tmp_path / "o.wav"
        _make_wav(wav)
        _age(wav, 600)
        transcriber = MagicMock()
        transcriber.transcribe.side_effect = RuntimeError("nope")

        summary = recover_all(
            tmp_path,
            transcriber=transcriber,
            min_age_seconds=120,
            delete_on_success=True,
        )
        assert summary["failed"] == 1
        # WAV must remain so the user can retry
        assert wav.exists()
        # No .txt sidecar written on failure
        assert not wav.with_suffix(".txt").exists()

    def test_skips_in_flight_files(self, tmp_path: Path):
        from contextpulse_voice.orphan_recovery import recover_all

        old = tmp_path / "old.wav"
        _make_wav(old)
        _age(old, 600)
        new = tmp_path / "new.wav"
        _make_wav(new)  # in-flight

        transcriber = MagicMock()
        transcriber.transcribe.return_value = "ok"

        summary = recover_all(
            tmp_path,
            transcriber=transcriber,
            min_age_seconds=120,
        )
        assert summary["recovered"] == 1
        assert new.exists()
        assert not new.with_suffix(".txt").exists()
