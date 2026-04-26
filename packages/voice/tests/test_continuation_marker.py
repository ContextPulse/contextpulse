# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for the truncation continuation marker.

When a recording is truncated (audio exceeded the duration cap, by
either the stuck-release watchdog or just a long user hold), the
transcribed text gets a clear marker appended before paste. This
tells the receiving AI (Claude, etc.) that the message was cut off
and to wait for the next dictation before responding.

Acceptance criteria:
  - Truncated transcriptions get the marker appended.
  - Non-truncated transcriptions do NOT get the marker.
  - Empty transcripts don't get a bare marker pasted.
  - The marker text is clear, ASCII, and unmistakable to an LLM.
"""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _make_wav_bytes(seconds: float = 0.5) -> bytes:
    n_samples = int(seconds * 16000)
    audio = np.zeros((n_samples, 1), dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


@pytest.fixture
def module(tmp_path: Path):
    """VoiceModule with mocked recorder/transcriber, tmp recordings dir."""
    with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
        mock_cfg.return_value = {
            "hotkey": "ctrl+space",
            "fix_hotkey": "ctrl+shift+space",
            "whisper_model": "base",
            "always_use_llm": False,
            "anthropic_api_key": "",
        }
        with patch("contextpulse_voice.voice_module.RECORDINGS_DIR", tmp_path):
            from contextpulse_voice.voice_module import VoiceModule

            m = VoiceModule(model_size="base")
            # Mock recorder with a was_truncated flag we can flip per-test.
            m._recorder = MagicMock()
            m._recorder.was_truncated = False
            m._transcriber = MagicMock()
            m._transcriber.transcribe.return_value = (
                "I was talking about ContextPulse Cloud and how"
            )
            m.register(MagicMock())
            m._running = True
            yield m


class TestContinuationMarker:
    def test_truncated_transcription_gets_marker_appended(self, module):
        from contextpulse_voice.voice_module import _CONTINUATION_MARKER

        module._recorder.was_truncated = True

        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "claude.exe", "Claude")

        # paste_text was called once with text that ends with the marker
        assert mock_paste.called
        pasted = mock_paste.call_args[0][0]
        assert _CONTINUATION_MARKER.strip() in pasted, (
            f"expected marker in pasted text. Got: {pasted!r}"
        )

    def test_non_truncated_transcription_has_no_marker(self, module):
        from contextpulse_voice.voice_module import _CONTINUATION_MARKER

        module._recorder.was_truncated = False

        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "claude.exe", "Claude")

        pasted = mock_paste.call_args[0][0]
        assert "CONTINUATION PENDING" not in pasted, (
            f"normal transcript must not contain marker. Got: {pasted!r}"
        )
        assert _CONTINUATION_MARKER.strip() not in pasted

    def test_empty_transcription_with_truncation_skips_paste(self, module):
        """If Whisper produced nothing, don't paste a bare marker."""
        module._recorder.was_truncated = True
        module._transcriber.transcribe.return_value = ""

        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "claude.exe", "Claude")

        # No paste — empty transcript path returns before paste
        assert not mock_paste.called

    def test_marker_text_is_ascii_and_clear(self):
        """The marker must survive non-UTF8 receiving apps and be
        unambiguous to a downstream LLM."""
        from contextpulse_voice.voice_module import _CONTINUATION_MARKER

        # ASCII-only check — no smart quotes, em dashes, or emoji that
        # could break in cp1252 console contexts.
        try:
            _CONTINUATION_MARKER.encode("ascii")
        except UnicodeEncodeError as exc:
            pytest.fail(f"Marker contains non-ASCII: {exc}")

        # The marker contains an unambiguous instruction
        marker_lower = _CONTINUATION_MARKER.lower()
        assert "continuation" in marker_lower
        assert "wait" in marker_lower or "before responding" in marker_lower

    def test_marker_event_payload_records_truncation(self, module):
        """The TRANSCRIPTION event payload should record was_truncated
        so downstream consumers (journal, MCP queries) can filter."""
        from contextpulse_core.spine import EventType

        module._recorder.was_truncated = True
        events: list = []
        module.register(events.append)

        with patch("contextpulse_voice.voice_module.paste_text") as mock_paste:
            mock_paste.return_value = (time.time(), "abc123")
            with patch("contextpulse_voice.voice_module.has_api_key", return_value=False):
                module._transcribe_and_paste(_make_wav_bytes(), "claude.exe", "Claude")

        transcriptions = [e for e in events if e.event_type == EventType.TRANSCRIPTION]
        assert len(transcriptions) == 1
        assert transcriptions[0].payload.get("was_truncated") is True
