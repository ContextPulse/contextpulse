# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Dictated text must never reach storage, the FTS index, or an MCP client.

Sibling of packages/screen/tests/test_clipboard_redaction.py, for voice.
get_recent_transcriptions and learn_from_session returned spoken content
verbatim and no redaction path existed for voice at all
(cp-voice-touch-memory-text-unredacted).

Each case asserts the secret appears in NONE of:
  1. the `events` table (payload JSON: transcript AND raw_transcript)
  2. the `events_fts` full-text index (transcript IS indexed by the live
     trigger, so this probe is load-bearing here, unlike in touch)
  3. get_recent_transcriptions / learn_from_session / get_voice_stats /
     get_vocabulary / check_corrections
  4. the raw bytes of the database file on disk

and that the text handed to paste_text() is the UNREDACTED original -- the
user's cursor must still receive what they said. Redacting the paste would be
a different product, and a worse one.

Every value below is SYNTHETIC. Nothing here was ever dictated.
"""

import json
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest
from contextpulse_core.redact import redact_sensitive
from contextpulse_core.spine import EventBus
from contextpulse_voice import mcp_server

CONTROL_WORD = "zqcontrol"

# (family, what Whisper heard, the substring that must disappear)
#
# Families are chosen to survive clean_basic(), which uppercases the first
# character and appends a period. A lowercase-prefixed token (ghp_...) is
# turned into "Ghp_..." by that path and stops matching its case-sensitive
# pattern -- reported separately, not papered over here by patching cleanup
# out of the seam.
SPOKEN_SECRETS = [
    ("ssn", f"{CONTROL_WORD} my social is 987-65-4321", "987-65-4321"),
    ("credit_card", f"{CONTROL_WORD} card 4111 1111 1111 1111", "4111 1111 1111 1111"),
    ("password_phrase", f"{CONTROL_WORD} password: zqspokenneedle42", "zqspokenneedle42"),
    ("api_key_phrase", f"{CONTROL_WORD} api_key=zqspokenapineedle77", "zqspokenapineedle77"),
    ("aws_access_key", f"{CONTROL_WORD} AKIAZQVOICENEEDLE012", "AKIAZQVOICENEEDLE012"),
]

FAMILY_IDS = [c[0] for c in SPOKEN_SECRETS]


def _read_db_bytes(db_path):
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            blob += sidecar.read_bytes()
    return blob


def _dictate_once(tmp_path, heard_text):
    """Drive one dictation through the real VoiceModule and the real EventBus.

    Only the hardware ends are mocked: the transcriber (a Whisper model) and
    paste_text (a synthetic Ctrl+V). Cleanup, vocabulary application, payload
    construction, event validation, the INSERT and the FTS trigger are all the
    production path.

    Returns (db_path, text_actually_pasted).
    """
    db_path = tmp_path / "activity.db"
    bus = EventBus(db_path)

    with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
        mock_cfg.return_value = {
            "hotkey": "ctrl+space",
            "fix_hotkey": "ctrl+shift+space",
            "whisper_model": "base",
            "always_use_llm": False,
            "anthropic_api_key": "",
        }
        from contextpulse_voice.voice_module import VoiceModule

        module = VoiceModule(model_size="base")
        module._transcriber = MagicMock()
        module._transcriber.transcribe.return_value = heard_text
        module.register(bus.emit)
        module._running = True

        with (
            patch("contextpulse_voice.voice_module.paste_text") as mock_paste,
            patch("contextpulse_voice.voice_module.has_api_key", return_value=False),
            patch("contextpulse_voice.voice_module.threading.Thread"),
        ):
            mock_paste.return_value = (time.time(), "abc123")
            module._transcribe_and_paste(b"fake_wav", f"{CONTROL_WORD}.exe", "editor")
            pasted = mock_paste.call_args[0][0] if mock_paste.call_args else None

    bus.close()
    return db_path, pasted


def _call_voice_tools(db_path, tmp_path):
    original = mcp_server._DB_PATH
    mcp_server._DB_PATH = db_path
    try:
        return {
            "get_recent_transcriptions": mcp_server.get_recent_transcriptions(
                minutes=60, limit=50,
            ),
            "get_voice_stats": mcp_server.get_voice_stats(hours=1.0),
            "learn_from_session": mcp_server.learn_from_session(hours=1, dry_run=True),
            "check_corrections": mcp_server.check_corrections(hours=1, dry_run=True),
        }
    finally:
        mcp_server._DB_PATH = original


def _fts_hits(db_path, term):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT count(*) FROM events_fts WHERE events_fts MATCH ?", (term,)
        ).fetchone()[0]
    finally:
        conn.close()


class TestSpokenSecretsAreRedactableAtAll:
    """Guard on the fixtures: a value no pattern matches was never a secret."""

    @pytest.mark.parametrize("family,heard,needle", SPOKEN_SECRETS, ids=FAMILY_IDS)
    def test_pattern_removes_it(self, family, heard, needle):
        cleaned = redact_sensitive(heard)
        assert needle not in cleaned, f"{family}: fixture is not redactable"
        assert CONTROL_WORD in cleaned, f"{family}: control word was itself redacted"


class TestSpokenSecretsNeverReachStorageOrMCP:
    @pytest.mark.parametrize("family,heard,needle", SPOKEN_SECRETS, ids=FAMILY_IDS)
    def test_secret_absent_everywhere(self, tmp_path, family, heard, needle):
        db_path, pasted = _dictate_once(tmp_path, heard)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT payload FROM events WHERE event_type = 'transcription'"
        ).fetchall()
        conn.close()

        # The dictation must have been stored, or every "not in" is vacuous.
        assert len(rows) == 1, f"{family}: expected exactly one transcription event"
        payload = json.loads(rows[0][0])
        assert "[REDACTED" in payload["transcript"], (
            f"{family}: nothing was redacted in the stored transcript -- "
            f"got {payload['transcript']!r}"
        )
        for key in ("transcript", "raw_transcript"):
            assert needle not in payload[key], f"{family}: leaked via payload.{key}"
        assert needle not in rows[0][0], f"{family}: leaked into events.payload"

        # Positive control: transcript IS FTS-indexed, so the control word
        # proves the probe below can find things.
        assert _fts_hits(db_path, CONTROL_WORD) == 1, (
            f"{family}: control not indexed; the MATCH probe proves nothing"
        )
        probe = needle.lower().replace("-", "").replace(" ", "")
        assert _fts_hits(db_path, probe) == 0, f"{family}: discoverable through events_fts"

        responses = _call_voice_tools(db_path, tmp_path)
        assert CONTROL_WORD in str(responses["get_recent_transcriptions"]), (
            f"{family}: get_recent_transcriptions returned nothing to redact"
        )
        for tool, response in responses.items():
            assert needle not in str(response), f"{family}: leaked through MCP tool {tool}"

        assert needle.encode() not in _read_db_bytes(db_path), (
            f"{family}: secret found in the raw database file"
        )

    @pytest.mark.parametrize("family,heard,needle", SPOKEN_SECRETS, ids=FAMILY_IDS)
    def test_the_pasted_text_is_not_redacted(self, tmp_path, family, heard, needle):
        """Redaction is a STORAGE control, not a dictation filter.

        If this ever goes red the feature is broken in the user's face: they
        said something and their cursor received "[REDACTED:...]".
        """
        _db_path, pasted = _dictate_once(tmp_path, heard)
        assert pasted is not None, f"{family}: paste_text was never called"
        assert "[REDACTED" not in pasted, f"{family}: the user's own dictation was redacted"


class TestPreFixRowsAreRedactedAtTheMCPBoundary:
    """Every transcription stored before today is still raw on disk."""

    @pytest.mark.parametrize("family,heard,needle", SPOKEN_SECRETS, ids=FAMILY_IDS)
    def test_tools_scrub_raw_rows(self, tmp_path, family, heard, needle):
        db_path = tmp_path / "activity.db"
        bus = EventBus(db_path)
        bus.close()

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
            " window_title, monitor_index, payload, correlation_id, attention_score,"
            " cognitive_load) VALUES (?, ?, 'voice', 'transcription', ?, 'editor', 0, ?,"
            " NULL, 0.0, 0.0)",
            (
                "raw-zq-voice-1",
                time.time(),
                f"{CONTROL_WORD}.exe",
                json.dumps({
                    "transcript": heard,
                    "raw_transcript": f"{heard} unclean",
                    "confidence": 0.9,
                    "language": "en",
                    "duration_seconds": 2.0,
                    "cleanup_applied": False,
                }),
            ),
        )
        conn.commit()
        conn.close()

        responses = _call_voice_tools(db_path, tmp_path)
        assert CONTROL_WORD in str(responses["get_recent_transcriptions"]), (
            f"{family}: nothing returned to redact -- assertion is vacuous"
        )
        for tool, response in responses.items():
            assert needle not in str(response), (
                f"{family}: raw pre-fix row leaked through MCP tool {tool}"
            )


class TestVocabularyCannotServeASecret:
    """get_vocabulary reads a JSON file, and that file predates every fix here.

    The learned-vocabulary file is the one stored word LIST in this codebase
    that can hold a whole token: entries are word pairs, and a pasted-then-
    retyped secret arrives as a key. Writers now refuse such a pair; this pins
    the read side for files already on disk.
    """

    @pytest.mark.parametrize("family,heard,needle", SPOKEN_SECRETS, ids=FAMILY_IDS)
    def test_a_secret_already_in_the_file_is_masked_on_read(self, family, heard, needle):
        entries = {f"{CONTROL_WORD} {heard}": f"corrected {heard}"}
        with (
            patch("contextpulse_voice.vocabulary.get_all_entries", return_value=entries),
            patch("contextpulse_voice.vocabulary.get_learned_entries", return_value=entries),
        ):
            out = mcp_server.get_vocabulary()
        assert CONTROL_WORD in out, f"{family}: returned nothing to redact"
        assert needle not in out, f"{family}: vocabulary file leaked a secret"


class TestLogsDoNotCarryDictatedText:
    """Four log lines wrote the user's own speech into a rotating file."""

    def test_dictation_log_has_no_content(self, tmp_path, caplog):
        heard = f"{CONTROL_WORD} password: zqlogneedle42"
        with caplog.at_level("INFO"):
            _dictate_once(tmp_path, heard)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "zqlogneedle42" not in joined, f"dictated text reached the log: {joined!r}"
