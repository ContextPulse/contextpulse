# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Audio recording module — captures mic input while hotkey is held.

Audio recording via sounddevice.
"""

import io
import logging
import time
import wave
from typing import Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1
DTYPE = "int16"

# Energy-based tail extension: after key release, keep recording until
# the mic goes quiet or MAX_TAIL_S elapses.  This prevents cutting off
# trailing words that the user finishes after releasing the hotkey.
_SILENCE_THRESHOLD_RMS = 200      # RMS below this = silence (int16 range)
_SILENCE_DURATION_S = 0.5         # need 500ms of consecutive silence to stop
_MAX_TAIL_S = 2.0                 # hard cap on tail extension


class Recorder:
    """Records audio from the default microphone."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        """Start recording audio."""
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DTYPE,
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Recording started")

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes.

        Always clears the internal frame buffer, even if WAV conversion fails,
        to prevent memory accumulation across dictation cycles.
        """
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            logger.info("Recording stopped — %d frames captured", len(self._frames))
            return self._to_wav()
        finally:
            self._frames = []

    def stop_after_silence(self) -> bytes:
        """Keep recording until silence is detected, then stop.

        Waits for _SILENCE_DURATION_S of consecutive silence (RMS below
        threshold) or _MAX_TAIL_S total, whichever comes first.
        Returns WAV bytes.
        """
        try:
            if self._stream is None:
                logger.warning("stop_after_silence called but no stream active")
                return self._to_wav()

            silence_start: Optional[float] = None
            tail_start = time.monotonic()

            while True:
                elapsed = time.monotonic() - tail_start
                if elapsed >= _MAX_TAIL_S:
                    logger.info(
                        "Tail extension hit max (%.1fs) — stopping", _MAX_TAIL_S
                    )
                    break

                # Check energy of most recent frames
                if self._frames:
                    recent = self._frames[-1]
                    rms = np.sqrt(np.mean(recent.astype(np.float64) ** 2))

                    if rms < _SILENCE_THRESHOLD_RMS:
                        if silence_start is None:
                            silence_start = time.monotonic()
                        elif time.monotonic() - silence_start >= _SILENCE_DURATION_S:
                            logger.info(
                                "Silence detected after %.1fs tail — stopping",
                                elapsed,
                            )
                            break
                    else:
                        silence_start = None  # reset — still speaking

                time.sleep(0.05)  # check every 50ms

            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            logger.info("Recording stopped — %d frames captured", len(self._frames))
            return self._to_wav()
        finally:
            self._frames = []

    def _callback(
        self, indata: np.ndarray, frames: int, time_info: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        self._frames.append(indata.copy())

    def _to_wav(self) -> bytes:
        """Convert captured frames to WAV bytes."""
        if not self._frames:
            logger.warning("No audio frames captured")
            return b""
        audio = np.concatenate(self._frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
