# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""AT-3 — conformance harness. One parametrized test over all 14 vectors.

Loads each conformance/vectors/*.json, builds a FRESH in-memory store, applies
`given`, runs `ingest` steps in order through the store's NORMAL ingest entry
point (so the C2 skip rule is exercised), and asserts `expect` per the §5.1
matching semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from contextpulse_knowledge.cp_core import Observation
from contextpulse_knowledge.store_sqlite import KnowledgeStore, config_from_json

VECTORS_DIR = Path(__file__).resolve().parents[1] / "conformance" / "vectors"
VECTOR_FILES = sorted(VECTORS_DIR.glob("cv-*.json"))
TOL = 1e-6


def _vector_id(p: Path) -> str:
    return p.stem


# ---------------------------------------------------------------------------
# harness core
# ---------------------------------------------------------------------------


def _build_store(vector: dict) -> KnowledgeStore:
    given = vector.get("given", {})
    config = config_from_json(given.get("config", {}))
    store = KnowledgeStore(":memory:", config=config)
    # pre_entities / pre_aliases / pre_facts are supported but unused by seed set.
    for e in given.get("pre_entities", []):
        store.conn.execute(
            "INSERT OR IGNORE INTO entities(id, type, canonical_name, created_at, meta) "
            "VALUES(?,?,?,?, '{}')",
            (e["id"], e["type"], e.get("canonical_name", e["id"]), e.get("created_at", 0)),
        )
    store.conn.commit()
    return store


def _make_observation(od: dict) -> Observation:
    return Observation(
        source=od["source"],
        source_event_id=od["source_event_id"],
        kind=od["kind"],
        observed_at=od["observed_at"],
        app=od.get("app", ""),
        window_title=od.get("window_title", ""),
        url=od.get("url"),
        content=od.get("content"),
        media_ref=od.get("media_ref"),
        meta=od.get("meta", {}),
    )


def _run_ingest(store: KnowledgeStore, vector: dict) -> int:
    """Execute steps in order; return max(now) seen across steps for default as_of."""
    max_now = vector.get("given", {}).get("now", 0)
    for step in vector.get("ingest", []):
        kind = step["step"]
        if kind == "observe":
            od = step["observation"]
            obs = _make_observation(od)
            ingested = store.observe(obs, now=od.get("observed_at"))
            max_now = max(max_now, obs.observed_at)
            # test injection (bypasses chunker + min-chars) — only if ingested
            if ingested and "embedding" in od and od["embedding"]:
                store.inject_observation_vector(obs.source_event_id, od["embedding"])
        elif kind == "flush":
            now = step["now"]
            store.flush(now, force=step.get("force", False))
            max_now = max(max_now, now)
        elif kind == "correct_fact":
            sel = step["select"]
            store.correct_fact(
                subject_id=sel["subject_id"],
                predicate=sel["predicate"],
                object_value=sel.get("object_value"),
                valid_from=sel.get("valid_from"),
                new_object_value=step.get("new_object_value"),
                new_object_entity_id=step.get("new_object_entity_id"),
                asserted_at=step["asserted_at"],
            )
            max_now = max(max_now, step["asserted_at"])
        elif kind == "purge_observation":
            store.purge_observation(step["source_event_id"], step["now"])
            max_now = max(max_now, step["now"])
        else:  # pragma: no cover
            raise AssertionError(f"unknown ingest step: {kind}")
    return max_now


# ---------------------------------------------------------------------------
# fact fetching + matching
# ---------------------------------------------------------------------------


def _all_facts(store: KnowledgeStore) -> list[dict]:
    rows = store.conn.execute(
        "SELECT id, subject_id, predicate, object_entity_id, object_value, valid_from, "
        "valid_to, asserted_at, retracted_at, superseded_by, confidence, extraction "
        "FROM facts"
    ).fetchall()
    facts = []
    for r in rows:
        d = dict(r)
        d["provenance_source_event_ids"] = _prov_for(store, r["id"])
        facts.append(d)
    return facts


def _prov_for(store: KnowledgeStore, fact_id: str) -> list[str]:
    rows = store.conn.execute(
        "SELECT o.source_event_id FROM fact_provenance p "
        "JOIN observations o ON o.id = p.observation_id WHERE p.fact_id=? "
        "ORDER BY o.source_event_id ASC",
        (fact_id,),
    ).fetchall()
    return [r["source_event_id"] for r in rows]


_COMPARABLE_KEYS = {
    "subject_id",
    "predicate",
    "object_entity_id",
    "object_value",
    "valid_from",
    "valid_to",
    "asserted_at",
    "retracted_at",
    "superseded_by",
    "confidence",
    "extraction",
}


def _matcher_matches(matcher: dict, fact: dict, ref_resolution: dict) -> bool:
    for key, expected in matcher.items():
        if key in ("$id", "$ref"):
            continue
        if key == "provenance_source_event_ids":
            if sorted(fact["provenance_source_event_ids"]) != sorted(expected):
                return False
            continue
        if key not in _COMPARABLE_KEYS:
            continue
        actual = fact.get(key)
        if isinstance(expected, dict) and "$ref" in expected:
            # superseded_by referencing another labeled fact
            target_id = ref_resolution.get(expected["$ref"])
            if actual != target_id:
                return False
            continue
        if key == "confidence":
            if actual is None or abs(float(actual) - float(expected)) > TOL:
                return False
            continue
        if actual != expected:
            return False
    return True


def _match_fact_set(expected_facts: list[dict], actual_facts: list[dict]) -> None:
    """Set match with $id/$ref resolution (two-pass)."""
    # Pass 1: resolve non-$ref matchers to actual fact ids
    ref_resolution: dict[str, str] = {}
    remaining = list(actual_facts)
    # Order matchers so that ones without $ref-typed values resolve first is not
    # strictly necessary; we do a fixpoint over resolution.
    unresolved = list(expected_facts)
    passes = 0
    while unresolved and passes <= len(expected_facts) + 1:
        passes += 1
        progressed = False
        still: list[dict] = []
        for matcher in unresolved:
            # can we match now? (any $ref must already be resolved)
            refs_needed = [
                v["$ref"] for v in matcher.values() if isinstance(v, dict) and "$ref" in v
            ]
            if any(r not in ref_resolution for r in refs_needed):
                still.append(matcher)
                continue
            match_idx = None
            for i, f in enumerate(remaining):
                if _matcher_matches(matcher, f, ref_resolution):
                    match_idx = i
                    break
            assert match_idx is not None, (
                f"no actual fact matches expected matcher {matcher}; "
                f"remaining actual facts: {remaining}"
            )
            matched = remaining.pop(match_idx)
            if "$id" in matcher:
                ref_resolution[matcher["$id"]] = matched["id"]
            progressed = True
        unresolved = still
        if not progressed and unresolved:
            # try one more pass resolving $ids that need refs still pending —
            # break to fail loudly below
            break
    assert not unresolved, f"could not resolve fact matchers (refs?): {unresolved}"


# ---------------------------------------------------------------------------
# query execution + assertion
# ---------------------------------------------------------------------------


def _fact_to_dict(f) -> dict:
    return {
        "id": f.id,
        "subject_id": f.subject_id,
        "predicate": f.predicate,
        "object_entity_id": f.object_entity_id,
        "object_value": f.object_value,
        "valid_from": f.valid_from,
        "valid_to": f.valid_to,
        "asserted_at": f.asserted_at,
        "retracted_at": f.retracted_at,
        "superseded_by": f.superseded_by,
        "confidence": f.confidence,
        "extraction": f.extraction,
        "provenance_source_event_ids": [],
    }


def _assert_ordered_facts(expected: list[dict], actual: list[dict]) -> None:
    assert len(expected) == len(actual), (
        f"ordered fact count mismatch: expected {len(expected)}, got "
        f"{[a.get('object_value') or a.get('predicate') for a in actual]}"
    )
    for matcher, fact in zip(expected, actual):
        assert _matcher_matches(matcher, fact, {}), (
            f"ordered fact mismatch: matcher {matcher} vs fact {fact}"
        )


def _run_query(store: KnowledgeStore, q: dict, default_as_of: int) -> None:
    call = q["call"]
    args = dict(q.get("args", {}))
    if (
        call in ("facts_about", "timeline")
        and "as_of" not in args
        and "include_retracted" not in args
    ):
        args.setdefault("as_of", default_as_of)
    if call == "context_at" and "as_of" not in args:
        args.setdefault("as_of", default_as_of)

    if call == "facts_about":
        result = store.facts_about(args.pop("subject"), **args)
        actual = [_fact_to_dict(f) for f in result]
        _assert_ordered_facts(q["expect_facts"], actual)
    elif call == "timeline":
        result = store.timeline(args.pop("subject"), **args)
        actual = [_fact_to_dict(f) for f in result]
        _assert_ordered_facts(q["expect_facts"], actual)
    elif call == "context_at":
        snap = store.context_at(args.pop("t"), **args)
        expect = q["expect"]
        if "session_id" in expect:
            assert snap["session_id"] == expect["session_id"], (
                f"context_at session_id {snap['session_id']} != {expect['session_id']}"
            )
        if "project" in expect:
            exp_proj = expect["project"]
            if exp_proj is None:
                assert snap["project"] is None, f"expected no project, got {snap['project']}"
            else:
                assert snap["project"] is not None
                assert _matcher_matches(exp_proj, _fact_to_dict(snap["project"]), {})
        if "apps" in expect:
            actual_apps = [_fact_to_dict(f) for f in snap["apps"]]
            _assert_ordered_facts(expect["apps"], actual_apps)
    elif call == "search":
        hits = store.search(args.pop("query"), **args)
        expected_hits = q["expect_hits_ordered"]
        assert len(hits) == len(expected_hits), (
            f"search hit count mismatch: expected {len(expected_hits)}, "
            f"got {[h['source_event_id'] for h in hits]}"
        )
        for exp, hit in zip(expected_hits, hits):
            assert hit["source_event_id"] == exp["source_event_id"], (
                f"search order mismatch: {hit['source_event_id']} != {exp['source_event_id']}"
            )
    else:  # pragma: no cover
        raise AssertionError(f"unknown query call: {call}")


# ---------------------------------------------------------------------------
# the parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vector_path", VECTOR_FILES, ids=[_vector_id(p) for p in VECTOR_FILES])
def test_conformance_vector(vector_path: Path) -> None:
    vector = json.loads(vector_path.read_text(encoding="utf-8"))
    assert vector["format"] == "cp-conformance/1"
    store = _build_store(vector)
    try:
        default_as_of = _run_ingest(store, vector)
        expect = vector.get("expect", {})

        # entity_count
        if "entity_count" in expect:
            ec = expect["entity_count"]
            n = store.conn.execute(
                "SELECT COUNT(*) FROM entities WHERE type=?", (ec["type"],)
            ).fetchone()[0]
            assert n == ec["count"], f"entity_count[{ec['type']}] {n} != {ec['count']}"

        # entities present
        for e in expect.get("entities", []):
            row = store.conn.execute("SELECT type FROM entities WHERE id=?", (e["id"],)).fetchone()
            assert row is not None, f"missing entity {e['id']}"
            if "type" in e:
                assert row["type"] == e["type"]

        # observation_count
        if "observation_count" in expect:
            n = store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            assert n == expect["observation_count"], (
                f"observation_count {n} != {expect['observation_count']}"
            )

        # fact_count
        if "fact_count" in expect:
            n = store.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            assert n == expect["fact_count"], f"fact_count {n} != {expect['fact_count']}"

        # facts (set match)
        if "facts" in expect:
            _match_fact_set(expect["facts"], _all_facts(store))

        # purge_log (set match on item_kind + strictly increasing tombstone_seq)
        if "purge_log" in expect:
            rows = store.conn.execute(
                "SELECT tombstone_seq, item_kind FROM purge_log ORDER BY tombstone_seq ASC"
            ).fetchall()
            assert len(rows) == len(expect["purge_log"]), (
                f"purge_log count {len(rows)} != {len(expect['purge_log'])}"
            )
            seqs = [r["tombstone_seq"] for r in rows]
            assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), (
                "tombstone_seq must be strictly increasing"
            )
            from collections import Counter

            got = Counter(r["item_kind"] for r in rows)
            want = Counter(pl["item_kind"] for pl in expect["purge_log"])
            assert got == want, f"purge_log kinds {got} != {want}"

        # queries
        for q in expect.get("queries", []):
            _run_query(store, q, default_as_of)
    finally:
        store.close()
