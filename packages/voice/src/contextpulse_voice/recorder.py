# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Audio recording module — captures mic input while hotkey is held.

Audio recording via sounddevice.
"""

import io
import logging
import threading
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
        # Guards _stream/_frames mutation across threads: the pynput listener
        # thread (start()), the background stop/transcribe thread (stop(),
        # stop_after_silence()), and PortAudio's own native callback thread
        # (_callback) can all touch this state concurrently.
        self._lock = threading.Lock()
        # Bumped every time a stream is opened. _callback captures the token
        # of ITS stream via closure and only appends when that token still
        # matches self._stream_token. Without this, a stream orphaned by a
        # re-entrant start() (see start()'s docstring) keeps its native
        # callback thread alive, and that thread would otherwise keep
        # appending into self._frames after start() has reassigned it to a
        # new recording -- a data race in native memory that surfaced as an
        # access violation (0xc0000005) in python314.dll.
        self._stream_token = 0

    def _close_active_stream(self) -> None:
        """Stop + close self._stream if one is open. Never raises.

        Atomically swaps self._stream to None under the lock first, so if
        this races with another thread doing the same thing (e.g. a
        re-entrant start() racing the background stop/transcribe thread's
        teardown), only one of them actually calls stop()/close() on the
        real stream -- the other sees None and returns immediately. This is
        what prevents a double-close and what guarantees "every opened
        stream is closed exactly once."
        """
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            logger.warning("Error closing prior audio stream (continuing)", exc_info=True)

    def start(self) -> None:
        """Start recording audio.

        Defensively closes any stream left open by a prior start() before
        opening a new one. This is the fix for the overlapping-PortAudio-
        stream crash: a re-entrant start() (the hotkey re-pressed while a
        prior recording's background stop/transcribe thread is still
        tearing down) used to silently overwrite self._stream without
        closing the old one. The orphaned stream's native callback thread
        stayed alive and kept appending into self._frames concurrently with
        this method reassigning it -> a data race that crashed the process.
        """
        if self._stream is not None:
            logger.warning(
                "Recorder.start() called with a stream already open -- "
                "closing it before opening a new one"
            )
        self._close_active_stream()

        with self._lock:
            self._frames = []
            self._stream_token += 1
            token = self._stream_token

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=DTYPE,
            callback=lambda *args: self._callback(*args, token=token),
        )
        stream.start()
        with self._lock:
            self._stream = stream
        logger.info("Recording started")

    def warm_start(self) -> None:
        """Open + close a brief audio stream to prime PortAudio.

        First-ever call to sd.InputStream() on Windows can block for
        100-500ms while PortAudio enumerates devices and opens the WASAPI
        endpoint.  When this happens on the keyboard hook thread inside
        start(), the recording overlay does not appear until the user
        releases the hotkey (Bug: first-press hotkey delay).  Calling this
        once at daemon init pays the cost up-front so the first real
        start() returns immediately.
        """
        try:
            # token=-1 never matches a real recording's token (start()
            # begins numbering at 1), so even if the warm-up callback fired
            # late (after stop()/close() below returned) it would be a
            # dropped no-op rather than polluting a real recording's buffer.
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DTYPE,
                callback=lambda *args: self._callback(*args, token=-1),
            )
            stream.start()
            stream.stop()
            stream.close()
            with self._lock:
                self._frames = []  # discard anything captured during warm-up
            logger.info("Audio device warmed up")
        except Exception:
            logger.debug("Recorder warm_start failed (non-fatal)", exc_info=True)

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes.

        Always clears the internal frame buffer, even if WAV conversion fails,
        to prevent memory accumulation across dictation cycles.
        """
        try:
            self._close_active_stream()
            logger.info("Recording stopped — %d frames captured", len(self._frames))
            return self._to_wav()
        finally:
            with self._lock:
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

                # Check energy of most recent frames. Snapshot under the
                # lock so a concurrent start() reassigning self._frames
                # can't be observed mid-read (worst case without this was a
                # benign but avoidable race on the list reference).
                with self._lock:
                    recent = self._frames[-1] if self._frames else None

                if recent is not None:
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

            self._close_active_stream()
            logger.info("Recording stopped — %d frames captured", len(self._frames))
            return self._to_wav()
        finally:
            with self._lock:
                self._frames = []

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
        token: int = 0,
    ) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        with self._lock:
            if token != self._stream_token:
                # This callback belongs to a stream that has since been
                # superseded (orphaned) by a newer start() -- e.g. a
                # re-entrant start() raced this callback's own teardown.
                # Drop the frame instead of writing into a buffer that now
                # belongs to a different recording.
                return
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
