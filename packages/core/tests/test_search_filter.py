# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The shared oracle filter: a count must describe REDACTED text.

Review S-4. The first version of this filter was a literal substring test
(`term in raw and term not in red`) while every index it guarded is declared
``tokenize='porter unicode61'``. FTS matches on STEMS, so appending an "s" to a
probe produced a term whose stem still hit the secret token and whose literal
form was not a substring of the row -- the rule never fired and the result count
was an oracle again. The reviewer demonstrated it end to end.

These tests pin the two halves that fail in opposite directions:

  DROPS    a row whose match depended on text redaction removes, including
           through the stemmer and through prefix queries
  KEEPS    everything else, with stemming, prefixes and boolean syntax intact --
           a filter that dropped those would "close the oracle" by breaking
           search, which is the easy wrong fix

Every value here is SYNTHETIC.
"""

import pytest
from contextpulse_core.search_filter import keep_rows_matching_redacted_text

SECRET = "sk-zqfilterneedle0123456789ABCDEFGHIJ"
PORTER = "porter unicode61"


def rows_of(*texts):
    return [{"body": t} for t in texts]


def body(row):
    return row["body"]


class TestTheFixtureIsRedactable:
    def test_the_secret_really_is_removed_by_redaction(self):
        """Without this, every assertion below could pass vacuously."""
        from contextpulse_core.redact import redact_sensitive

        assert SECRET not in redact_sensitive(f"before {SECRET} after")


class TestRowsThatOnlyMatchedRedactedTextAreDropped:
    @pytest.mark.parametrize(
        "probe",
        [
            "zqfilterneedle0123456789ABCDEFGHIJ",  # the literal token
            "zqfilterneedle*",                     # prefix probe
            "zqfilterneedles*",                    # the stemmer bypass (S-4)
            "zqfilterneedless*",
        ],
    )
    def test_a_probe_for_the_secret_returns_nothing(self, probe):
        rows = rows_of(f"control {SECRET}")
        assert keep_rows_matching_redacted_text(probe, rows, body, tokenize=PORTER) == []

    def test_only_the_secret_row_is_dropped(self):
        rows = rows_of(f"alpha {SECRET}", "alpha zqfilterneedle-lookalike-note")
        kept = keep_rows_matching_redacted_text(
            "zqfilterneedle*", rows, body, tokenize=PORTER
        )
        assert [r["body"] for r in kept] == ["alpha zqfilterneedle-lookalike-note"], (
            "the filter is dropping everything, not just the redacted match"
        )


class TestOrdinarySearchStillWorks:
    def test_porter_stemming_survives(self):
        rows = rows_of("running the importer")
        assert keep_rows_matching_redacted_text("run", rows, body, tokenize=PORTER) == rows

    def test_prefix_queries_survive(self):
        # "check*" and not "deploy*": under a porter tokenizer the QUERY term is
        # stemmed too, so "deploy" becomes "deploi" and never prefix-matches the
        # stored "deploy" from "deployment". That is how the real events_fts
        # behaves, and the shadow reproduces it because it is the same
        # tokenizer -- which is the point. Asserting otherwise would have been
        # asserting a property of FTS5 that is not true.
        rows = rows_of("deployment checklist")
        assert keep_rows_matching_redacted_text("check*", rows, body, tokenize=PORTER) == rows

    def test_boolean_syntax_survives(self):
        rows = rows_of("alpha and beta", "alpha only")
        kept = keep_rows_matching_redacted_text("alpha AND beta", rows, body, tokenize=PORTER)
        assert [r["body"] for r in kept] == ["alpha and beta"]

    def test_row_order_is_preserved(self):
        rows = rows_of("alpha one", "alpha two", "alpha three")
        kept = keep_rows_matching_redacted_text("alpha", rows, body, tokenize=PORTER)
        assert [r["body"] for r in kept] == [r["body"] for r in rows]

    def test_a_non_stemming_tokenizer_is_honoured(self):
        """activity_fts uses the default tokenizer, with no porter stage."""
        rows = rows_of("running the importer")
        assert keep_rows_matching_redacted_text("run", rows, body, tokenize="unicode61") == [], (
            "unicode61 does not stem; matching 'run' to 'running' means the "
            "shadow index is not using the tokenizer it was given"
        )


class TestTheSubstringPath:
    """tokenize=None is the LIKE fallback, whose semantics are a substring."""

    def test_a_substring_of_redacted_text_is_kept(self):
        rows = rows_of("Quarterly Report Draft")
        assert keep_rows_matching_redacted_text("report dra", rows, body) == rows

    def test_a_substring_of_the_secret_is_dropped(self):
        rows = rows_of(f"control {SECRET}")
        assert keep_rows_matching_redacted_text("zqfilterneedle0123", rows, body) == []


class TestFailureModes:
    def test_an_unparseable_query_falls_back_to_the_substring_test(self):
        """Fail closed: an FTS query SQLite rejects must not keep everything."""
        rows = rows_of(f"control {SECRET}")
        assert keep_rows_matching_redacted_text(
            'zqfilterneedle"', rows, body, tokenize=PORTER
        ) == []

    def test_an_unknown_tokenizer_raises_rather_than_being_interpolated(self):
        with pytest.raises(ValueError):
            keep_rows_matching_redacted_text(
                "x", rows_of("x"), body, tokenize="unicode61'); DROP TABLE shadow--"
            )

    @pytest.mark.parametrize("query", ["", "   "])
    def test_an_empty_query_changes_nothing(self, query):
        rows = rows_of("alpha")
        assert keep_rows_matching_redacted_text(query, rows, body, tokenize=PORTER) == rows

    def test_no_rows_is_not_an_error(self):
        assert keep_rows_matching_redacted_text("alpha", [], body, tokenize=PORTER) == []

    def test_a_missing_text_field_is_not_an_error(self):
        rows = [{"body": None}]
        assert keep_rows_matching_redacted_text("alpha", rows, body, tokenize=PORTER) == []
