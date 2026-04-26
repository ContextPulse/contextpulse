"""ffmpeg WAV -> opus compression wrapper.

Pre-compresses audio before Groq Whisper API calls.
Rule #7: raw broadcast WAV exceeds the 25 MB API limit.
opus 64 kbps mono 16 kHz = 40-60x compression with negligible WER impact.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WHISPER_API_LIMIT_BYTES = 25 * 1024 * 1024  # 25 MB


class AudioTooLargeError(Exception):
    """Raised when compressed audio still exceeds the Whisper API 25 MB limit."""


def compress_for_whisper(
    input_path: Path,
    output_path: Path | None = None,
    bitrate: str = "64k",
) -> Path:
    """Compress audio to opus 64 kbps mono 16 kHz for Whisper API submission.

    Args:
        input_path: Source audio file (WAV, MP4, M4A, etc.)
        output_path: Destination .opus file. Defaults to same stem with .opus suffix.
        bitrate: opus bitrate string (default "64k").

    Returns:
        Path to the compressed output file.

    Raises:
        RuntimeError: ffmpeg exited with non-zero code.
        AudioTooLargeError: Compressed output still exceeds 25 MB.
    """
    if output_path is None:
        output_path = input_path.with_suffix(".opus")

    cmd = [
        "ffmpeg",
        "-y",  # overwrite output if exists
        "-i",
        str(input_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        str(output_path),
    ]

    logger.info("Compressing %s -> %s (%s)", input_path.name, output_path.name, bitrate)
    result = subprocess.run(cmd, capture_output=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}) for {input_path.name}: "
            f"{result.stderr.decode(errors='replace')[:200]}"
        )

    size = output_path.stat().st_size
    if size > WHISPER_API_LIMIT_BYTES:
        raise AudioTooLargeError(
            f"Compressed output {output_path.name} is {size / 1e6:.1f} MB, "
            f"still exceeds Whisper API 25 MB limit. Input audio is too long."
        )

    logger.info(
        "Compressed %s -> %s (%.1f MB)",
        input_path.name,
        output_path.name,
        size / 1e6,
    )
    return output_path
