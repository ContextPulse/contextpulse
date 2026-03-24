"""Transcription module — converts audio to text via local Whisper or cloud APIs.

Ported from Voiceasy with import paths updated.
"""

import io
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class Transcriber(ABC):
    """Base transcriber interface."""

    @abstractmethod
    def transcribe(self, wav_bytes: bytes, **kwargs) -> str:
        """Transcribe WAV audio bytes to text."""
        ...


class LocalTranscriber(Transcriber):
    """Transcribes audio using faster-whisper (local, no API cost).

    First call downloads the model (~1.5GB for medium). Subsequent calls are instant.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
        from faster_whisper import WhisperModel

        from contextpulse_voice.model_manager import get_model_path

        model_path = get_model_path(model_size)
        logger.info("Loading Whisper '%s' model (path: %s)...", model_size, model_path)
        self.model = WhisperModel(model_path, device=device, compute_type="int8")
        logger.info("Whisper model loaded")

    def transcribe(self, wav_bytes: bytes, beam_size: int = 1) -> str:
        if not wav_bytes:
            return ""
        audio_file = io.BytesIO(wav_bytes)
        segments, info = self.model.transcribe(
            audio_file,
            beam_size=beam_size,
            condition_on_previous_text=False,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
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
