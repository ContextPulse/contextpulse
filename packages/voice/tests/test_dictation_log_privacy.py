"""Dictated words must not reach contextpulse.log in plaintext.

The daemon log is a 10 MB rotated text file outside the redaction applied to
activity.db. Until 2026-10-19 the transcriber wrote the first 60 characters of
every segment and the first 80 of every clip at INFO, and the paster logged a
16-hex sha256 prefix of the RAW text -- a preimage oracle for a dictated SSN or
card number (see contextpulse_core.redact.redacted_text_digest). A secret said
aloud therefore landed on disk twice. These tests pin the fix:

- INFO lines about a transcription carry lengths and timings, never words.
- The degenerate-segment WARNING keeps its diagnostic preview, redacted.
- Every hash the paster exposes (logs, return value, drop notifier) is the
  redacted digest; the raw-text key used for duplicate detection stays private,
  so duplicate detection behaves exactly as before.
"""

import hashlib
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import contextpulse_voice.paster as paster_module
import pytest
from contextpulse_core.redact import redact_sensitive, redacted_text_digest
from contextpulse_voice.transcriber import LocalTranscriber

SECRET = "sk-ant-zqdictneedle0123456789abcdefghij"
SSN = "123-45-6789"
PLAIN_WORD = "porcupinequill"  # an ordinary dictated word, no pattern to redact


def _segment(text: str, compression_ratio: float = 1.1, start: float = 0.0, end: float = 5.0):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.avg_logprob = -0.2
    seg.no_speech_prob = 0.01
    seg.compression_ratio = compression_ratio
    return seg


def _transcribe(segments, caplog, level=logging.DEBUG):
    with (
        patch("contextpulse_voice.transcriber.sys") as mock_sys,
        patch("contextpulse_voice.model_manager.get_model_path", return_value="fake"),
        patch("faster_whisper.WhisperModel") as MockModel,
    ):
        mock_sys.platform = "linux"
        model = MagicMock()
        model.transcribe.return_value = (segments, MagicMock(duration=6.0, language="en"))
        MockModel.return_value = model
        t = LocalTranscriber(model_size="small")
        with caplog.at_level(level, logger="contextpulse_voice.transcriber"):
            return t.transcribe(b"fake_wav_bytes")


def _messages(caplog, levelno=None) -> str:
    return " ".join(
        r.getMessage() for r in caplog.records if levelno is None or r.levelno == levelno
    )


def test_redactor_covers_the_needles():
    """Precondition: without this the absence checks below prove nothing."""
    assert SECRET not in redact_sensitive(f"my key is {SECRET}")
    assert SSN not in redact_sensitive(f"my social is {SSN}")


class TestTranscriberLog:
    def test_ordinary_dictation_words_never_reach_the_log(self, caplog):
        text = f"remind me to buy a {PLAIN_WORD} tomorrow"
        result = _transcribe([_segment(text)], caplog)

        assert result == text, "the caller still gets the full transcription"
        logged = _messages(caplog)
        assert f"{len(text)} chars" in logged, "the transcriber must still log the clip"
        assert PLAIN_WORD not in logged
        assert "remind me" not in logged

    def test_secret_dictated_in_a_normal_segment_never_reaches_the_log(self, caplog):
        _transcribe([_segment(f"my key is {SECRET}"), _segment(f"my social is {SSN}")], caplog)

        logged = _messages(caplog)
        assert SECRET not in logged
        assert "zqdictneedle" not in logged
        assert SSN not in logged

    def test_segment_and_clip_lines_still_carry_diagnostics(self, caplog):
        text = f"remind me to buy a {PLAIN_WORD}"
        _transcribe([_segment(text, compression_ratio=1.3)], caplog, level=logging.INFO)

        info = _messages(caplog, logging.INFO)
        assert "cr=1.3" in info, "per-segment scores are the reason the line exists"
        assert f"{len(text)} chars" in info, "length replaces the words"

    def test_degenerate_warning_keeps_a_redacted_preview(self, caplog):
        looping = f"{SECRET}, {SECRET}, {SECRET}, Shading, Shading, Shading"
        result = _transcribe([_segment(looping, compression_ratio=18.6)], caplog)

        assert result == ""
        warnings = _messages(caplog, logging.WARNING)
        assert "18.6" in warnings
        assert "Shading" in warnings, "the repetition preview is the diagnostic"
        assert SECRET not in warnings
        assert "zqdictneedle" not in warnings


class TestPasterHash:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        import pyautogui
        import pyperclip

        monkeypatch.setattr(pyperclip, "copy", MagicMock())
        monkeypatch.setattr(pyperclip, "paste", MagicMock(return_value=""))
        monkeypatch.setattr(pyautogui, "hotkey", MagicMock())
        monkeypatch.setattr(paster_module, "_focused_is_terminal", lambda: False)
        monkeypatch.setattr(paster_module, "_last_paste_time", 0.0)
        monkeypatch.setattr(paster_module, "_last_paste_hash", "")
        monkeypatch.setattr(paster_module, "_paste_lock", threading.Lock())
        if hasattr(paster_module, "_last_paste_key"):
            monkeypatch.setattr(paster_module, "_last_paste_key", "")

    def test_exposed_hash_is_the_redacted_digest_not_the_raw_sha(self, caplog):
        text = f"my social is {SSN}"
        raw_prefix = hashlib.sha256(text.encode()).hexdigest()[:16]

        with caplog.at_level(logging.DEBUG, logger="contextpulse_voice.paster"):
            ts, h = paster_module.paste_text(text)

        assert ts > 0
        assert h == redacted_text_digest(text)
        assert h != raw_prefix
        logged = _messages(caplog)
        assert raw_prefix not in logged
        assert SSN not in logged
        assert paster_module.get_last_paste_info()[1] == h

    def test_dropped_paste_notifies_with_the_redacted_digest(self, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(paster_module, "_drop_notifier", seen.append)
        busy = MagicMock()
        busy.acquire.return_value = False
        monkeypatch.setattr(paster_module, "clipboard_lock", busy)
        text = f"my social is {SSN}"

        ts, h = paster_module.paste_text(text)

        assert (ts, h) == (0.0, "")
        assert seen == [redacted_text_digest(text)]

    def test_two_different_secrets_are_not_mistaken_for_a_duplicate(self):
        """Both redact to the same text, so a redacted dedup key would drop the
        second paste. Duplicate detection must still see the raw text."""
        first = "my social is 123-45-6789"
        second = "my social is 987-65-4321"
        assert redacted_text_digest(first) == redacted_text_digest(second)

        assert paster_module.paste_text(first)[0] > 0
        paster_module._last_paste_time = time.time() - 5.0  # inside the 10 s dedup window
        assert paster_module.paste_text(second)[0] > 0, "second, different paste was dropped"

    def test_identical_text_inside_the_window_is_still_a_duplicate(self):
        text = "same words twice"
        assert paster_module.paste_text(text)[0] > 0
        paster_module._last_paste_time = time.time() - 5.0
        assert paster_module.paste_text(text) == (0.0, "")
