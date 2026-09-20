# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Keep only the rows a search would have returned from REDACTED text.

WHY A SEARCH NEEDS THIS AT ALL. Redacting a tool's OUTPUT does not close a
query oracle. A client issues "sk-", "sk-a", "sk-ab" ... and reads the RESULT
COUNT to recover a pre-fix secret one character at a time, while every response
it sees is correctly redacted. An FTS index has the same shape at token
granularity -- one query per candidate token instead of per character. So no
count and no match may be computed against raw stored text.

WHY A SUBSTRING TEST IS NOT ENOUGH. The first version of this filter dropped a
row when a query term appeared in its raw text and not in its redacted text --
a literal containment test. `events_fts` and the memory indexes are declared
``tokenize='porter unicode61'``, so they match on STEMS: appending an "s" to a
probe gives a term whose stem still hits the secret token while its literal
form is not a substring of the row, so the rule never fired. The reviewer
demonstrated the bypass end to end (review S-4).

THE FIX IS TO ASK THE SAME QUESTION OF THE REDACTED TEXT. A throwaway in-memory
FTS5 index is built over the redacted rendering of the candidate rows, using the
SAME tokenizer as the index that produced them, and the caller's query is
re-run against it verbatim. The query parser, the stemmer and the prefix rules
are therefore identical to the ones that produced the hit, so:

  * a row that matched only because of text redaction removes is dropped, and
  * stemmed, prefixed and boolean queries keep working on everything else --
    which a "every term must appear literally in the redacted text" rule would
    have quietly broken.

The candidate set is the search's own result page (tens of rows), so the
throwaway index costs microseconds and is discarded with the connection.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable

from contextpulse_core.redact import redact_sensitive

logger = logging.getLogger(__name__)

# Tokenizers this module is allowed to name in DDL. The value is interpolated
# into a CREATE VIRTUAL TABLE, so it is checked against a fixed set rather than
# trusted -- every caller passes a literal, and an allowlist keeps it that way.
_ALLOWED_TOKENIZERS = frozenset({"porter unicode61", "unicode61", "ascii", "trigram"})


def _rowids_matching_redacted(
    query: str, texts: list[str], tokenize: str
) -> set[int] | None:
    """Re-run *query* against a throwaway FTS index over *texts*.

    Returns the set of matching indices, or None if SQLite could not parse the
    query at all -- in which case the caller falls back to the substring test
    rather than silently keeping everything.
    """
    if tokenize not in _ALLOWED_TOKENIZERS:
        raise ValueError(f"unsupported tokenizer for the redacted shadow index: {tokenize!r}")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE VIRTUAL TABLE shadow USING fts5(body, tokenize='{tokenize}')")
        conn.executemany(
            "INSERT INTO shadow(rowid, body) VALUES (?, ?)",
            [(i + 1, text) for i, text in enumerate(texts)],
        )
        rows = conn.execute(
            "SELECT rowid FROM shadow WHERE shadow MATCH ?", (query,)
        ).fetchall()
        return {r[0] - 1 for r in rows}
    except sqlite3.OperationalError:
        logger.debug("redacted shadow index could not parse %r; using substring test", query)
        return None
    finally:
        conn.close()


def keep_rows_matching_redacted_text(
    query: str,
    rows: list[dict[str, Any]],
    text_of: Callable[[dict[str, Any]], str],
    tokenize: str | None = None,
) -> list[dict[str, Any]]:
    """Drop rows whose match depended on text that redaction removes.

    Args:
        query: the caller's query, verbatim -- not re-parsed here.
        rows:  the result page the real index returned.
        text_of: renders the searchable text of a row. Give it the SAME fields
            the real index covers, or the filter answers a different question
            from the one that produced the rows.
        tokenize: the real index's tokenizer, for the shadow index. ``None``
            means the rows came from a LIKE fallback, whose semantics are a
            literal case-insensitive substring, so that is what is applied.

    Row order is preserved: the real index already ranked them.
    """
    if not rows or not query or not query.strip():
        return rows

    redacted = [redact_sensitive(text_of(row) or "") for row in rows]

    if tokenize is not None:
        kept = _rowids_matching_redacted(query, redacted, tokenize)
        if kept is not None:
            return [row for i, row in enumerate(rows) if i in kept]

    needle = query.lower()
    if needle.strip():
        return [row for i, row in enumerate(rows) if needle in redacted[i].lower()]
    return rows
