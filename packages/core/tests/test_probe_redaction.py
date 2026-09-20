# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""probe.db is a THIRD copy of captured text, and it leaves the machine.

Review findings S1 and (coordinator correction) the consolidator location.
probe.read_recent_events reads all five payload text keys out of `events`,
build_extraction_prompt formats them into a prompt, and
scripts/probe_consolidator.py sends that prompt to `claude -p`. The resulting
fact sentences land in probe.db's `facts` table, which facts_about and
context_at -- both LIVE MCP tools -- serve with no redaction anywhere.

Two separate properties, and they need separate tests:

EGRESS    the prompt built from a pre-fix event must not carry the secret,
          because that string goes to an external model.
BOUNDARY  a fact already in probe.db quoting a pre-fix secret must not be
          served, because no purge of activity.db touches probe.db.

Every value below is SYNTHETIC.
"""

import json
import sqlite3

import pytest
from contextpulse_core import probe, probe_mcp
from contextpulse_core.redact import redact_sensitive

CONTROL_WORD = "zqcontrol"

SECRETS = [
    ("ocr_text", "ghp_zqprobeneedle0123456789abcdefghijklmn"),
    ("transcript", "password: zqprobespoken42"),
    ("text", "sk-ant-zqprobeclipboard0123456789"),
    ("burst_text", "AKIAZQPROBENEEDLE012"),
    ("correction_text", "987-65-4321"),
]

IDS = [s[0] for s in SECRETS]

_DDL = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL,
    modality TEXT,
    event_type TEXT,
    app_name TEXT,
    window_title TEXT,
    payload TEXT
)
"""


def _events_db(tmp_path, key, secret):
    conn = sqlite3.connect(str(tmp_path / "activity.db"))
    conn.execute(_DDL)
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
        (
            "e-zq-1", 100.0, "sight", "capture", "Code.exe", f"{CONTROL_WORD} editor",
            json.dumps({key: f"{CONTROL_WORD} {secret}"}),
        ),
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


class TestFixturesAreRedactable:
    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_pattern_removes_it(self, key, secret):
        assert secret not in redact_sensitive(secret), f"{key}: fixture is not redactable"


class TestConsolidatorInputIsRedacted:
    """The prompt goes to an external model. This is an EGRESS test."""

    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_read_recent_events_redacts(self, tmp_path, key, secret):
        conn = _events_db(tmp_path, key, secret)
        try:
            events = probe.read_recent_events(conn, since_ts=0.0)
        finally:
            conn.close()
        assert len(events) == 1, f"{key}: nothing read -- assertion would be vacuous"
        assert CONTROL_WORD in events[0]["text"], f"{key}: text is empty -- vacuous"
        assert secret not in events[0]["text"], f"{key}: raw text left the read path"

    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_prompt_carries_no_secret(self, tmp_path, key, secret):
        conn = _events_db(tmp_path, key, secret)
        try:
            events = probe.read_recent_events(conn, since_ts=0.0)
        finally:
            conn.close()
        prompt = probe.build_extraction_prompt(events)
        assert CONTROL_WORD in prompt, f"{key}: prompt has no event text -- vacuous"
        assert secret not in prompt, f"{key}: secret would be sent to the model"

    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_prompt_builder_redacts_independently_of_the_reader(self, key, secret):
        """build_extraction_prompt is public and a caller can hand it anything.

        Redaction is idempotent, so the second pass costs a regex sweep and
        buys a guarantee that does not depend on the caller having used
        read_recent_events.
        """
        prompt = probe.build_extraction_prompt([{
            "event_id": "e-zq-2", "timestamp": 1.0, "modality": "sight",
            "app_name": "Code.exe", "window_title": "x",
            "text": f"{CONTROL_WORD} {secret}",
        }])
        assert CONTROL_WORD in prompt
        assert secret not in prompt, f"{key}: hand-assembled event text reached the prompt"

    def test_redaction_precedes_the_prompt_truncation(self):
        """A token the _MAX_TEXT_CHARS cut halves matches nothing."""
        secret = "ghp_zqprobeboundary0123456789abcdefghijklm"
        head = secret[:30]
        filler = "x" * (probe._MAX_TEXT_CHARS - len(CONTROL_WORD) - 2 - 30)
        text = f"{CONTROL_WORD} {filler} {secret} tail"
        prompt = probe.build_extraction_prompt([{
            "event_id": "e", "timestamp": 1.0, "modality": "sight",
            "app_name": "a", "window_title": "w", "text": text,
        }])
        assert secret not in prompt
        assert head not in prompt, "the truncation sent the leading half of a token"


class TestFactsAreRedactedAtTheMCPBoundary:
    """Facts minted by consolidator runs that predate the fixes are on disk."""

    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_fmt_facts_scrubs_a_stored_fact(self, key, secret):
        rendered = probe_mcp._fmt_facts([{
            "entity": f"{CONTROL_WORD}-project",
            "fact": f"deploy key is {secret}",
            "confidence": 0.9,
            "valid_from": 1_760_000_000.0,
            "source_event_ids": ["e1"],
        }])
        assert CONTROL_WORD in rendered, f"{key}: nothing rendered -- vacuous"
        assert secret not in rendered, f"{key}: stored fact leaked through facts_about"

    @pytest.mark.parametrize("key,secret", SECRETS, ids=IDS)
    def test_fmt_facts_scrubs_the_entity_too(self, key, secret):
        # The entity is model-authored as well, so it can carry a value.
        rendered = probe_mcp._fmt_facts([{
            "entity": f"{CONTROL_WORD} {secret}",
            "fact": "was referenced",
            "confidence": 0.5,
            "valid_from": 1_760_000_000.0,
            "source_event_ids": [],
        }])
        assert CONTROL_WORD in rendered
        assert secret not in rendered, f"{key}: entity field leaked"
