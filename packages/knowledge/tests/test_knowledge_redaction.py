# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""knowledge.db is a SECOND COPY of captured text, and nothing purged it.

Review finding B2. bridge.py ingests clipboard_change, transcription and
typing_burst events into `observations`, whose text is FTS-indexed and served
verbatim by search_knowledge. A purge of activity.db reports "0 remaining
matches" while the same secret sits here untouched.

Asserts a planted secret appears in NONE of:
  1. observations.content in knowledge.db
  2. the observation FTS index (probed by the store's own search, with a
     positive control so "no hits" cannot mean "empty index")
  3. search_knowledge's rendered output
  4. the raw bytes of knowledge.db on disk

Every value below is SYNTHETIC.
"""

import json
import sqlite3

import pytest
from contextpulse_core.redact import redact_sensitive
from contextpulse_knowledge import bridge, mcp_tools
from contextpulse_knowledge.store_sqlite import KnowledgeStore

BASE = 1_760_000_000.0
CONTROL_WORD = "zqcontrol"

SECRETS = [
    # ghp_ needs 36+ trailing characters -- the fixture guard caught a 34.
    ("clipboard_github", "text", "ghp_zqkgneedle0123456789abcdefghijklmnop"),
    ("transcript_password", "transcript", "password: zqkgspokenneedle42"),
    ("burst_aws", "burst_text", "AKIAZQKGNEEDLE012345"),
    ("clipboard_stripe", "text", "sk_live_zqkgstripeneedle0123456"),
]

IDS = [s[0] for s in SECRETS]

_EVENTS_DDL = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    modality TEXT NOT NULL,
    event_type TEXT NOT NULL,
    app_name TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    monitor_index INTEGER DEFAULT 0,
    payload TEXT NOT NULL,
    correlation_id TEXT,
    attention_score REAL DEFAULT 0.0,
    cognitive_load REAL DEFAULT 0.0
)
"""

_EVENT_TYPE_FOR_KEY = {
    "text": "clipboard_change",
    "transcript": "transcription",
    "burst_text": "typing_burst",
}


def _make_events_db(path, key, secret):
    conn = sqlite3.connect(str(path))
    conn.execute(_EVENTS_DDL)
    conn.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,0,?,NULL,0.0,0.0)",
        (
            "e-zq-1",
            BASE,
            "sight",
            _EVENT_TYPE_FOR_KEY[key],
            "Code.exe",
            f"{CONTROL_WORD} editor",
            json.dumps({key: f"{CONTROL_WORD} {secret}"}),
        ),
    )
    conn.commit()
    conn.close()


def _backfill(tmp_path, key, secret):
    events_db = tmp_path / "activity.db"
    _make_events_db(events_db, key, secret)
    kdb = tmp_path / "knowledge.db"
    store = KnowledgeStore(str(kdb))
    try:
        stats = bridge.backfill(str(events_db), store, since_days=30, now_s=BASE + 100)
        assert stats["ingested"] >= 1, "nothing was ingested -- assertions would be vacuous"
    finally:
        store.close()
    return kdb


class TestFixturesAreRedactable:
    @pytest.mark.parametrize("family,key,secret", SECRETS, ids=IDS)
    def test_pattern_removes_it(self, family, key, secret):
        assert secret not in redact_sensitive(secret), f"{family}: fixture is not redactable"


class TestIngestRedactsObservationText:
    @pytest.mark.parametrize("family,key,secret", SECRETS, ids=IDS)
    def test_secret_absent_from_knowledge_db(self, tmp_path, family, key, secret):
        kdb = _backfill(tmp_path, key, secret)

        conn = sqlite3.connect(str(kdb))
        contents = [r[0] or "" for r in conn.execute("SELECT content FROM observations")]
        conn.close()

        assert contents, f"{family}: no observations stored"
        joined = " ".join(contents)
        assert CONTROL_WORD in joined, f"{family}: observation text is empty -- vacuous"
        assert secret not in joined, f"{family}: leaked into observations.content"

        assert secret.encode() not in kdb.read_bytes(), (
            f"{family}: secret found in the raw knowledge.db bytes"
        )

    @pytest.mark.parametrize("family,key,secret", SECRETS, ids=IDS)
    def test_secret_not_searchable(self, tmp_path, family, key, secret):
        kdb = _backfill(tmp_path, key, secret)
        store = KnowledgeStore(str(kdb))
        try:
            # Positive control -- the FTS index is populated and reachable.
            assert store.search(CONTROL_WORD, k=10, mode="fts"), (
                f"{family}: control not searchable; the probe below proves nothing"
            )
            probe = secret.lower().replace("-", "").replace(":", "").split()[-1]
            hits = store.search(probe, k=10, mode="fts")
        finally:
            store.close()
        assert not hits, f"{family}: secret is discoverable through the observation index"


class TestSearchKnowledgeBoundary:
    """Observations ingested before the bridge started redacting are raw."""

    @pytest.mark.parametrize("family,key,secret", SECRETS, ids=IDS)
    def test_a_raw_snippet_is_scrubbed_on_the_way_out(self, family, key, secret, monkeypatch):
        class _RawStore:
            """Stands in for a store holding pre-fix observations."""

            def search(self, query, k=10, mode="fts"):
                return [{"snippet": f"{CONTROL_WORD} {secret}", "observed_at": BASE * 1000}]

            def close(self):
                pass

        monkeypatch.setattr(mcp_tools, "_open_store", lambda: _RawStore())
        out = mcp_tools.search_knowledge(CONTROL_WORD)
        assert CONTROL_WORD in out, f"{family}: returned nothing to redact -- vacuous"
        assert secret not in out, f"{family}: raw pre-fix snippet leaked through search_knowledge"
