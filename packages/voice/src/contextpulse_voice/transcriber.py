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


class LocalTranscriber(Transcriber):
    """Transcribes audio using a local Whisper model (no API cost).

    Backend is chosen automatically:
      - macOS Apple Silicon → mlx-whisper (Metal acceleration)
      - Everything else → faster-whisper / ctranslate2

    First call downloads the model (~1.5GB for medium). Subsequent calls are instant.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
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
            from faster_whisper import WhisperModel

            from contextpulse_voice.model_manager import get_model_path

            model_path = get_model_path(model_size)
            logger.info("Loading Whisper '%s' model (path: %s)...", model_size, model_path)
            self.model = WhisperModel(model_path, device=device, compute_type="int8")
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
                return result.get("text", "").strip()
            finally:
                os.unlink(tmp_path)
        else:
            # ctranslate2 / faster-whisper path
            audio_file = io.BytesIO(wav_bytes)
            segments, info = self.model.transcribe(
                audio_file,
                beam_size=beam_size,
                condition_on_previous_text=False,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
                initial_prompt=initial_prompt or None,
                # Relax quality filters to prevent premature cutoff on
                # longer recordings.  Defaults silently drop segments
                # whose log-prob, no-speech prob, or compression ratio
                # exceed thresholds — causing mid-sentence truncation.
                log_prob_threshold=-1.5,
                no_speech_threshold=0.8,
                compression_ratio_threshold=3.0,
            )
            # Collect segments, skip duplicates
            parts = []
            for seg in segments:
                t = seg.text.strip()
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
