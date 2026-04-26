# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for crash-safe audio persistence in the voice pipeline.

Acceptance criteria (from feat/voice-audio-persistence-orphan-recovery):
  - WAV is written to disk BEFORE the transcriber is invoked.
  - WAV is deleted AFTER a successful TRANSCRIPTION event is emitted.
  - WAV is PRESERVED if transcribe raises, returns empty, or any
    other failure path runs.
  - Old recordings are pruned (file count + age cap) on the success path.

These tests fail today — persistence does not exist yet.
"""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _make_wav_bytes(seconds: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Build a small valid WAV byte string for tests."""
    n_samples = int(seconds * sample_rate)
    audio = np.zeros((n_samples, 1), dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


# ── Recorder: save_wav_bytes ──────────────────────────────────────────


class TestSaveWavBytes:
    def test_save_wav_bytes_writes_file(self, tmp_path: Path):
        from contextpulse_voice.recorder import save_wav_bytes

        target = tmp_path / "out.wav"
        data = _make_wav_bytes()
        save_wav_bytes(data, target)
        assert target.exists()
        assert target.read_bytes() == data

    def test_save_wav_bytes_creates_parent_dir(self, tmp_path: Path):
        from contextpulse_voice.recorder import save_wav_bytes

        target = tmp_path / "deep" / "nested" / "out.wav"
        save_wav_bytes(_make_wav_bytes(), target)
        assert target.exists()

    def test_save_wav_bytes_atomic_no_partial_left(self, tmp_path: Path):
        """After a successful write there is no .partial sidecar."""
        from contextpulse_voice.recorder import save_wav_bytes

        target = tmp_path / "out.wav"
        save_wav_bytes(_make_wav_bytes(), target)
        leftovers = list(tmp_path.glob("*.partial"))
        assert leftovers == []

    def test_save_wav_bytes_empty_input_skipped(self, tmp_path: Path):
        """Empty bytes must not produce a 0-byte file."""
        from contextpulse_voice.recorder import save_wav_bytes

        target = tmp_path / "out.wav"
        save_wav_bytes(b"", target)
        assert not target.exists()


# ── VoiceModule: persistence wired into transcribe pipeline ───────────


@pytest.fixture
def module_with_recordings_dir(tmp_path: Path):
    """A VoiceModule wired with mocks and a tmp recordings dir."""
    with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
        mock_cfg.return_value = {
            "hotkey": "ctrl+space",
            "fix_hotkey": "ctrl+shift+space",
            "whisper_model": "base",
            "always_use_llm": False,
            "anthropic_api_key": "",
        }
        # Redirect recordings dir to tmp_path for isolation
        with patch("contextpulse_voice.voice_module.RECORDINGS_DIR", tmp_path):
            from contextpulse_voice.voice_module import VoiceModule

            m = VoiceModule(model_size="base")
            m._transcriber = MagicMock()
            m._transcriber.transcribe.return_value = "hello world"
            received: list = []
            m.register(lambda e: received.append(e))
            m._running = True
            yield m, received, tmp_path


class TestVoiceModulePersistence:
    def test_persists_wav_before_calling_transcriber(self, module_with_recordings_dir, tmp_path):
        """When transcribe is called, the WAV must already be on disk.

        This is the crash-safety property: if the daemon dies during
        transcribe, the WAV is recoverable from the recordings dir.
        """
        module, _received, recordings_dir = module_with_recordings_dir
        wav_data = _make_wav_bytes(seconds=1.0)

        # Inspect disk at the moment transcribe is invoked
        observed_files: list[list[Path]] = []

        def _capture_disk_state(*_args, **_kwargs):
            observed_files.append(list(recordings_dir.glob("*.wav")))
            return "hello world"

        module._transcriber.transcribe.side_effect = _capture_disk_state
        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(wav_data, "code.exe", "test.py")

        # At least one WAV existed on disk when transcribe was called
        assert observed_files, "transcribe was never called"
        assert len(observed_files[0]) == 1, (
            f"expected 1 WAV present during transcribe, got {observed_files[0]}"
        )

    def test_deletes_wav_on_successful_transcription(self, module_with_recordings_dir):
        """After a successful TRANSCRIPTION event, WAV is removed."""
        module, received, recordings_dir = module_with_recordings_dir
        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "code.exe", "test.py")

        # TRANSCRIPTION event was emitted, AND no WAVs left behind
        from contextpulse_core.spine import EventType

        transcriptions = [e for e in received if e.event_type == EventType.TRANSCRIPTION]
        assert len(transcriptions) == 1
        leftover = list(recordings_dir.glob("*.wav"))
        assert leftover == [], f"expected no WAVs after success, got {leftover}"

    def test_preserves_wav_when_transcriber_raises(self, module_with_recordings_dir):
        """If transcribe raises, the WAV stays on disk for recovery."""
        module, _received, recordings_dir = module_with_recordings_dir
        module._transcriber.transcribe.side_effect = RuntimeError("whisper hung")
        with patch("contextpulse_voice.voice_module.paste_text"):
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "code.exe", "test.py")

        leftover = list(recordings_dir.glob("*.wav"))
        assert len(leftover) == 1, f"expected 1 WAV preserved after exception, got {leftover}"

    def test_preserves_wav_when_transcription_empty(self, module_with_recordings_dir):
        """Empty transcripts don't emit a TRANSCRIPTION event, so the
        audio is also unrecovered — keep the WAV so the user can retry
        with a larger model via orphan recovery."""
        module, _received, recordings_dir = module_with_recordings_dir
        module._transcriber.transcribe.return_value = ""
        with patch("contextpulse_voice.voice_module.paste_text"):
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "code.exe", "test.py")

        leftover = list(recordings_dir.glob("*.wav"))
        assert len(leftover) == 1, "expected WAV preserved when transcript was empty"

    def test_preserves_wav_when_duplicate_audio_skipped(self, module_with_recordings_dir):
        """Duplicate-hash short-circuit must NOT delete the prior WAV
        from a real successful transcription."""
        module, _received, recordings_dir = module_with_recordings_dir
        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                # First call succeeds — clears its WAV
                module._transcribe_and_paste(
                    _make_wav_bytes(seconds=0.5),
                    "code.exe",
                    "test.py",
                )
                assert list(recordings_dir.glob("*.wav")) == []
                # Second call with same bytes — duplicate-hash skip
                module._transcribe_and_paste(
                    _make_wav_bytes(seconds=0.5),
                    "code.exe",
                    "test.py",
                )
        # Duplicate path may persist + leave file (caller can clean up
        # via orphan recovery) OR may skip persisting altogether — both
        # are acceptable. What is NOT acceptable is corrupting the
        # disk state for unrelated WAVs from prior transcripts.
        leftover = list(recordings_dir.glob("*.wav"))
        assert len(leftover) <= 1


class TestRecordingCleanup:
    def test_cleanup_keeps_only_recent_files(self, tmp_path: Path):
        from contextpulse_voice.voice_module import _cleanup_old_recordings

        # 60 fake files; cleanup should keep at most 50 newest
        for i in range(60):
            f = tmp_path / f"dict_{i:03d}.wav"
            f.write_bytes(b"x")
            # Stagger mtimes so order is deterministic
            os_t = time.time() - (60 - i) * 60
            import os as _os

            _os.utime(f, (os_t, os_t))

        _cleanup_old_recordings(tmp_path, max_files=50, max_age_days=7)
        remaining = sorted(tmp_path.glob("*.wav"))
        assert len(remaining) == 50
        # Newest 50 = files 010..059
        names = [p.name for p in remaining]
        assert "dict_059.wav" in names
        assert "dict_009.wav" not in names

    def test_cleanup_removes_old_files_by_age(self, tmp_path: Path):
        from contextpulse_voice.voice_module import _cleanup_old_recordings

        # 5 files, all within count cap, but 2 older than 7 days
        old = tmp_path / "old.wav"
        old.write_bytes(b"x")
        import os as _os

        _os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
        new = tmp_path / "new.wav"
        new.write_bytes(b"x")

        _cleanup_old_recordings(tmp_path, max_files=50, max_age_days=7)
        assert new.exists()
        assert not old.exists()

    def test_cleanup_handles_missing_dir(self, tmp_path: Path):
        """Cleanup on a non-existent dir is a no-op, not a crash."""
        from contextpulse_voice.voice_module import _cleanup_old_recordings

        missing = tmp_path / "does-not-exist"
        # Must not raise
        _cleanup_old_recordings(missing, max_files=50, max_age_days=7)
