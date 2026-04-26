# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Orphan-WAV recovery for ContextPulse Voice.

When the daemon crashes mid-transcribe, the WAV that was about to be
sent to Whisper remains on disk in the recordings dir (because
voice_module persists audio BEFORE calling transcribe and only deletes
it on a successful TRANSCRIPTION event). This module re-runs Whisper
on those leftovers so the user does not lose dictation work.

Usage from a script:

    from contextpulse_voice.orphan_recovery import recover_all
    summary = recover_all()
    print(summary)

Or via CLI:

    python -m contextpulse_voice.orphan_recovery --delete

Design notes
------------

- `min_age_seconds` (default 120) protects against picking up a WAV
  that is currently being transcribed by a running daemon. A 2-minute
  buffer is conservative — typical local-Whisper dictations finish in
  under 30s on this hardware.

- We never delete on failure. The user can retry with a larger model
  by re-running with --model medium / --model large.

- A `.txt` sidecar is written next to each successfully transcribed
  WAV so the recovery output is durable even if the user runs the
  script unattended (e.g. via a scheduled task).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _Transcriber(Protocol):
    """Structural type — anything with a `transcribe(bytes) -> str`."""

    def transcribe(self, wav_bytes: bytes, **kwargs: Any) -> str: ...


def find_orphan_recordings(
    recordings_dir: Path,
    min_age_seconds: float = 120.0,
) -> list[Path]:
    """Return WAV files in `recordings_dir` older than `min_age_seconds`.

    Recent files are skipped because they may still be in flight on a
    running daemon. Returns an empty list if the directory does not
    exist. Sorted oldest-first so recovery output is deterministic.
    """
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.is_dir():
        return []
    cutoff = time.time() - min_age_seconds
    out: list[Path] = []
    for p in recordings_dir.glob("*.wav"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime <= cutoff:
            out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def transcribe_orphan(
    wav_path: Path,
    transcriber: _Transcriber,
    initial_prompt: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Run the transcriber on a single orphan WAV.

    On success, writes a `.txt` sidecar next to the WAV with the
    transcript and recovery metadata, and returns `(text, metadata)`.

    On any failure (empty transcript, transcriber exception, IO
    error), returns None and leaves the WAV untouched so the user
    can retry.
    """
    wav_path = Path(wav_path)
    try:
        wav_bytes = wav_path.read_bytes()
    except OSError as exc:
        logger.warning("Could not read orphan %s: %s", wav_path, exc)
        return None

    started = time.time()
    try:
        text = transcriber.transcribe(wav_bytes, initial_prompt=initial_prompt)
    except Exception:
        logger.exception("Transcribe failed for orphan %s", wav_path)
        return None

    text = (text or "").strip()
    if not text:
        logger.info("Empty transcript for orphan %s — skipping", wav_path.name)
        return None

    elapsed = time.time() - started
    metadata: dict[str, Any] = {
        "source_wav": wav_path.name,
        "recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "transcribe_seconds": round(elapsed, 2),
        "audio_bytes": len(wav_bytes),
    }
    sidecar = wav_path.with_suffix(".txt")
    try:
        sidecar.write_text(
            f"# Recovered transcription\n"
            f"# source: {metadata['source_wav']}\n"
            f"# recovered_at: {metadata['recovered_at']}\n"
            f"# transcribe_seconds: {metadata['transcribe_seconds']}\n\n"
            f"{text}\n",
            encoding="utf-8",
        )
    except OSError:
        logger.exception("Could not write sidecar for %s", wav_path)
        return None

    return text, metadata


def recover_all(
    recordings_dir: Path,
    transcriber: _Transcriber | None = None,
    min_age_seconds: float = 120.0,
    delete_on_success: bool = False,
    model_size: str = "small",
) -> dict[str, Any]:
    """Recover every orphan WAV in `recordings_dir`.

    Returns a summary dict:
        {
            "scanned": int,         # how many orphans we considered
            "recovered": int,       # successfully transcribed
            "failed": int,          # transcribe raised or returned empty
            "skipped": int,         # in-flight, filtered by min_age
            "results": list[dict],  # per-file detail
        }

    If `transcriber` is None, a `LocalTranscriber(model_size)` is
    constructed lazily — letting CLI callers pick model size at the
    command line without importing whisper code in this module.
    """
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.is_dir():
        return {
            "scanned": 0,
            "recovered": 0,
            "failed": 0,
            "skipped": 0,
            "results": [],
        }

    orphans = find_orphan_recordings(recordings_dir, min_age_seconds)
    all_wavs = list(recordings_dir.glob("*.wav"))
    skipped = max(0, len(all_wavs) - len(orphans))

    if not orphans:
        return {
            "scanned": 0,
            "recovered": 0,
            "failed": 0,
            "skipped": skipped,
            "results": [],
        }

    if transcriber is None:
        from contextpulse_voice.transcriber import LocalTranscriber

        transcriber = LocalTranscriber(model_size=model_size)

    recovered = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for wav in orphans:
        outcome = transcribe_orphan(wav, transcriber)
        if outcome is None:
            failed += 1
            results.append({"wav": wav.name, "ok": False})
            continue
        text, meta = outcome
        recovered += 1
        results.append(
            {
                "wav": wav.name,
                "ok": True,
                "chars": len(text),
                "seconds": meta["transcribe_seconds"],
                "sidecar": wav.with_suffix(".txt").name,
            }
        )
        if delete_on_success:
            try:
                wav.unlink()
            except OSError:
                logger.warning("Could not delete %s after recovery", wav)

    return {
        "scanned": len(orphans),
        "recovered": recovered,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
