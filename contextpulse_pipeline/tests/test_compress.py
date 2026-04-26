"""Tests for contextpulse_pipeline.compress — mocked ffmpeg."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_pipeline.compress import AudioTooLargeError, compress_for_whisper

# 25 MB limit in bytes
LIMIT_BYTES = 25 * 1024 * 1024


class TestCompressForWhisper:
    def test_returns_output_path(self, tmp_path: Path) -> None:
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)  # 1 KB fake WAV
        out = tmp_path / "output.opus"

        def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            out.write_bytes(b"x" * 512)  # write fake output
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_fake_run):
            result = compress_for_whisper(src, out)
        assert result == out

    def test_ffmpeg_called_with_correct_args(self, tmp_path: Path) -> None:
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)
        out = tmp_path / "output.opus"

        captured: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured.append(cmd)
            out.write_bytes(b"x" * 100)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_capture_run):
            compress_for_whisper(src, out)

        assert len(captured) == 1
        cmd = captured[0]
        assert "ffmpeg" in cmd[0]
        assert "-ar" in cmd
        assert "16000" in cmd
        assert "-ac" in cmd
        assert "1" in cmd
        assert "-c:a" in cmd
        assert "libopus" in cmd
        assert "-b:a" in cmd
        assert "64k" in cmd

    def test_custom_bitrate_passed(self, tmp_path: Path) -> None:
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)
        out = tmp_path / "output.opus"
        captured: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured.append(cmd)
            out.write_bytes(b"x" * 100)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_capture_run):
            compress_for_whisper(src, out, bitrate="32k")

        cmd = captured[0]
        assert "32k" in cmd

    def test_output_too_large_raises(self, tmp_path: Path) -> None:
        """If compressed output is still >25 MB, raise AudioTooLargeError."""
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)
        out = tmp_path / "output.opus"

        def _big_output(cmd: list[str], **kwargs: object) -> MagicMock:
            out.write_bytes(b"x" * (LIMIT_BYTES + 1))  # just over 25 MB
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_big_output):
            with pytest.raises(AudioTooLargeError):
                compress_for_whisper(src, out)

    def test_ffmpeg_nonzero_exit_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)
        out = tmp_path / "output.opus"

        def _fail(cmd: list[str], **kwargs: object) -> MagicMock:
            m = MagicMock()
            m.returncode = 1
            m.stderr = b"error"
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_fail):
            with pytest.raises(RuntimeError, match="ffmpeg"):
                compress_for_whisper(src, out)

    def test_default_output_path_derived(self, tmp_path: Path) -> None:
        """If no output path specified, derive from input with .opus suffix."""
        src = tmp_path / "input.wav"
        src.write_bytes(b"x" * 1024)
        expected_out = tmp_path / "input.opus"

        def _create_out(cmd: list[str], **kwargs: object) -> MagicMock:
            expected_out.write_bytes(b"x" * 100)
            m = MagicMock()
            m.returncode = 0
            return m

        with patch("contextpulse_pipeline.compress.subprocess.run", side_effect=_create_out):
            result = compress_for_whisper(src)

        assert result == expected_out
