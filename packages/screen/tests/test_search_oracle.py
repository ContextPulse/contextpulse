# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""A search must not leak through its RESULT COUNT.

Review finding B1, reproduced as a permanent test. Redacting a tool's OUTPUT
does not close a query oracle: search_clipboard ran `WHERE text LIKE ?` against
the raw stored column and the MCP tool printed the match count, so a client
could issue "sk-", "sk-a", "sk-ab" ... and recover a pre-fix secret ONE
CHARACTER AT A TIME while every response it saw was correctly redacted.
search_all_events had the same shape at FTS-token granularity.

The first test below IS that attack, run against the fixed code. It walks the
alphabet exactly as an attacker would and asserts it learns nothing past the
prefix that is common to the redaction marker itself.

The rows planted here are RAW -- written straight to the tables, bypassing the
capture path -- because pre-fix rows are the population an attacker would
probe, and because a store that is already clean cannot test this at all.

Every value below is SYNTHETIC.
"""

import json
import sqlite3
import string
import time
from unittest.mock import patch

import pytest

CONTROL_WORD = "zqcontrol"
SECRET = "sk-zqoracleneedle0123456789abcdefghij"


def _raw_store(tmp_path):
    """A store holding one raw pre-fix clipboard row and one raw event."""
    from contextpulse_core.spine import EventBus
    from contextpulse_sight.activity import ActivityDB

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    now = time.time()
    db.record_clipboard(timestamp=now, text=f"{CONTROL_WORD} {SECRET}")
    db.close()

    bus = EventBus(db_path)
    bus.close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO events (event_id, timestamp, modality, event_type, app_name,"
        " window_title, monitor_index, payload, correlation_id, attention_score,"
        " cognitive_load) VALUES (?, ?, 'clipboard', 'clipboard_change', 'Code.exe',"
        " ?, 0, ?, NULL, 0.0, 0.0)",
        (
            "e-oracle-1", now, f"{CONTROL_WORD} editor",
            json.dumps({"text": f"{CONTROL_WORD} {SECRET}"}),
        ),
    )
    conn.commit()
    conn.close()
    return db_path


def _extract_by_counting(probe_fn, alphabet, max_len):
    """The attack: extend a prefix one character at a time, keeping whatever
    still returns a non-zero count. Exactly what the review's harness did."""
    recovered = ""
    for _ in range(max_len):
        for ch in alphabet:
            if probe_fn(recovered + ch) > 0:
                recovered += ch
                break
        else:
            break
    return recovered


ALPHABET = string.ascii_lowercase + string.digits + "-_"


class TestClipboardSearchIsNotAnOracle:
    def test_counting_recovers_nothing_beyond_the_marker(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            # Positive control: the row IS there and IS findable by its
            # non-secret content, so a zero count below means redaction and
            # not an empty table.
            assert len(db.search_clipboard(CONTROL_WORD, minutes_ago=60)) == 1

            def probe(prefix):
                return len(db.search_clipboard(prefix, minutes_ago=60))

            recovered = _extract_by_counting(probe, ALPHABET, max_len=len(SECRET) + 5)
        finally:
            db.close()

        assert SECRET.lower() not in recovered, (
            f"the count oracle recovered the secret: {recovered!r}"
        )
        # Nothing past "sk-" may be recoverable. The marker "[REDACTED:API_KEY]"
        # contains no "sk-", so even that prefix should die immediately.
        assert "zqoracleneedle" not in recovered, (
            f"the count oracle recovered part of the secret body: {recovered!r}"
        )

    def test_returned_rows_carry_redacted_text(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            rows = db.search_clipboard(CONTROL_WORD, minutes_ago=60)
        finally:
            db.close()
        assert len(rows) == 1
        assert CONTROL_WORD in rows[0]["text"], "returned nothing to redact"
        assert SECRET not in rows[0]["text"], (
            "search_clipboard returned the raw column; a caller rendering this "
            "reintroduces the leak the MCP layer was closing"
        )

    def test_a_substring_of_the_secret_finds_nothing(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            assert db.search_clipboard("zqoracleneedle", minutes_ago=60) == []
            assert db.search_clipboard(SECRET, minutes_ago=60) == []
        finally:
            db.close()


class TestEventSearchIsNotATokenOracle:
    def test_a_redacted_token_returns_no_rows(self, tmp_path):
        from contextpulse_core.spine import EventBus

        db_path = _raw_store(tmp_path)
        bus = EventBus(db_path)
        try:
            # Positive control -- the event is indexed and findable.
            assert len(bus.search(CONTROL_WORD, minutes_ago=60)) == 1
            assert bus.search("zqoracleneedle0123456789abcdefghij", minutes_ago=60) == []
        finally:
            bus.close()

    def test_stemmed_and_ordinary_matches_still_work(self, tmp_path):
        """The filter is narrow on purpose: it must not break the search.

        A term absent from BOTH the raw and the redacted text (a stem, a
        prefix match) never triggers the rule, so FTS stemming survives.
        """
        from contextpulse_core.spine import EventBus

        db_path = tmp_path / "plain.db"
        bus = EventBus(db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO events (event_id, timestamp, modality, event_type,"
                " app_name, window_title, monitor_index, payload, correlation_id,"
                " attention_score, cognitive_load) VALUES (?, ?, 'sight',"
                " 'ocr_result', 'Code.exe', 'notes', 0, ?, NULL, 0.0, 0.0)",
                ("e-plain-1", time.time(), json.dumps({"ocr_text": "running the importer"})),
            )
            conn.commit()
            conn.close()
            assert len(bus.search("run", minutes_ago=60)) == 1, (
                "the oracle filter broke porter stemming"
            )
            assert len(bus.search("importer", minutes_ago=60)) == 1
        finally:
            bus.close()


class TestMcpToolCountsAreComputedOverRedactedText:
    def test_search_clipboard_header_count(self, tmp_path):
        from contextpulse_sight import mcp_server
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        original = mcp_server._activity_db
        mcp_server._activity_db = db
        try:
            hit = mcp_server.search_clipboard(CONTROL_WORD, minutes_ago=60)
            miss = mcp_server.search_clipboard("zqoracleneedle", minutes_ago=60)
        finally:
            mcp_server._activity_db = original
            db.close()

        assert "(1 results)" in hit, f"control did not match: {hit!r}"
        assert SECRET not in hit
        assert "(1 results)" not in miss, (
            "the header count still reports a match computed over raw text"
        )

    def test_search_all_events_header_count(self, tmp_path):
        from contextpulse_core.spine import EventBus
        from contextpulse_sight import mcp_server
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        bus = EventBus(db_path)
        original_db, original_bus = mcp_server._activity_db, mcp_server._event_bus
        mcp_server._activity_db, mcp_server._event_bus = db, bus
        try:
            with (
                patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True),
                patch("contextpulse_sight.mcp_server.is_title_blocked", return_value=False),
            ):
                hit = mcp_server.search_all_events(CONTROL_WORD, minutes_ago=60)
                miss = mcp_server.search_all_events(
                    "zqoracleneedle0123456789abcdefghij", minutes_ago=60,
                )
        finally:
            mcp_server._activity_db, mcp_server._event_bus = original_db, original_bus
            bus.close()
            db.close()

        assert "(1 results)" in hit, f"control did not match: {hit!r}"
        assert SECRET not in hit
        assert "No results" in miss, (
            "the FTS token oracle still reports a hit for a redacted token"
        )


@pytest.mark.parametrize("term", ["sk-zqoracle", "zqoracleneedle0123", "0123456789abcdefghij"])
def test_no_search_surface_confirms_a_secret_substring(tmp_path, term):
    """One assertion across both surfaces, so a new one cannot be added
    without someone noticing this file exists."""
    from contextpulse_core.spine import EventBus
    from contextpulse_sight.activity import ActivityDB

    db_path = _raw_store(tmp_path)
    db = ActivityDB(db_path=db_path)
    bus = EventBus(db_path)
    try:
        assert db.search_clipboard(term, minutes_ago=60) == []
        assert bus.search(term, minutes_ago=60) == []
    finally:
        bus.close()
        db.close()
