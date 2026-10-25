# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Tests for contextpulse_core.recall_inject -- choosing facts to inject.

The fixtures are built to put a real competitor next to every expected match,
because a scorer tested only on facts it should pick cannot fail.
"""

from __future__ import annotations

import sqlite3

from contextpulse_core import recall_inject as ri

NOW = 1_800_000_000.0
DAY = 86400.0


def _f(i, entity, text, age_days=1.0, conf=0.9):
    return ri.Fact(i, entity, text, NOW - age_days * DAY, conf)


def _filler(start=1000, n=60):
    """Unrelated facts so document frequencies look like a real store."""
    return [_f(start + i, f"Topic{i}", f"routine note number {i} about weekly planning") for i in range(n)]


# ── matching ───────────────────────────────────────────────────────


def test_tokens_are_whole_words():
    assert "clear" not in ri.tokens("cleared the cache")
    assert ri.tokens("CLEAR renewal") == ["clear", "renewal"]


def test_clear_prompt_does_not_pick_cleared_facts():
    facts = _filler() + [
        _f(1, "Browser", "cleared the cache and cookies in Chrome"),
        _f(2, "Inbox", "Cleared 40 old notifications"),
        _f(3, "CLEAR", "CLEAR membership renewal is due in November at the airport kiosk"),
    ]
    picked = ri.score_facts("why did you forget my CLEAR renewal?", ri.Index(facts))
    assert [s.fact.id for s in picked] == [3]


def test_entity_named_in_prompt_is_a_hit_but_substring_is_not():
    facts = _filler() + [_f(1, "ContextPulse", "the distiller now runs as a bare call")]
    idx = ri.Index(facts)
    hit = ri.score_facts("how is ContextPulse doing on the distiller?", idx)
    assert [s.fact.id for s in hit] == [1] and hit[0].entity_hit
    assert ri.score_facts("what about the pulse", idx) == []
    assert ri.score_facts("contextpulsecloud billing", idx) == []


def test_common_words_alone_never_qualify():
    # "planning" and "weekly" are in every filler fact: no anchor, no pick.
    facts = _filler() + [_f(1, "Calendar", "weekly planning review moved to Friday")]
    assert ri.score_facts("weekly planning", ri.Index(facts)) == []


def test_rare_terms_anchor_a_fact_without_its_entity():
    facts = _filler() + [_f(1, "Home", "rooftop solar quote from Namaste expired on 10-01")]
    picked = ri.score_facts("did the namaste solar quote expire?", ri.Index(facts))
    assert [s.fact.id for s in picked] == [1]
    assert not picked[0].entity_hit


def test_low_confidence_facts_are_never_offered():
    facts = _filler() + [_f(1, "Namaste", "rooftop solar quote expired", conf=0.5)]
    assert ri.score_facts("namaste solar quote", ri.Index(facts)) == []


def test_min_score_is_tunable():
    facts = _filler() + [_f(1, "Home", "rooftop solar quote from Namaste expired")]
    idx = ri.Index(facts)
    assert ri.score_facts("namaste solar", idx, min_score=1.0)
    assert ri.score_facts("namaste solar", idx, min_score=50.0) == []


def test_one_shared_rare_word_is_not_enough_without_the_entity():
    """Replay finding: "CLEAR renewal" pulled an unrelated Workspace renewal fact."""
    facts = _filler() + [_f(1, "Google Workspace", "annual plan renewal is locked in")]
    idx = ri.Index(facts)
    assert ri.score_facts("why did you forget my CLEAR renewal?", idx) == []
    assert ri.score_facts("google workspace renewal", idx)  # the entity anchors it


def test_stopword_only_entity_is_not_matched():
    facts = _filler() + [_f(1, "The", "an odd entity name from the model")]
    assert ri.score_facts("what is the plan", ri.Index(facts)) == []


# ── the block: cap, dedupe, rendering ──────────────────────────────


def _many(n, chars=200):
    return [ri.Scored(_f(i, f"E{i}", "x" * chars), 10.0, True) for i in range(1, n + 1)]


def test_block_never_exceeds_the_token_cap():
    for cap in (200, 300, 600, 1500):
        block = ri.build_block(_many(40), now=NOW, token_cap=cap, max_facts=40)
        assert block is not None
        assert block.tokens <= cap, (cap, block.tokens)


def test_block_returns_none_when_nothing_fits_under_the_cap():
    assert ri.build_block(_many(3, chars=2000), now=NOW, token_cap=200) is None


def test_long_fact_is_skipped_not_allowed_to_crowd_out_short_ones():
    cands = [ri.Scored(_f(99, "Big", "y" * 5000), 20.0, True)] + _many(3, chars=50)
    block = ri.build_block(cands, now=NOW, token_cap=400)
    assert block is not None and block.fact_ids == [1, 2, 3]


def test_block_skips_facts_already_injected():
    block = ri.build_block(_many(5, chars=40), now=NOW, token_cap=1500, exclude_ids={1, 3})
    assert block is not None and block.fact_ids == [2, 4, 5]


def test_block_is_none_when_every_candidate_was_already_injected():
    assert ri.build_block(_many(2, chars=40), now=NOW, token_cap=1500, exclude_ids={1, 2}) is None


def test_block_respects_max_facts():
    block = ri.build_block(_many(10, chars=20), now=NOW, token_cap=5000, max_facts=4)
    assert block is not None and len(block.fact_ids) == 4


def test_each_fact_is_one_line_with_id_date_age_and_confidence():
    f = ri.Fact(42, "WezTerm", "Shift+Enter\nnow sends a newline", NOW - 3 * DAY, 0.85)
    line = ri.render_line(f, NOW)
    assert "\n" not in line
    assert line.startswith("- cp#42 [")
    assert "3d ago" in line and "conf 0.85" in line and "WezTerm:" in line


def test_block_header_labels_the_memory_unverified():
    block = ri.build_block(_many(1, chars=20), now=NOW, token_cap=1500)
    assert block is not None
    first = block.text.splitlines()[0]
    assert "UNVERIFIED" in first and "stale" in first


def test_rendered_line_scrubs_a_secret():
    f = ri.Fact(7, "AWS", "key AKIAIOSFODNN7EXAMPLE was rotated", NOW, 0.9)  # synthetic
    assert "AKIAIOSFODNN7EXAMPLE" not in ri.render_line(f, NOW)  # synthetic


# ── session-start selectors ────────────────────────────────────────


def test_recent_facts_window_and_confidence():
    facts = [
        _f(1, "A", "new and sure", age_days=1, conf=0.9),
        _f(2, "B", "new and unsure", age_days=1, conf=0.6),
        _f(3, "C", "old and sure", age_days=10, conf=0.95),
        _f(4, "D", "newest", age_days=0.1, conf=0.85),
    ]
    assert [s.fact.id for s in ri.recent_facts(facts, NOW)] == [4, 1]


def test_entity_facts_whole_word_newest_first():
    facts = [
        _f(1, "ContextPulse", "a", age_days=5),
        _f(2, "ContextPulse KG", "b", age_days=1),
        _f(3, "ContextPulseCloud", "c", age_days=0.5),
    ]
    assert [s.fact.id for s in ri.entity_facts(facts, "contextpulse")] == [2, 1]


def test_load_facts_reads_a_read_only_connection(tmp_path):
    db = tmp_path / "probe.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY, entity TEXT, fact TEXT, valid_from REAL, confidence REAL)"
    )
    conn.execute("INSERT INTO facts VALUES (1, 'A', 'b', NULL, NULL)")
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        assert ri.load_facts(ro) == [ri.Fact(1, "A", "b", None, 0.5)]
    finally:
        ro.close()
