# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Per-source Whisper transcription on a RawSourceCollection.

Operates on each RawSource independently — no channel grouping, no
diarization, no speaker assumptions. The cross-source matcher (A.3)
consumes these per-source transcripts to compute sync offsets.

Backend: local faster-whisper (CPU or GPU). The transcribe_func
argument lets callers swap in a remote backend (GPU spot, Groq, etc.)
without touching orchestration code.
"""

from __future__ import annotations

# CRITICAL: cap C-extension thread pools BEFORE numpy/ctranslate2 import
# (building-transcription-pipelines skill rule G — auto-promoted lesson)
import os

os.environ.setdefault("OMP_NUM_THREADS", "8")
os.environ.setdefault("MKL_NUM_THREADS", "8")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "8")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "8")

import json  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Callable, Protocol  # noqa: E402

from contextpulse_pipeline.compress import (  # noqa: E402
    AudioTooLargeError,
    compress_for_whisper,
)
from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection  # noqa: E402

logger = logging.getLogger(__name__)

# Compression threshold — files above this get compressed to opus before transcription.
# Even on local Whisper (no API limit) this saves significant decode time.
COMPRESS_THRESHOLD_BYTES = 25 * 1024 * 1024  # 25 MB

DEFAULT_MODEL = "large-v3"
DEFAULT_BEAM_SIZE = 1  # 1 = greedy, fast; 5 = default Whisper, slower
DEFAULT_CPU_THREADS = 8


class TranscribeFunc(Protocol):
    """Backend interface: take an audio path, return Whisper verbose dict."""

    def __call__(self, audio_path: Path, *, model: str) -> dict: ...


def _local_faster_whisper(audio_path: Path, *, model: str = DEFAULT_MODEL) -> dict:
    """Default backend: local faster-whisper, CPU int8."""
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    wm = WhisperModel(
        model,
        device="cpu",
        compute_type="int8",
        cpu_threads=DEFAULT_CPU_THREADS,
        num_workers=1,
    )
    segments_iter, info = wm.transcribe(
        str(audio_path),
        beam_size=DEFAULT_BEAM_SIZE,
        vad_filter=True,
        word_timestamps=False,
    )
    segments: list[dict] = []
    full_text_parts: list[str] = []
    for seg in segments_iter:
        segments.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "avg_logprob": float(seg.avg_logprob),
                "compression_ratio": float(seg.compression_ratio),
                "no_speech_prob": float(seg.no_speech_prob),
            }
        )
        full_text_parts.append(seg.text)

    return {
        "language": info.language,
        "duration": float(info.duration),
        "text": "".join(full_text_parts),
        "segments": segments,
    }


def _output_paths(output_dir: Path, sha256: str) -> tuple[Path, Path]:
    """Return (json_path, txt_path) for a given source sha256."""
    sha16 = sha256[:16]
    return output_dir / f"{sha16}.json", output_dir / f"{sha16}.txt"


def transcribe_raw_source(
    rs: RawSource,
    output_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    transcribe_func: Callable[..., dict] | None = None,
    skip_existing: bool = True,
) -> Path:
    """Transcribe one RawSource, write JSON + TXT to output_dir.

    Returns path to the JSON output. Idempotent: skips if output already
    exists (unless skip_existing=False).

    Auto-compresses files larger than COMPRESS_THRESHOLD_BYTES to opus
    before transcription (handles 300+ MB DJI WAVs).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, txt_path = _output_paths(output_dir, rs.sha256)

    if skip_existing and json_path.exists():
        logger.info("Skipping %s — transcript already exists at %s", rs.file_path, json_path)
        return json_path

    src_path = Path(rs.file_path)
    if not src_path.exists():
        raise FileNotFoundError(f"RawSource file not found: {src_path}")

    # Auto-compress large files
    audio_for_whisper = src_path
    compressed_path: Path | None = None
    if src_path.stat().st_size > COMPRESS_THRESHOLD_BYTES:
        compressed_path = output_dir / f"_tmp_{rs.sha256[:16]}.opus"
        logger.info(
            "Compressing %s (%.1f MB) -> opus for transcription",
            src_path.name,
            src_path.stat().st_size / 1e6,
        )
        try:
            audio_for_whisper = compress_for_whisper(src_path, output_path=compressed_path)
        except AudioTooLargeError as e:
            # On local Whisper this shouldn't trigger (no 25 MB API limit), but if
            # compression itself fails the file is unusable — re-raise.
            raise RuntimeError(f"Compression failed for {src_path.name}: {e}") from e

    try:
        backend = transcribe_func or _local_faster_whisper
        logger.info("Transcribing %s (%.1f sec)", src_path.name, rs.duration_sec)
        result = backend(audio_for_whisper, model=model)
    finally:
        if compressed_path is not None and compressed_path.exists():
            try:
                compressed_path.unlink()
            except OSError as e:
                logger.warning("Could not clean up %s: %s", compressed_path, e)

    # Build output document
    doc = {
        "session_id": rs.container,
        "source_sha256": rs.sha256,
        "source_path": str(src_path),
        "source_tier": rs.source_tier,
        "duration_sec": result.get("duration", rs.duration_sec),
        "model": f"whisper-{model}",
        "language": result.get("language", "en"),
        "text": result.get("text", ""),
        "segments": result.get("segments", []),
    }

    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(doc["text"], encoding="utf-8")
    logger.info("Wrote %s (%d segments)", json_path.name, len(doc["segments"]))

    return json_path


def transcribe_collection(
    coll: RawSourceCollection,
    output_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    transcribe_func: Callable[..., dict] | None = None,
    skip_existing: bool = True,
) -> list[Path]:
    """Transcribe every RawSource in a collection. Returns list of JSON paths.

    Errors on individual sources are logged and re-raised (no silent skip).
    """
    results: list[Path] = []
    for rs in coll.sources:
        try:
            json_path = transcribe_raw_source(
                rs,
                output_dir,
                model=model,
                transcribe_func=transcribe_func,
                skip_existing=skip_existing,
            )
            results.append(json_path)
        except Exception as e:
            logger.error("Failed to transcribe %s: %s", rs.file_path, e)
            raise
    return results
