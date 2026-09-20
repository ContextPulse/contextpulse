# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Typed keystroke text must never reach storage, the FTS index, or an MCP client.

Sibling of packages/screen/tests/test_clipboard_redaction.py, for the modality
the clipboard fix did not cover. The touch MCP module's own docstring claimed
"Privacy-safe: shows activity patterns, not keystrokes", which was false for the
corrections branch: get_correction_history and
get_recent_touch_events(event_types="corrections") returned the literal
before-and-after text of what the user typed
(cp-burst-text-correction-text-unredacted).

Each case asserts the secret appears in NONE of:
  1. the `events` table (payload JSON)
  2. the `events_fts` full-text index (probed by MATCH, with a positive control
     on window_title -- correction_text is not FTS-indexed by the live trigger,
     so without the control "0 hits" would be equally consistent with an empty
     index)
  3. get_recent_touch_events (all and corrections filters)
  4. get_correction_history
  5. get_touch_stats (counts only -- included so a future change that starts
     printing text is caught here)
  6. the raw bytes of the database file on disk

Every value below is SYNTHETIC.
"""

import json
import sqlite3
import time

import pytest
from contextpulse_core.redact import redact_sensitive
from contextpulse_core.spine import EventBus
from contextpulse_touch import mcp_server
from contextpulse_touch.correction_detector import VocabularyBridge
from contextpulse_touch.touch_module import TouchModule

CONTROL_WORD = "zqcontrol"

# (family, the word the user typed, the substring that must disappear)
TYPED_SECRETS = [
    ("github_token", "ghp_zqtouchneedle0123456789abcdefghijklmn", "ghp_zqtouchneedle0123456789abcdefghijklmn"),
    ("aws_access_key", "AKIAZQTOUCHNEEDLE012", "AKIAZQTOUCHNEEDLE012"),
    ("anthropic_key", "sk-ant-zqtouchanthropic0123456789", "sk-ant-zqtouchanthropic0123456789"),
    ("ssn", "987-65-4321", "987-65-4321"),
    ("credit_card", "4111-1111-1111-1111", "4111-1111-1111-1111"),
    # Glued to a word character -- the anchoring gap this branch also closes.
    (
        "glued_github_token",
        "notesghp_zqgluedtouch0123456789abcdefghijklmn",
        "ghp_zqgluedtouch0123456789abcdefghijklmn",
    ),
]

FAMILY_IDS = [c[0] for c in TYPED_SECRETS]


def _read_db_bytes(db_path):
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            blob += sidecar.read_bytes()
    return blob


def _emit_one_correction(tmp_path, typed_secret):
    """Drive one correction through the real TouchModule and the real EventBus.

    The payload is built in TouchModule._on_correction from the dict
    CorrectionDetector hands it -- that dict is what is reproduced here, keys
    and all. Everything downstream (event construction, validation, the
    INSERT, the FTS trigger) is the production path.
    """
    db_path = tmp_path / "activity.db"
    bus = EventBus(db_path)
    module = TouchModule()
    module.register(bus.emit)
    module.start()
    try:
        module._on_correction({
            "original_word": typed_secret,
            "corrected_word": f"{typed_secret}X",
            "correction_type": "retype",
            "confidence": 0.9,
            "seconds_after_paste": 1.2,
            "paste_event_id": "evt-zq-1",
        })
        # A second event whose window_title carries the control word, so the
        # FTS probe below has something it is known to be able to find.
        module._on_mouse_click({
            "x": 1, "y": 2, "button": "left",
            "app_name": "Code.exe", "window_title": f"{CONTROL_WORD} editor",
        })
    finally:
        module.stop()
        bus.close()
    return db_path


def _call_touch_tools(db_path):
    original = mcp_server._DB_PATH
    mcp_server._DB_PATH = db_path
    try:
        return {
            "get_recent_touch_events_all": mcp_server.get_recent_touch_events(seconds=3600),
            "get_recent_touch_events_corrections": mcp_server.get_recent_touch_events(
                seconds=3600, event_types="corrections",
            ),
            "get_correction_history": mcp_server.get_correction_history(limit=50),
            "get_touch_stats": mcp_server.get_touch_stats(hours=1.0),
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


class TestTypedSecretsAreRedactableAtAll:
    """Guard on the fixtures: a value no pattern matches was never a secret."""

    @pytest.mark.parametrize("family,typed,needle", TYPED_SECRETS, ids=FAMILY_IDS)
    def test_pattern_removes_it(self, family, typed, needle):
        assert needle not in redact_sensitive(typed), f"{family}: fixture is not redactable"


class TestTypedSecretsNeverReachStorageOrMCP:
    @pytest.mark.parametrize("family,typed,needle", TYPED_SECRETS, ids=FAMILY_IDS)
    def test_secret_absent_everywhere(self, tmp_path, family, typed, needle):
        db_path = _emit_one_correction(tmp_path, typed)

        conn = sqlite3.connect(str(db_path))
        payloads = [
            r[0] for r in conn.execute(
                "SELECT payload FROM events WHERE event_type = 'correction_detected'"
            ).fetchall()
        ]
        conn.close()

        # The emit must have happened, or every "not in" below is vacuous.
        assert len(payloads) == 1, f"{family}: expected exactly one correction event"
        payload = json.loads(payloads[0])
        for key in ("original_text", "corrected_text", "correction_text"):
            assert key in payload, f"{family}: {key} missing -- the shape changed"
            assert needle not in payload[key], f"{family}: leaked via payload.{key}"
        assert needle not in payloads[0], f"{family}: leaked into events.payload"

        # Positive control: the FTS index exists and MATCH can find things in it.
        assert _fts_hits(db_path, CONTROL_WORD) == 1, (
            f"{family}: control not indexed; the MATCH probe below proves nothing"
        )
        probe = needle.lower().lstrip("-").replace("-", "")
        assert _fts_hits(db_path, probe) == 0, f"{family}: discoverable through events_fts"

        responses = _call_touch_tools(db_path)
        # Positive control on the tools too: they returned real content.
        assert "CORRECTION" in responses["get_correction_history"] or "->" in (
            responses["get_correction_history"]
        ), f"{family}: get_correction_history returned nothing to redact"
        for tool, response in responses.items():
            assert needle not in str(response), f"{family}: leaked through MCP tool {tool}"

        assert needle.encode() not in _read_db_bytes(db_path), (
            f"{family}: secret found in the raw database file"
        )


class TestPreFixRowsAreRedactedAtTheMCPBoundary:
    """Rows written before this branch are still raw on disk.

    The test above drives the fixed module, so its store is already clean and
    its MCP assertions would pass with no boundary redaction at all. This one
    writes a RAW row straight to the events table -- what the daemon wrote
    until today -- and asserts the tools scrub it on the way out.
    """

    @pytest.mark.parametrize("family,typed,needle", TYPED_SECRETS, ids=FAMILY_IDS)
    def test_tools_scrub_raw_rows(self, tmp_path, family, typed, needle):
        db_path = tmp_path / "activity.db"
        bus = EventBus(db_path)
        bus.close()

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
            " window_title, monitor_index, payload, correlation_id, attention_score,"
            " cognitive_load) VALUES (?, ?, 'keys', 'correction_detected', ?, ?, 0, ?,"
            " NULL, 0.0, 0.0)",
            (
                "raw-zq-1",
                time.time(),
                f"{CONTROL_WORD}.exe",
                f"{CONTROL_WORD} editor",
                json.dumps({
                    "original_text": typed,
                    "corrected_text": typed,
                    "correction_text": f"{typed} -> {typed}",
                    "confidence": 0.9,
                    "correction_type": "retype",
                    "seconds_after_paste": 1.0,
                }),
            ),
        )
        conn.commit()
        conn.close()

        responses = _call_touch_tools(db_path)
        for tool in ("get_recent_touch_events_corrections", "get_correction_history"):
            assert CONTROL_WORD in str(responses[tool]) or "->" in str(responses[tool]), (
                f"{family}: {tool} returned nothing to redact -- assertion is vacuous"
            )
        for tool, response in responses.items():
            assert needle not in str(response), (
                f"{family}: raw pre-fix row leaked through MCP tool {tool}"
            )


class TestVocabularyBridgeRefusesSecrets:
    """A correction pair is word-level, so a word CAN be a whole token.

    VocabularyBridge writes the pair verbatim into vocabulary_learned.json,
    which the voice get_vocabulary tool reads back. That file is the one place
    in this codebase where a stored word LIST can hold a whole secret.
    """

    @pytest.mark.parametrize("family,typed,needle", TYPED_SECRETS, ids=FAMILY_IDS)
    def test_a_secret_correction_is_not_learned(self, tmp_path, family, typed, needle):
        learned = tmp_path / "vocabulary_learned.json"
        bridge = VocabularyBridge(learned_file=learned)

        assert bridge.add_correction(typed, "harmless") is False
        assert bridge.add_correction("harmless", typed) is False
        assert not learned.exists() or needle not in learned.read_text(encoding="utf-8")

    def test_an_ordinary_correction_is_still_learned(self, tmp_path):
        # The refusal must not swallow the feature it guards.
        learned = tmp_path / "vocabulary_learned.json"
        bridge = VocabularyBridge(learned_file=learned)
        assert bridge.add_correction("quokka", "Quokka") is True
        assert json.loads(learned.read_text(encoding="utf-8")) == {"quokka": "Quokka"}


class TestPasteCorrelationSurvivesRedaction:
    """Review S-3, the half that could have broken silently.

    VoiceModule now stores paste_text_hash over the REDACTED transcript, so
    this detector -- which hashes the clipboard it reads -- has to derive its
    digest the same way. If only one side redacted first, the correlation
    would stop working for exactly the dictations that contained a secret, and
    nothing would report it: on_paste_detected simply returns, no correction is
    ever harvested, and the pipeline looks idle rather than broken. That is the
    argument the original code used for hashing the raw text.
    """

    SPOKEN = f"{CONTROL_WORD} my social is 987-65-4321"

    def _db_with_voice_event(self, tmp_path, stored_hash):
        db_path = tmp_path / "activity.db"
        bus = EventBus(db_path)
        bus.close()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
            " window_title, monitor_index, payload, correlation_id, attention_score,"
            " cognitive_load) VALUES (?, ?, 'voice', 'transcription', 'code.exe',"
            " 'editor', 0, ?, NULL, 0.0, 0.0)",
            (
                "voice-corr-1", time.time() - 2,
                json.dumps({
                    "transcript": redact_sensitive(self.SPOKEN),
                    "paste_text_hash": stored_hash,
                }),
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def _detector(self, db_path, tmp_path):
        from contextpulse_touch.burst_tracker import BurstTracker
        from contextpulse_touch.correction_detector import CorrectionDetector

        return CorrectionDetector(
            burst_tracker=BurstTracker(burst_timeout=0.1, min_chars=1),
            watch_seconds=0.5,
            db_path=db_path,
            bridge=VocabularyBridge(learned_file=tmp_path / "learned.json"),
        )

    def test_a_secret_bearing_paste_still_correlates(self, tmp_path):
        from contextpulse_core.redact import redacted_text_digest

        db_path = self._db_with_voice_event(tmp_path, redacted_text_digest(self.SPOKEN))
        det = self._detector(db_path, tmp_path)
        try:
            # The clipboard holds the RAW text -- that is what was pasted.
            det.on_paste_detected(self.SPOKEN)
            assert det.is_watching, (
                "the two sides computed different digests: voice corrections "
                "would silently stop being harvested for secret-bearing dictations"
            )
        finally:
            det.stop()

    def test_an_unrelated_paste_still_does_not_correlate(self, tmp_path):
        """The positive control's opposite: the match is not vacuous."""
        from contextpulse_core.redact import redacted_text_digest

        db_path = self._db_with_voice_event(tmp_path, redacted_text_digest(self.SPOKEN))
        det = self._detector(db_path, tmp_path)
        try:
            det.on_paste_detected("something else entirely")
            assert not det.is_watching
        finally:
            det.stop()


class TestLogsDoNotCarryTypedText:
    """Two log lines used to print the user's own text into a rotating file."""

    def test_learned_correction_log_has_no_values(self, tmp_path, caplog):
        bridge = VocabularyBridge(learned_file=tmp_path / "vocabulary_learned.json")
        with caplog.at_level("INFO"):
            assert bridge.add_correction("quokka", "Quokka") is True
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "quokka" not in joined.lower(), f"correction text reached the log: {joined!r}"
        assert "chars" in joined
