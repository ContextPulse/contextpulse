# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Transcription module — converts audio to text via local Whisper or cloud APIs.

Whisper transcription backend (local and cloud).
Supports two local backends:
  - ctranslate2 (faster-whisper) — Windows/Linux, CUDA or CPU
  - mlx-whisper — macOS Apple Silicon (Metal acceleration)
"""

import io
import logging
import sys
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class Transcriber(ABC):
    """Base transcriber interface."""

    @abstractmethod
    def transcribe(self, wav_bytes: bytes, **kwargs) -> str:
        """Transcribe WAV audio bytes to text."""
        ...



# Quality filters are DISABLED for dictation — they silently drop segments
# and cause sentence truncation.  Only no_speech_threshold is kept at 0.95
# to filter out pure silence.  log_prob_threshold is retained for logging
# only (see below for why it is not a usable signal).  compression_ratio_
# threshold IS enforced, but post-transcription and per-segment rather than
# inside Whisper (see _segment_is_degenerate and its call sites).
#
# Each profile is: (log_prob_threshold, no_speech_threshold, compression_ratio_threshold)
_MODEL_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "tiny":      (-1.5, 0.8, 3.0),
    "base":      (-2.0, 0.85, 3.5),
    "small":     (-3.0, 0.95, 5.0),
    "medium":    (-3.0, 0.95, 5.0),
    "large-v3":  (-3.5, 0.98, 6.0),
}
_DEFAULT_THRESHOLDS = (-3.0, 0.95, 5.0)


def _segment_is_degenerate(compression_ratio: float, compression_ratio_threshold: float) -> bool:
    """True if a single segment's compression_ratio marks it as a
    repetition-runaway (Whisper hallucinating a repeated token/phrase)
    rather than real speech.

    Filtering is per-segment, not whole-transcript: real dictation can
    contain one degenerate segment alongside entirely legitimate ones in
    the same clip (evidence below), so an all-or-nothing reject would
    discard real speech -- exactly the truncation failure the disabled
    Whisper-internal filters exist to avoid.

    log_prob is NOT used as a signal here: a 2026-08-21 incident segment
    logged avg_logprob=-0.06 (the HIGHEST confidence of the whole session)
    while genuinely repeating "Shading, Shading, Shading, ...".  Whisper is
    highly confident in its own repetition loops. compression_ratio is the
    only discriminator with a clean separation in observed data:
      - Degenerate segments: cr=18.6, cr=19.5 ("GIF, GIF, GIF...",
        "Shading, Shading, Shading...")
      - Legitimate dictation, full sessions: cr in ~0.6-1.5
    The existing per-model profile threshold (5.0 for 'small') sits
    comfortably in that gap, so no separate constant is needed.
    """
    return compression_ratio > compression_ratio_threshold


class LocalTranscriber(Transcriber):
    """Transcribes audio using a local Whisper model (no API cost).

    Backend is chosen automatically:
      - macOS Apple Silicon → mlx-whisper (Metal acceleration)
      - Everything else → faster-whisper / ctranslate2

    First call downloads the model (~1.5GB for medium). Subsequent calls are instant.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
        self._model_size = model_size
        self._thresholds = _MODEL_THRESHOLDS.get(model_size, _DEFAULT_THRESHOLDS)
        _, no_speech_threshold, compression_ratio_threshold = self._thresholds
        logger.info(
            "Whisper '%s' profile: no_speech_threshold=%.2f enforced by Whisper; "
            "log_prob filtering disabled (would truncate real speech); "
            "compression_ratio_threshold=%.1f enforced per-segment AFTER "
            "transcription (drops individual degenerate segments, not the "
            "whole transcript) rather than inside Whisper",
            model_size, no_speech_threshold, compression_ratio_threshold,
        )
        import platform

        if sys.platform == "darwin" and platform.machine() == "arm64":
            self._backend = "mlx"
            import mlx_whisper
            self._mlx_whisper = mlx_whisper
            # Map model sizes to HuggingFace repos
            model_map = {
                "base": "mlx-community/whisper-base",
                "small": "mlx-community/whisper-small",
                "medium": "mlx-community/whisper-medium",
                "large-v3": "mlx-community/whisper-large-v3-turbo",
            }
            self._mlx_model = model_map.get(model_size, f"mlx-community/whisper-{model_size}")
            logger.info("Using mlx-whisper with model %s", self._mlx_model)
        else:
            self._backend = "ctranslate2"
            from contextpulse_core._thread_caps import get_cap
            from faster_whisper import WhisperModel

            from contextpulse_voice.model_manager import get_model_path

            model_path = get_model_path(model_size)
            cpu_threads = get_cap()
            logger.info(
                "Loading Whisper '%s' model (path: %s, cpu_threads=%d)...",
                model_size, model_path, cpu_threads,
            )
            # cpu_threads caps the OpenMP intra-op pool; num_workers=1 keeps
            # the inter-op (batch parallelism) pool at a single worker since
            # ContextPulse only ever transcribes one clip at a time. Without
            # these, ctranslate2 allocates ~cpu_count() workers per pool which
            # was the dominant contributor to a 163-thread daemon baseline
            # (incident: 2026-04-29).
            self.model = WhisperModel(
                model_path,
                device=device,
                compute_type="int8",
                cpu_threads=cpu_threads,
                num_workers=1,
            )
            logger.info("Whisper model loaded")

    def transcribe(
        self, wav_bytes: bytes, beam_size: int = 1, initial_prompt: str = "",
    ) -> str:
        if not wav_bytes:
            return ""

        if self._backend == "mlx":
            # mlx-whisper needs a file path, write temp file
            import os
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_bytes)
                tmp_path = f.name
            try:
                result = self._mlx_whisper.transcribe(
                    tmp_path,
                    path_or_hf_repo=self._mlx_model,
                    language="en",
                    initial_prompt=initial_prompt or None,
                )
                raw_segments = result.get("segments") or []
                if not raw_segments:
                    # No per-segment data to filter with -- fall back to the
                    # whole-clip text rather than rejecting on nothing.
                    return result.get("text", "").strip()

                compression_ratio_threshold = self._thresholds[2]
                parts = []
                for seg in raw_segments:
                    t = str(seg.get("text", "")).strip()
                    cr = float(seg.get("compression_ratio", 0.0))
                    if _segment_is_degenerate(cr, compression_ratio_threshold):
                        logger.warning(
                            "Dropped degenerate segment (mlx) compression_ratio=%.1f "
                            "exceeds '%s' profile threshold %.1f -- discarded: %r",
                            cr, self._model_size, compression_ratio_threshold, t[:120],
                        )
                        continue
                    if t and (not parts or t != parts[-1]):
                        parts.append(t)
                text = " ".join(parts)
                return " ".join(text.split())
            finally:
                os.unlink(tmp_path)
        else:
            # ctranslate2 / faster-whisper path
            audio_file = io.BytesIO(wav_bytes)
            segments, info = self.model.transcribe(
                audio_file,
                beam_size=beam_size,
                condition_on_previous_text=True,
                initial_prompt=initial_prompt or None,
                # Disable all quality filters — they silently drop segments
                # and cause mid-sentence/end-of-sentence truncation.
                # For dictation, we NEVER want to discard user speech.
                log_prob_threshold=None,
                no_speech_threshold=0.95,
                compression_ratio_threshold=None,
            )
            # Collect segments, skip duplicates, and drop individual
            # degenerate (repetition-runaway) segments while keeping
            # legitimate ones from the SAME clip -- see
            # _segment_is_degenerate for why this is per-segment rather
            # than an all-or-nothing reject of the whole transcript.
            # Log per-segment scores at INFO level for production
            # diagnostics regardless of the filtering decision.
            compression_ratio_threshold = self._thresholds[2]
            parts = []
            dropped_segments = 0
            for seg in segments:
                t = seg.text.strip()
                logger.info(
                    "Segment [%.1f-%.1fs] logprob=%.2f no_speech=%.2f cr=%.1f %r",
                    seg.start, seg.end, seg.avg_logprob,
                    seg.no_speech_prob, seg.compression_ratio, t[:60],
                )
                if _segment_is_degenerate(seg.compression_ratio, compression_ratio_threshold):
                    dropped_segments += 1
                    logger.warning(
                        "Dropped degenerate segment [%.1f-%.1fs] compression_ratio=%.1f "
                        "exceeds '%s' profile threshold %.1f -- discarded: %r",
                        seg.start, seg.end, seg.compression_ratio,
                        self._model_size, compression_ratio_threshold, t[:120],
                    )
                    continue
                if t and (not parts or t != parts[-1]):
                    parts.append(t)
            text = " ".join(parts)
            text = " ".join(text.split())
            logger.info(
                "Local transcription (%.1fs audio, lang=%s): %s",
                info.duration,
                info.language,
                text[:80],
            )
            if dropped_segments:
                logger.warning(
                    "Transcription dropped %d degenerate segment(s); "
                    "%d chars of legitimate speech survived",
                    dropped_segments, len(text),
                )

            return text


class WhisperAPITranscriber(Transcriber):
    """Transcribes audio using OpenAI Whisper API (~$0.006/min)."""

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def transcribe(self, wav_bytes: bytes, **kwargs) -> str:
        if not wav_bytes:
            return ""
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "recording.wav"
        logger.info("Sending %d bytes to Whisper API...", len(wav_bytes))
        response = self.client.audio.transcriptions.create(
            model=self.model,
            file=audio_file,
            response_format="text",
        )
        text = response.strip()
        logger.info("Transcription: %s", text[:80])
        return text
