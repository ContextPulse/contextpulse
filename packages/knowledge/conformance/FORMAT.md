<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Conformance vector format — `cp-conformance/1`

Files: `packages/knowledge/conformance/vectors/cv-XXX-<slug>.json`, one vector
per file. Harness (per language): load vector -> build fresh store -> apply
`given` -> run `ingest` steps in order -> assert `expect`. The Python harness is
`tests/test_conformance.py`; the future Rust harness consumes THE SAME files. No
Python types, no pickle, ASCII-only, ints for all timestamps (ms).

```json
{
  "format": "cp-conformance/1",
  "api_surface": "cp_core.v1",
  "vector_id": "cv-001-...",
  "description": "...",
  "given": {
    "now": 0,
    "config": { "session_gap_ms": 900000, "corroboration_factor": 0.25,
                "confidence_cap": 0.99, "projects": [ {"name": "...", "aliases": ["..."]} ] },
    "pre_entities": [], "pre_aliases": [], "pre_facts": []
  },
  "ingest": [
    { "step": "observe", "observation": { "...Observation fields...": 0, "embedding": [] } },
    { "step": "flush",   "now": 0, "force": false },
    { "step": "correct_fact", "select": {"subject_id": "...", "predicate": "...",
        "object_value": "optional disambiguator", "valid_from": 0 },
      "new_object_value": "...", "asserted_at": 0 },
    { "step": "purge_observation", "source_event_id": "..." , "now": 0 }
  ],
  "expect": {
    "entities":   [ { "id": "...", "type": "..." } ],
    "entity_count": {"type": "project", "count": 1},
    "observation_count": 2,
    "facts": [
      { "$id": "label",  "subject_id": "...", "predicate": "...",
        "object_value": "...", "valid_from": 0, "valid_to": null,
        "retracted_at": null, "superseded_by": {"$ref": "other-label"},
        "confidence": 0.95, "extraction": "deterministic",
        "provenance_source_event_ids": ["e1", "e2"] }
    ],
    "fact_count": 3,
    "purge_log": [ {"item_kind": "observation"}, {"item_kind": "fact"} ],
    "queries": [
      { "call": "facts_about", "args": { "subject": "...", "at": 0 },
        "expect_facts": [ { "...partial matchers, ordered...": 0 } ] },
      { "call": "context_at", "args": { "t": 0 },
        "expect": { "session_id": "...", "project": null, "apps": [] } },
      { "call": "timeline", "args": {}, "expect_facts": [] },
      { "call": "search", "args": { "query": "...", "k": 5, "mode": "hybrid",
          "query_embedding": [1,0,0,0] },
        "expect_hits_ordered": [ {"source_event_id": "..."} ] }
    ]
  }
}
```

## Matching semantics (normative)

1. Expected fact objects are **partial matchers** — only listed keys are
   compared; unlisted keys are unconstrained. `fact_count` / `entity_count` /
   `observation_count`, when present, pin totals.
2. `expect.facts` matches **as a set** (order-free, each matcher must match
   exactly one distinct row); `expect_facts` inside queries matches **ordered**
   (the API defines total order).
3. Floats compare with tolerance `1e-6`. `null` means SQL NULL. Timestamps exact.
4. `"$id"` / `"$ref"` let one expected fact reference another (e.g.
   `superseded_by`) without hardcoding hash ids. Harness resolves after matching.
5. `provenance_source_event_ids` matches the set of `source_event_id`s reachable
   via `fact_provenance` -> `observations` (observation integer ids are
   implementation-internal and never appear in vectors).
6. `observation.embedding` (optional, any dim) is a **test injection**: the
   harness stores it as the observation's single vector verbatim, **bypassing
   BOTH the `embed_min_chars` gate AND the chunker** (m8) and skipping the model.
   `search.args.query_embedding` likewise. This makes cv-010 model-independent —
   it tests cp_core ranking (cosine + RRF), not MiniLM. An implementer who routes
   injection through the normal embedding queue is WRONG and gets an empty vector
   leg on short content.
7. Steps execute strictly in order; `flush` with `"force": true` closes the open
   session unconditionally at `open_session_last` (+1 if zero-width).
8. Unless overridden, queries run with `as_of = max(now over all steps)`.
9. An `observe` step whose `source_event_id` already exists in the store MUST be
   skipped entirely per the C2 rule (§1.6) — the harness routes every observe
   through the adapter's normal ingest entry point, never a raw insert.
10. `correct_fact.select` must resolve to exactly ONE non-retracted fact
    (subject_id + predicate + optional `object_value` / `valid_from` filters);
    anything else is a harness error (fail loud).

**Event-type discipline:** vectors use ONLY event kinds with a verified live
emitter: `ocr_result`, `typing_burst`, `transcription`, `clipboard_change`,
`session_lock`, `session_unlock`, `correction_detected`.
