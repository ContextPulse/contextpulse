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


def _extract_by_counting(probe_fn, alphabet, max_len, seed=""):
    """The attack: extend a prefix one character at a time, keeping whatever
    still returns a non-zero count. Exactly what the review's harness did.

    `seed` models an attacker who already knows how the token starts -- which
    is the realistic case, since every secret family has a fixed prefix. Without
    it the greedy walk latches onto whichever character matches the BENIGN
    content first and the attack never reaches the secret, so the test would
    pass against vulnerable code.
    """
    recovered = seed
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


def _raw_activity_store(tmp_path):
    """A store holding one raw pre-fix OCR row in the `activity` table.

    `activity` is the largest text store in the product and the one the first
    round missed entirely: ActivityDB.search matched raw ocr_text through
    activity_fts while the MCP tool redacted only the rendered snippet.
    update_ocr is used deliberately -- it is the daemon's own writer and it
    stores what it is given, which is what a pre-redaction row looks like.
    """
    from contextpulse_sight.activity import ActivityDB

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    row_id = db.record(
        timestamp=time.time(),
        window_title=f"{CONTROL_WORD} editor",
        app_name="Code.exe",
    )
    db.update_ocr(row_id, f"{CONTROL_WORD} {SECRET}", 0.9)
    db.close()
    return db_path


class TestHistorySearchIsNotAPrefixOracle:
    """Review B-1, reproduced. activity_fts uses the default tokenizer with no
    stemmer, so a prefix query is a clean binary signal and the reviewer
    recovered 13 characters of a planted key from result counts alone."""

    def test_counting_recovers_nothing_beyond_the_marker(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_activity_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            # Positive control: the row IS indexed and IS findable by its
            # non-secret content, so the zero counts below mean redaction and
            # not an empty table.
            assert len(db.search(CONTROL_WORD, minutes_ago=60)) == 1

            def probe(prefix):
                return len(db.search(prefix + "*", minutes_ago=60))

            # Seeded with what an attacker already knows -- every secret family
            # has a fixed prefix -- so the walk starts INSIDE the planted token
            # instead of latching onto "Code.exe".
            seeded = _extract_by_counting(
                probe, ALPHABET, max_len=len(SECRET) + 5, seed="zqoracl"
            )
            blind = _extract_by_counting(probe, ALPHABET, max_len=len(SECRET) + 5)
        finally:
            db.close()

        assert seeded == "zqoracl", (
            f"the count oracle extended a known prefix into the secret: {seeded!r}"
        )
        assert "zqoracleneedle" not in blind, (
            f"the count oracle recovered part of the secret body: {blind!r}"
        )
        assert SECRET.lower() not in blind

    @pytest.mark.parametrize(
        "probe", ["zqoracleneedle*", "zqoracleneedle0123456789abcdefghij", "sk*"]
    )
    def test_a_direct_probe_for_the_secret_returns_nothing(self, tmp_path, probe):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_activity_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            assert db.search(probe, minutes_ago=60) == []
        finally:
            db.close()

    def test_the_like_fallback_is_filtered_too(self, tmp_path):
        """A query FTS5 refuses falls through to `ocr_text LIKE ?` on the RAW
        column, which is the same oracle by another route."""
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_activity_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            assert db.search(SECRET, minutes_ago=60) == []
        finally:
            db.close()

    def test_returned_rows_carry_redacted_text(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_activity_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            rows = db.search(CONTROL_WORD, minutes_ago=60)
        finally:
            db.close()
        assert len(rows) == 1
        assert CONTROL_WORD in rows[0]["ocr_text"], "returned nothing to redact"
        assert SECRET not in rows[0]["ocr_text"], (
            "search returned the raw column; a caller rendering this "
            "reintroduces the leak the MCP layer was closing"
        )

    def test_the_mcp_header_count_is_computed_over_redacted_text(self, tmp_path):
        from contextpulse_sight import mcp_server
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_activity_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        original = mcp_server._activity_db
        mcp_server._activity_db = db
        try:
            with patch(
                "contextpulse_sight.mcp_server.is_title_blocked", return_value=False
            ):
                hit = mcp_server.search_history(CONTROL_WORD, minutes_ago=60)
                miss = mcp_server.search_history("zqoracleneedle*", minutes_ago=60)
        finally:
            mcp_server._activity_db = original
            db.close()

        assert "(1 results)" in hit, f"control did not match: {hit!r}"
        assert SECRET not in hit
        assert "No results" in miss, (
            "the header count still reports a match computed over raw ocr_text"
        )


class TestClipboardScanCapIsDeclared:
    """Review S-8: the 2000-row cap was a silent behaviour change.

    Matching against REDACTED text is what closed the count oracle, and a
    bounded scan is what makes it affordable -- but the tool said nothing when
    the bound bit, so "no clipboard entries matching X" could mean "none in the
    2000 rows I looked at" while the answer sat in row 2001.
    """

    def _store_with(self, tmp_path, rows):
        from contextpulse_sight.activity import ActivityDB

        db_path = tmp_path / "activity.db"
        db = ActivityDB(db_path=db_path)
        now = time.time()
        for i in range(rows):
            db.record_clipboard(timestamp=now - i, text=f"{CONTROL_WORD} note {i}")
        return db

    def test_the_flag_appears_when_the_cap_binds(self, tmp_path, monkeypatch):
        from contextpulse_sight import mcp_server
        from contextpulse_sight.activity import ActivityDB

        monkeypatch.setattr(ActivityDB, "_SEARCH_SCAN_LIMIT", 2)
        db = self._store_with(tmp_path, 5)
        monkeypatch.setattr(mcp_server, "_activity_db", db)
        try:
            out = mcp_server.search_clipboard(CONTROL_WORD, minutes_ago=60)
            miss = mcp_server.search_clipboard("zqabsent", minutes_ago=60)
        finally:
            db.close()

        assert "truncated: true" in out
        assert "2 most recent of 5" in out
        assert "truncated: true" in miss, (
            "a zero-result answer is exactly where the caller needs to know "
            "the scan was partial"
        )

    def test_no_flag_when_the_whole_window_was_scanned(self, tmp_path, monkeypatch):
        from contextpulse_sight import mcp_server

        db = self._store_with(tmp_path, 3)
        monkeypatch.setattr(mcp_server, "_activity_db", db)
        try:
            out = mcp_server.search_clipboard(CONTROL_WORD, minutes_ago=60)
        finally:
            db.close()
        assert "truncated" not in out

    def test_the_cap_is_documented_in_the_tool_description(self):
        """The description is what an MCP client reads before calling."""
        from contextpulse_sight import mcp_server

        doc = mcp_server.search_clipboard.__doc__
        assert "2000" in doc and "truncated: true" in doc

    def test_the_window_count_does_not_reveal_content(self, tmp_path):
        """The count is over a TIME RANGE, so it cannot be used as an oracle."""
        from contextpulse_sight.activity import ActivityDB

        db_path = _raw_store(tmp_path)
        db = ActivityDB(db_path=db_path)
        try:
            assert db.clipboard_rows_in_window(60) == 1
            assert db.clipboard_rows_in_window(0) == 0
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

    @pytest.mark.parametrize("probe", ["zqoracles*", "zqoracleneedles*", "zqoracle*"])
    def test_a_stemmed_probe_cannot_survive_the_filter(self, tmp_path, probe):
        """Review S-4: the filter was a literal substring test, events_fts is porter.

        events_fts is declared tokenize='porter unicode61', so FTS matches on
        STEMS. The first filter asked `term in raw and term not in red` -- a
        literal containment test. Appending an "s" to a probe gives a term whose
        STEM still hits the secret token but whose literal form is not a
        substring of the row, so the rule never fired and the count signal
        survived. Demonstrated by the reviewer end to end.

        The fix re-runs the query through an FTS index built over the REDACTED
        text with the same tokenizer, so the query parser and the stemmer are
        the same ones that produced the hit.
        """
        from contextpulse_core.spine import EventBus

        db_path = _raw_store(tmp_path)
        bus = EventBus(db_path)
        try:
            # Positive control on the same index and the same query SHAPE: a
            # stemmed prefix probe against non-secret content must still work,
            # or this assertion passes by breaking search rather than by
            # closing the oracle.
            assert len(bus.search("zqcontrols*", minutes_ago=60)) == 1, (
                "the stemmed-prefix control found nothing -- search is broken, "
                "so the assertion below proves nothing"
            )
            assert bus.search(probe, minutes_ago=60) == [], (
                f"{probe!r} still returns a row: the stemmer routes around the "
                "filter and the count is an oracle again"
            )
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
