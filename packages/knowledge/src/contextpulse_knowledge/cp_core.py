# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""cp_core — the PURE-LOGIC referee for the ContextPulse knowledge graph (Phase 1).

This module is the semantic contract. The SQLite adapter (``store_sqlite``), the
conformance harness, and any future Rust crate all defer to the functions here.

PURITY (AT-0, enforced by ``tests/test_purity.py``): permitted imports are ONLY
``dataclasses``, ``typing``, ``enum``, ``json``, ``hashlib``, ``math``, ``re``,
``unicodedata``, ``itertools``, ``bisect``. FORBIDDEN: ``sqlite3``, ``os``,
``pathlib``, ``time``, ``datetime``, ``numpy``, ``onnxruntime``, any
``contextpulse_*`` import, any I/O. "now" is ALWAYS an explicit parameter.

Functional core / imperative shell: cp_core never touches storage. The adapter
(1) fetches the narrow context cp_core declares it needs, (2) calls a pure
planning function, (3) applies the returned ChangeSet transactionally.

Divergence ledger (D1-D8) and BD-1 live in ``migrate.py`` / ``schema.sql``.
BD-1 (short-alias word-boundary rule) is implemented in :func:`resolve_entity`.

C-2 RESOLUTION (authoritative — resolves the v2 dossier m4 vs §3.1 contradiction):
For ``session.active_project`` under an open session, a re-derived candidate whose
project == the currently-open project pins ``valid_from = session_start`` (m4's
intent: same deterministic fact id => rule-1 fusion corroborates the open era). A
candidate for a DIFFERENT project is a genuine mid-session transition and pins
``valid_from = observed_at``, so rule 2's strict ``candidate.valid_from >
old.valid_from`` fires: the old interval closes at the switch time and the new one
opens. Pinning the switch to ``session_start`` instead would build a zero-width
``[x, x)`` era (violating §1.2 "never zero-width") and crash the schema CHECK — this
resolution makes §3.1's promised rule-2 supersession reachable. Implemented in
:func:`_plan_project`; the open fact's ``valid_from`` is carried in
``IngestState.open_project_valid_from`` so :func:`_close_session` can address it.
Pinned by cv-016.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# 1.1 Value objects (all frozen; all timestamps epoch-ms int)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectDef:
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Observation:
    source: str
    source_event_id: str
    kind: str
    observed_at: int
    app: str = ""
    window_title: str = ""
    url: Optional[str] = None
    content: Optional[str] = None
    media_ref: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    canonical_name: str
    created_at: int
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Fact:
    id: str
    subject_id: str
    predicate: str
    object_entity_id: Optional[str]
    object_value: Optional[str]
    valid_from: int
    valid_to: Optional[int]
    asserted_at: int
    retracted_at: Optional[int]
    superseded_by: Optional[str]
    confidence: float
    extraction: str
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IngestConfig:
    session_gap_ms: int = 900_000
    corroboration_factor: float = 0.25
    confidence_cap: float = 0.99
    projects: tuple[ProjectDef, ...] = ()
    embed_min_chars: int = 40
    chunk_chars: int = 700
    chunk_overlap: int = 80


@dataclass(frozen=True)
class IngestState:
    open_session_start: Optional[int] = None
    open_session_last: Optional[int] = None
    open_app_first_seen: tuple[tuple[str, int], ...] = ()
    open_app_last_seen: tuple[tuple[str, int], ...] = ()
    open_project_id: Optional[str] = None  # entity id of current active project (in open session)
    # valid_from of the CURRENTLY-open active_project fact. Equals session_start for the
    # first project of a session; equals the switch's observed_at after a mid-session
    # project change (C-2). _close_session needs this to compute the open fact's id.
    open_project_valid_from: Optional[int] = None


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    canonical_name: str
    is_new: bool


# --- context the adapter fetches for planning -------------------------------


@dataclass(frozen=True)
class ExistingFacts:
    """Non-retracted facts for the fusion keys, ACROSS partitions.

    Keyed by (subject_id, predicate). Rule 0 needs the user partition; rules
    1-3 filter to the candidate's own partition.
    """

    by_key: dict[tuple[str, str], tuple[Fact, ...]] = field(default_factory=dict)

    def get(self, subject_id: str, predicate: str) -> tuple[Fact, ...]:
        return self.by_key.get((subject_id, predicate), ())


# ---------------------------------------------------------------------------
# ChangeSet ops (closed vocabulary)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsertObservation:
    obs: Observation
    session_id: Optional[str]
    late: bool = False


@dataclass(frozen=True)
class InsertEntity:
    entity: Entity


@dataclass(frozen=True)
class InsertAlias:
    entity_id: str
    alias: str  # casefolded
    source: str
    created_at: int


@dataclass(frozen=True)
class InsertFact:
    fact: Fact


@dataclass(frozen=True)
class AddProvenance:
    fact_id: str
    source_event_id: str


@dataclass(frozen=True)
class CloseValidity:
    fact_id: str
    valid_to: int


@dataclass(frozen=True)
class UpdateConfidence:
    fact_id: str
    confidence: float


@dataclass(frozen=True)
class RetractFact:
    fact_id: str
    retracted_at: int
    superseded_by: Optional[str]


@dataclass(frozen=True)
class InsertCorrection:
    original: str
    corrected: str
    detected_at: int
    source: str
    source_event_id: str


@dataclass(frozen=True)
class PurgeObservation:
    source_event_id: str


@dataclass(frozen=True)
class PurgeFact:
    fact_id: str


@dataclass(frozen=True)
class AppendPurgeLog:
    item_kind: str
    item_id: str
    purged_at: int


@dataclass(frozen=True)
class SetState:
    state: IngestState


Op = object  # closed vocabulary above; typing convenience


@dataclass(frozen=True)
class ChangeSet:
    ops: tuple = ()
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 1.3 Identity — deterministic IDs
# ---------------------------------------------------------------------------

_SLUG_DROP = re.compile(r"[^a-z0-9._-]+")
_SLUG_DASHES = re.compile(r"-{2,}")
_WS = re.compile(r"\s+")


def slugify(s: str) -> str:
    """NFKC-normalize, casefold, trim, collapse internal whitespace to '-',
    DROP chars outside [a-z0-9._-] (§1.3 — M-2: drop, do NOT replace with '-'),
    collapse repeated '-', strip leading/trailing '-'.

    M-2 (identity layer, Rust-port byte-equality): ``slugify("notepad++") == "notepad"``
    (not ``"notepad-"``). Dropping keeps punctuation-bearing surfaces from minting ids
    with dangling dashes.
    """
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = s.strip()
    s = _WS.sub("-", s)
    s = _SLUG_DROP.sub("", s)  # DROP disallowed chars (M-2), not replace with '-'
    s = _SLUG_DASHES.sub("-", s)
    s = s.strip("-")
    return s


def entity_id(type_: str, surface: str) -> str:
    if type_ == "app":
        surf = surface
        if surf.lower().endswith(".exe"):
            surf = surf[: -len(".exe")]
        return f"app:{slugify(surf)}"
    if type_ == "session":
        # surface is the start_ms (int-like)
        return f"session:{surface}"
    # vocab / project / others
    return f"{type_}:{slugify(surface)}"


def fact_id(
    subject_id: str,
    predicate: str,
    object_entity_id: Optional[str],
    object_value: Optional[str],
    valid_from: int,
    extraction: str,
) -> str:
    payload = json.dumps(
        [
            subject_id,
            predicate,
            object_entity_id or "",
            object_value or "",
            valid_from,
            extraction,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]
    return f"f_{digest}"


# ---------------------------------------------------------------------------
# 1.4 Entity resolution (Tier-0, deterministic)
# ---------------------------------------------------------------------------


def _alias_matches_title(alias: str, title: str) -> bool:
    """BD-1: aliases/names of length <= 3 match on WORD BOUNDARIES; longer keep
    live case-insensitive containment."""
    if not alias:
        return False
    if len(alias) <= 3:
        pat = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
        return re.search(pat, title, re.IGNORECASE) is not None
    return alias.casefold() in title.casefold()


def _matched_project_token(title: str, proj_id: str, config: IngestConfig) -> Optional[str]:
    """The first config token (name/alias) of the project ``proj_id`` that matches
    ``title`` under BD-1. Used to record the rule-3 alias (§1.4 minor)."""
    for proj in config.projects:
        if entity_id("project", proj.name) != proj_id:
            continue
        for cand in (proj.name,) + tuple(proj.aliases):
            if _alias_matches_title(cand, title):
                return cand
    return None


def resolve_entity(
    type_: str,
    surface: str,
    aliases: Mapping[str, str],
    config: IngestConfig,
) -> ResolvedEntity:
    """Pure resolution over an adapter-provided snapshot.

    Order: 1) exact canonical id match  2) case-insensitive alias table match
           3) for type='project': window-title match against config.projects
              (BD-1 short-token guard)  4) mint new entity.

    ``aliases`` maps casefolded alias -> entity_id for the relevant type.
    """
    if type_ == "session":
        eid = entity_id("session", surface)
        return ResolvedEntity(eid, surface, True)

    if type_ == "app":
        eid = entity_id("app", surface)
        canon = surface[:-4] if surface.lower().endswith(".exe") else surface
        return ResolvedEntity(eid, canon, True)

    if type_ == "project":
        # rule 3: window-title match. `surface` is the window title here.
        # An alias-table hit is a rule-2 resolution (already recorded); a config-token
        # match with NO alias-table entry is a fresh rule-3 resolution (is_new=True) that
        # the caller pins with an InsertAlias so the next hit is rule 2 (§1.4 minor).
        for proj in config.projects:
            candidates = (proj.name,) + tuple(proj.aliases)
            for cand in candidates:
                if _alias_matches_title(cand, surface):
                    eid = entity_id("project", proj.name)
                    is_new = aliases.get(cand.casefold()) != eid
                    return ResolvedEntity(eid, proj.name, is_new)
        return ResolvedEntity("", "", False)  # no match => caller emits nothing

    # vocab and other keyed types: surface is the exact key
    eid = entity_id(type_, surface)
    # alias-table hit (case-insensitive)
    hit = aliases.get(surface.casefold())
    if hit:
        return ResolvedEntity(hit, surface, False)
    return ResolvedEntity(eid, surface, True)


# ---------------------------------------------------------------------------
# 1.5 Predicate registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PredicateSpec:
    predicate: str
    subject_type: str
    object_kind: str  # 'none' | 'entity' | 'value'
    cardinality: str  # 'single' | 'multi'
    base_confidence: float
    presence: bool = False


PREDICATE_REGISTRY: dict[str, PredicateSpec] = {
    "session.occurred": PredicateSpec(
        "session.occurred", "session", "none", "single", 1.00, presence=True
    ),
    "session.active_project": PredicateSpec(
        "session.active_project", "session", "entity", "single", 0.85
    ),
    "session.used_app": PredicateSpec("session.used_app", "session", "entity", "multi", 0.95),
    "vocab.corrects_to": PredicateSpec("vocab.corrects_to", "vocab", "value", "single", 0.90),
    # DEFERRED (M7) — registry rows reserved, no Phase-1 writer:
    "session.visited_url": PredicateSpec("session.visited_url", "session", "entity", "multi", 0.95),
    "session.visited_domain": PredicateSpec(
        "session.visited_domain", "session", "entity", "multi", 0.95
    ),
    "person.name_variant": PredicateSpec("person.name_variant", "person", "value", "multi", 0.70),
}

CONTEXT_PREDICATES = frozenset(
    {
        "session.occurred",
        "session.active_project",
        "session.used_app",
        "session.visited_url",
        "session.visited_domain",
    }
)

# Qualifying live event kinds (M8).
QUALIFYING_KINDS = frozenset(
    {
        "ocr_result",
        "transcription",
        "clipboard_change",
        "typing_burst",
        "session_lock",
        "session_unlock",
        "correction_detected",
        "vocab_import",
    }
)


def fuse(c: float, config: IngestConfig) -> float:
    """Corroboration fusion — non-decreasing by construction, capped, identity
    at cap (M4)."""
    cap = config.confidence_cap
    if c >= cap:
        return c
    return min(cap, c + (1.0 - c) * config.corroboration_factor)


# ---------------------------------------------------------------------------
# 1.6 Ingestion contract (pure pipeline)
# ---------------------------------------------------------------------------

_USER = "user"
_DET = "deterministic"


def _instantaneous_valid_to(valid_from: int, last: int) -> int:
    """valid_to on close = last-seen ms + 1 if it equals valid_from, else last."""
    return last + 1 if last == valid_from else last


def derive_fusion_keys(
    obs: Observation, state: IngestState, config: IngestConfig
) -> list[tuple[str, str]]:
    """(subject_id, predicate) pairs whose existing non-retracted facts the
    adapter must fetch for this observation's planning."""
    keys: list[tuple[str, str]] = []
    session_id = _session_id_for(obs, state, config)

    # vocab (non-session-scoped)
    if _is_correction_kind(obs.kind):
        original = (obs.meta or {}).get("original_text") or ""
        if original:
            vocab_id = entity_id("vocab", original)
            keys.append((vocab_id, "vocab.corrects_to"))

    if session_id is not None:
        keys.append((session_id, "session.occurred"))
        keys.append((session_id, "session.used_app"))
        keys.append((session_id, "session.active_project"))
    return keys


def _is_correction_kind(kind: str) -> bool:
    return kind in ("correction_detected", "vocab_import")


def _session_id_for(obs: Observation, state: IngestState, config: IngestConfig) -> Optional[str]:
    """The session id this observation belongs to, WITHOUT mutating state.

    Returns None when the observation opens no session and joins none
    (session_lock, or a late observation before open_session_start).
    """
    if obs.kind == "session_lock":
        return None
    # late (before an open session's start) => joins no session
    if state.open_session_start is not None and obs.observed_at < state.open_session_start:
        return None
    # Decide whether this observation continues the open session or opens a new one.
    if state.open_session_start is None:
        return f"session:{obs.observed_at}"
    if state.open_session_last is None:
        # shouldn't happen when start is set, but be safe
        return f"session:{obs.observed_at}"
    if obs.observed_at - state.open_session_last >= config.session_gap_ms:
        return f"session:{obs.observed_at}"
    return f"session:{state.open_session_start}"


def _find(existing_list: Sequence[Fact], pred: str) -> list[Fact]:
    return [f for f in existing_list if f.predicate == pred]


def plan_ingest(
    obs: Observation,
    state: IngestState,
    existing: ExistingFacts,
    aliases: Mapping[str, str],
    config: IngestConfig,
    now: int,
) -> tuple[ChangeSet, IngestState]:
    ops: list = []  # close-of-prior-session ops (reference existing facts)
    open_ops: list = []  # session-open entity/fact/provenance (needs obs inserted first)
    notes: list[str] = []

    # ---- sessionization: does this observation close/open a session? --------
    is_late = (
        state.open_session_start is not None
        and obs.observed_at < state.open_session_start
        and obs.kind != "session_lock"
    )

    new_state = state
    session_id: Optional[str] = None
    # session_id stored on the observation ROW. Equals session_id for session-scoped
    # observations; for a lock it is the CLOSED session's id (minor, §1.6); None for late.
    obs_session_id: Optional[str] = None

    if obs.kind == "session_lock":
        # close open session (if any); open nothing
        # MINOR (§1.6): the lock observation carries the session_id of the session it
        # closes (not NULL). No session-scoped facts derive from a lock.
        obs_session_id = (
            f"session:{state.open_session_start}"
            if state.open_session_start is not None
            else None
        )
        close_ops, close_notes, new_state = _close_session(state, config)
        ops.extend(close_ops)
        notes.extend(close_notes)
        session_id = None
        obs_late = False
    elif is_late:
        # late observation joins no session; no session facts derive
        session_id = None
        obs_late = True
    else:
        # normal path: maybe close+open a new session
        need_new = False
        if state.open_session_start is None:
            need_new = True
        elif (
            obs.observed_at - (state.open_session_last or state.open_session_start)
            >= config.session_gap_ms
        ):
            need_new = True

        if need_new:
            close_ops, close_notes, closed_state = _close_session(state, config)
            ops.extend(close_ops)
            notes.extend(close_notes)
            session_id = f"session:{obs.observed_at}"
            # open new session entity + session.occurred fact (emitted AFTER obs)
            ent = Entity(
                id=session_id,
                type="session",
                canonical_name=session_id,
                created_at=now,
            )
            open_ops.append(InsertEntity(ent))
            occ = _make_fact(
                subject_id=session_id,
                predicate="session.occurred",
                object_entity_id=None,
                object_value=None,
                valid_from=obs.observed_at,
                valid_to=None,
                asserted_at=now,
                confidence=1.0,
                extraction=_DET,
            )
            open_ops.append(InsertFact(occ))
            open_ops.append(AddProvenance(occ.id, obs.source_event_id))
            new_state = IngestState(
                open_session_start=obs.observed_at,
                open_session_last=obs.observed_at,
                open_app_first_seen=(),
                open_app_last_seen=(),
                open_project_id=None,
            )
        else:
            session_id = f"session:{state.open_session_start}"
            new_state = state
        obs_late = False

    # non-lock observations store their own session_id (None for late) on the row.
    if obs.kind != "session_lock":
        obs_session_id = session_id

    # ---- record observation FIRST (so all provenance can reference it) ------
    ops.append(InsertObservation(obs=obs, session_id=obs_session_id, late=obs_late))
    ops.extend(open_ops)

    # ---- session-scoped extractors (only for non-late, non-lock obs) --------
    if session_id is not None:
        # update open_session_last + apps in state
        new_state = _bump_last(new_state, obs.observed_at)

        # apps extractor
        if obs.app:
            app_ops, app_notes, new_state = _plan_app(
                obs, session_id, new_state, existing, config, now
            )
            ops.extend(app_ops)
            notes.extend(app_notes)

        # project extractor
        proj_ops, proj_notes, new_state = _plan_project(
            obs, session_id, new_state, existing, aliases, config, now
        )
        ops.extend(proj_ops)
        notes.extend(proj_notes)

    # ---- vocab extractor (non-session-scoped; runs even if late) ------------
    if _is_correction_kind(obs.kind):
        v_ops, v_notes = _plan_vocab(obs, existing, config, now)
        ops.extend(v_ops)
        notes.extend(v_notes)

    return ChangeSet(ops=tuple(ops), notes=tuple(notes)), new_state


def _bump_last(state: IngestState, observed_at: int) -> IngestState:
    last = state.open_session_last
    if last is None or observed_at > last:
        return replace(state, open_session_last=observed_at)
    return state


def _make_fact(
    *,
    subject_id: str,
    predicate: str,
    object_entity_id: Optional[str],
    object_value: Optional[str],
    valid_from: int,
    valid_to: Optional[int],
    asserted_at: int,
    confidence: float,
    extraction: str,
    retracted_at: Optional[int] = None,
    superseded_by: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Fact:
    fid = fact_id(subject_id, predicate, object_entity_id, object_value, valid_from, extraction)
    return Fact(
        id=fid,
        subject_id=subject_id,
        predicate=predicate,
        object_entity_id=object_entity_id,
        object_value=object_value,
        valid_from=valid_from,
        valid_to=valid_to,
        asserted_at=asserted_at,
        retracted_at=retracted_at,
        superseded_by=superseded_by,
        confidence=confidence,
        extraction=extraction,
        meta=meta or {},
    )


def _close_session(state: IngestState, config: IngestConfig) -> tuple[list, list[str], IngestState]:
    """Close all open intervals for the open session. Returns (ops, notes, cleared_state)."""
    ops: list = []
    notes: list[str] = []
    if state.open_session_start is None:
        return ops, notes, state
    start = state.open_session_start
    last = state.open_session_last if state.open_session_last is not None else start
    session_id = f"session:{start}"

    close_to = _instantaneous_valid_to(start, last)

    # session.occurred
    occ_id = fact_id(session_id, "session.occurred", None, None, start, _DET)
    ops.append(CloseValidity(occ_id, close_to))

    # active_project (open interval). valid_from is session_start for the first project
    # of the session, or the switch time after a mid-session change (C-2). Close at
    # last-seen (+1 rule) but never zero-width vs the OPEN fact's own valid_from.
    if state.open_project_id:
        ap_from = state.open_project_valid_from
        if ap_from is None:
            ap_from = start
        ap_id = fact_id(
            session_id, "session.active_project", state.open_project_id, None, ap_from, _DET
        )
        ops.append(CloseValidity(ap_id, _instantaneous_valid_to(ap_from, last)))

    # used_app intervals — one per app, valid_from = app first-seen
    last_map = dict(state.open_app_last_seen)
    for app_id, app_first in state.open_app_first_seen:
        app_last = last_map.get(app_id, app_first)
        ua_id = fact_id(session_id, "session.used_app", app_id, None, app_first, _DET)
        ops.append(CloseValidity(ua_id, _instantaneous_valid_to(app_first, app_last)))

    cleared = IngestState()
    return ops, notes, cleared


def plan_flush(
    state: IngestState, now: int, config: IngestConfig, force: bool = False
) -> tuple[ChangeSet, IngestState]:
    if state.open_session_start is None:
        return ChangeSet(), state
    if not force:
        last = state.open_session_last or state.open_session_start
        if now - last < config.session_gap_ms:
            return ChangeSet(), state
    ops, notes, cleared = _close_session(state, config)
    return ChangeSet(ops=tuple(ops), notes=tuple(notes)), cleared


# --- apps -------------------------------------------------------------------


def _plan_app(
    obs: Observation,
    session_id: str,
    state: IngestState,
    existing: ExistingFacts,
    config: IngestConfig,
    now: int,
) -> tuple[list, list[str], IngestState]:
    ops: list = []
    notes: list[str] = []
    app_id = entity_id("app", obs.app)
    canon = obs.app[:-4] if obs.app.lower().endswith(".exe") else obs.app

    first_map = dict(state.open_app_first_seen)
    last_map = dict(state.open_app_last_seen)
    seen = app_id in first_map

    if not seen:
        first_map[app_id] = obs.observed_at
    last_map[app_id] = obs.observed_at
    app_first = first_map[app_id]

    new_state = replace(
        state,
        open_app_first_seen=tuple(sorted(first_map.items())),
        open_app_last_seen=tuple(sorted(last_map.items())),
    )

    # entity (idempotent at DB level via INSERT OR IGNORE in adapter)
    ops.append(InsertEntity(Entity(id=app_id, type="app", canonical_name=canon, created_at=now)))

    candidate = _make_fact(
        subject_id=session_id,
        predicate="session.used_app",
        object_entity_id=app_id,
        object_value=None,
        valid_from=app_first,
        valid_to=None,
        asserted_at=now,
        confidence=PREDICATE_REGISTRY["session.used_app"].base_confidence,
        extraction=_DET,
    )
    fuse_ops, fuse_notes = _fuse_candidate(candidate, obs, existing, config)
    ops.extend(fuse_ops)
    notes.extend(fuse_notes)
    return ops, notes, new_state


# --- project ----------------------------------------------------------------


def _plan_project(
    obs: Observation,
    session_id: str,
    state: IngestState,
    existing: ExistingFacts,
    aliases: Mapping[str, str],
    config: IngestConfig,
    now: int,
) -> tuple[list, list[str], IngestState]:
    ops: list = []
    notes: list[str] = []
    resolved = resolve_entity("project", obs.window_title, aliases, config)
    if not resolved.entity_id:
        return ops, notes, state  # no project match

    proj_id = resolved.entity_id
    session_start = state.open_session_start or obs.observed_at

    ops.append(
        InsertEntity(
            Entity(
                id=proj_id,
                type="project",
                canonical_name=resolved.canonical_name,
                created_at=now,
            )
        )
    )

    # MINOR (§1.4): a fresh rule-3 resolution writes an entity_aliases row for the matched
    # config token (casefolded) so the next sighting resolves via rule 2. INSERT OR IGNORE
    # in the adapter makes re-emission harmless.
    if resolved.is_new:
        matched = _matched_project_token(obs.window_title, proj_id, config)
        if matched is not None:
            ops.append(
                InsertAlias(
                    entity_id=proj_id,
                    alias=matched.casefold(),
                    source="deterministic",
                    created_at=now,
                )
            )

    # C-2 spec resolution (authoritative; resolves the m4 vs §3.1 contradiction):
    #   * SAME project as the currently-open one  -> valid_from = session_start.
    #     This reproduces the same deterministic fact id so rule 1 (fusion) is the
    #     natural path (m4's intent: re-derivation corroborates, never re-opens).
    #   * DIFFERENT project (a genuine mid-session switch) -> valid_from = observed_at.
    #     This makes candidate.valid_from > the open fact's valid_from, so rule 2's
    #     strict `>` fires: the old interval closes at the switch time and the new one
    #     opens. Using session_start here would build a zero-width [x, x) era (§1.2
    #     "never zero-width") and crash the schema CHECK — this is the C-2 fix.
    switching = state.open_project_id is not None and state.open_project_id != proj_id
    proj_valid_from = obs.observed_at if switching else session_start

    candidate = _make_fact(
        subject_id=session_id,
        predicate="session.active_project",
        object_entity_id=proj_id,
        object_value=None,
        valid_from=proj_valid_from,
        valid_to=None,
        asserted_at=now,
        confidence=PREDICATE_REGISTRY["session.active_project"].base_confidence,
        extraction=_DET,
    )
    fuse_ops, fuse_notes = _fuse_candidate(candidate, obs, existing, config)
    ops.extend(fuse_ops)
    notes.extend(fuse_notes)

    # Track the OPEN active_project fact's valid_from so _close_session can address it.
    # On a same-project re-derivation, keep the existing open valid_from (rule-1 fused
    # into that same era); on a switch, adopt the new candidate's valid_from.
    if switching or state.open_project_id is None:
        new_open_from = proj_valid_from
    else:
        new_open_from = state.open_project_valid_from or proj_valid_from
    new_state = replace(state, open_project_id=proj_id, open_project_valid_from=new_open_from)
    return ops, notes, new_state


# --- vocab ------------------------------------------------------------------


def _plan_vocab(
    obs: Observation, existing: ExistingFacts, config: IngestConfig, now: int
) -> tuple[list, list[str]]:
    ops: list = []
    notes: list[str] = []
    meta = obs.meta or {}
    original = meta.get("original_text") or ""
    corrected = meta.get("corrected_text") or ""
    if not original or not corrected:
        notes.append(f"vocab-skip: missing keys for {obs.source_event_id}")
        return ops, notes

    vocab_id = entity_id("vocab", original)
    ops.append(
        InsertEntity(Entity(id=vocab_id, type="vocab", canonical_name=original, created_at=now))
    )
    src = "voice_vocab" if obs.source == "bridge:vocab_file" else "correction_event"
    ops.append(
        InsertCorrection(
            original=original,
            corrected=corrected,
            detected_at=obs.observed_at,
            source=src,
            source_event_id=obs.source_event_id,
        )
    )

    candidate = _make_fact(
        subject_id=vocab_id,
        predicate="vocab.corrects_to",
        object_entity_id=None,
        object_value=corrected,
        valid_from=obs.observed_at,  # vocab facts use observed_at
        valid_to=None,
        asserted_at=now,
        confidence=PREDICATE_REGISTRY["vocab.corrects_to"].base_confidence,
        extraction=_DET,
    )
    fuse_ops, fuse_notes = _fuse_candidate(candidate, obs, existing, config)
    ops.extend(fuse_ops)
    notes.extend(fuse_notes)
    return ops, notes


# --- fusion engine (rules 0/1/2/2b/3) ---------------------------------------


def _overlaps(
    valid_from: int, valid_to: Optional[int], other_from: int, other_to: Optional[int]
) -> bool:
    """Half-open [from, to) overlap; None = open."""
    a_to = valid_to if valid_to is not None else math.inf
    b_to = other_to if other_to is not None else math.inf
    return valid_from < b_to and other_from < a_to


def _fuse_candidate(
    candidate: Fact,
    obs: Observation,
    existing: ExistingFacts,
    config: IngestConfig,
) -> tuple[list, list[str]]:
    """Apply rules 0/1/2/2b/3 for one candidate. Returns (ops, notes)."""
    ops: list = []
    notes: list[str] = []
    spec = PREDICATE_REGISTRY[candidate.predicate]
    all_facts = existing.get(candidate.subject_id, candidate.predicate)

    # RULE 0 — user-fact guard (single-valued only), across partitions
    if spec.cardinality == "single" and candidate.extraction != _USER:
        for f in all_facts:
            if f.extraction != _USER or f.retracted_at is not None:
                continue
            # user fact overlaps candidate's prospective interval?
            if f.valid_to is None or f.valid_to > candidate.valid_from:
                notes.append(
                    f"dropped-candidate: user fact holds {candidate.subject_id}/{candidate.predicate}"
                )
                return ops, notes

    # partition-local facts, non-retracted
    partition = [
        f for f in all_facts if f.extraction == candidate.extraction and f.retracted_at is None
    ]

    if spec.cardinality == "multi":
        # rule 1 (same object) vs rule 3 (new object key)
        same = [
            f
            for f in partition
            if _same_object(f, candidate)
            and (
                f.valid_to is None
                or _overlaps(candidate.valid_from, candidate.valid_to, f.valid_from, f.valid_to)
            )
        ]
        if same:
            e = same[0]
            ops.append(AddProvenance(e.id, obs.source_event_id))
            ops.append(UpdateConfidence(e.id, fuse(e.confidence, config)))
            return ops, notes
        # new object key -> insert alongside
        ops.append(InsertFact(candidate))
        ops.append(AddProvenance(candidate.id, obs.source_event_id))
        return ops, notes

    # single-valued
    # RULE 1 (C-1): duplicate support requires the same-object existing fact to be
    # OPEN (valid_to IS NULL) or its interval to OVERLAP the candidate's prospective
    # interval — mirroring the multi-valued branch and §1.5 rule 1. A same-object
    # candidate that does NOT overlap a CLOSED era is a reversion (A->B->A): the world
    # changed back, so it must fall through to rule 2 against the OPEN (different-object)
    # fact rather than boosting the dead historical era.
    same = [
        f
        for f in partition
        if _same_object(f, candidate)
        and (
            f.valid_to is None
            or _overlaps(candidate.valid_from, candidate.valid_to, f.valid_from, f.valid_to)
        )
    ]
    if same:
        # rule 1: duplicate support. Prefer an open fact / overlapping era.
        e = _pick_fusion_target(same, candidate)
        ops.append(AddProvenance(e.id, obs.source_event_id))
        ops.append(UpdateConfidence(e.id, fuse(e.confidence, config)))
        return ops, notes

    # different object on single-valued predicate
    open_diff = [f for f in partition if f.valid_to is None and not _same_object(f, candidate)]
    if open_diff:
        old = open_diff[0]
        if candidate.valid_from > old.valid_from:
            # rule 2: world changed, in-order
            ops.append(CloseValidity(old.id, candidate.valid_from))
            new = replace(candidate, valid_to=None)
            ops.append(InsertFact(new))
            ops.append(AddProvenance(new.id, obs.source_event_id))
            return ops, notes
        else:
            # rule 2b: LATE. Insert as closed historical era [cand.from, old.from)
            closed_to = old.valid_from
            # overlap check with existing CLOSED eras (same partition)
            for f in partition:
                if f.valid_to is not None and not _same_object(f, candidate):
                    if _overlaps(candidate.valid_from, closed_to, f.valid_from, f.valid_to):
                        notes.append(
                            f"dropped-candidate: late candidate overlaps closed era "
                            f"{candidate.subject_id}/{candidate.predicate}"
                        )
                        return ops, notes
            new = replace(candidate, valid_to=closed_to)
            ops.append(InsertFact(new))
            ops.append(AddProvenance(new.id, obs.source_event_id))
            return ops, notes

    # only closed eras exist (different objects) -> treat as late/new historical
    closed_diff = [
        f for f in partition if f.valid_to is not None and not _same_object(f, candidate)
    ]
    if closed_diff:
        # place before the earliest existing era if candidate is earlier; else append open
        earliest = min(closed_diff, key=lambda f: f.valid_from)
        if candidate.valid_from < earliest.valid_from:
            closed_to = earliest.valid_from
            for f in partition:
                if f.valid_to is not None and _overlaps(
                    candidate.valid_from, closed_to, f.valid_from, f.valid_to
                ):
                    notes.append(
                        f"dropped-candidate: late candidate overlaps closed era "
                        f"{candidate.subject_id}/{candidate.predicate}"
                    )
                    return ops, notes
            new = replace(candidate, valid_to=closed_to)
            ops.append(InsertFact(new))
            ops.append(AddProvenance(new.id, obs.source_event_id))
            return ops, notes

    # no existing facts (or all same handled) -> insert fresh
    ops.append(InsertFact(candidate))
    ops.append(AddProvenance(candidate.id, obs.source_event_id))
    return ops, notes


def _same_object(a: Fact, b: Fact) -> bool:
    return a.object_entity_id == b.object_entity_id and a.object_value == b.object_value


def _pick_fusion_target(same: list[Fact], candidate: Fact) -> Fact:
    """Among same-object facts, prefer one that overlaps the candidate interval;
    prefer open (valid_to is None), else the one with matching valid_from."""
    # exact valid_from match wins (re-derivation produces same id)
    for f in same:
        if f.valid_from == candidate.valid_from:
            return f
    for f in same:
        if f.valid_to is None:
            return f
    return same[0]


# ---------------------------------------------------------------------------
# 1.6 correct_fact — belief revision (extraction='user')
# ---------------------------------------------------------------------------


def plan_correct_fact(
    target: Fact,
    new_object_entity_id: Optional[str],
    new_object_value: Optional[str],
    asserted_at: int,
) -> ChangeSet:
    """Retract target; insert user replacement copying BOTH valid_from and
    valid_to (M1)."""
    replacement = _make_fact(
        subject_id=target.subject_id,
        predicate=target.predicate,
        object_entity_id=new_object_entity_id,
        object_value=new_object_value,
        valid_from=target.valid_from,
        valid_to=target.valid_to,  # M1: copy BOTH
        asserted_at=asserted_at,
        confidence=1.0,
        extraction=_USER,
    )
    ops = [
        InsertFact(replacement),
        RetractFact(target.id, asserted_at, superseded_by=replacement.id),
    ]
    return ChangeSet(ops=tuple(ops))


# ---------------------------------------------------------------------------
# 1.7 purge stub
# ---------------------------------------------------------------------------


def plan_purge_observation(
    source_event_id: str,
    facts_becoming_orphan: Sequence[Fact],
    now: int,
    vector_item_ids: Sequence[str] = (),
) -> ChangeSet:
    """Purge one observation. Caller supplies facts whose provenance becomes
    empty after removing this observation (adapter computes the set) and the
    ``vectors.item_id``s deleted with it (M-4: cp_core is pure and cannot see
    vector rows, so the adapter passes them in for tombstoning — §1.7 requires
    one purge_log row per deleted item, kind in {'observation','fact','vector'})."""
    ops: list = [PurgeObservation(source_event_id)]
    ops.append(AppendPurgeLog("observation", source_event_id, now))
    for f in facts_becoming_orphan:
        if f.extraction == _USER:
            continue  # user facts have no provenance; never cascade-purged
        ops.append(PurgeFact(f.id))
        ops.append(AppendPurgeLog("fact", f.id, now))
    for vid in vector_item_ids:
        ops.append(AppendPurgeLog("vector", vid, now))
    return ChangeSet(ops=tuple(ops))


# ---------------------------------------------------------------------------
# 1.6 chunker (D8) — deterministic
# ---------------------------------------------------------------------------


def chunk_text(content: str, config: IngestConfig) -> list[str]:
    """Deterministic fixed-window chunker (chunk_chars with chunk_overlap)."""
    if not content:
        return []
    n = len(content)
    size = config.chunk_chars
    overlap = config.chunk_overlap
    if n <= size:
        return [content]
    step = max(1, size - overlap)
    chunks: list[str] = []
    start = 0
    while start < n:
        chunks.append(content[start : start + size])
        if start + size >= n:
            break
        start += step
    return chunks


# ---------------------------------------------------------------------------
# 1.8 ranking — cosine + RRF
# ---------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Pure-python cosine similarity. REFERENCE implementation; the numpy fast
    path must agree within 1e-6."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass(frozen=True)
class RankedHit:
    item_id: str  # observation source_event_id-key or internal id (adapter's choice)
    rank: int  # 1-based


@dataclass(frozen=True)
class ScoredHit:
    item_id: str
    score: float  # cosine similarity
    observed_at: int


@dataclass(frozen=True)
class SearchHit:
    item_id: str
    score: float
    observed_at: int
    fts_rank: Optional[int]
    vec_rank: Optional[int]


def rank_hybrid(
    fts_hits: Sequence[RankedHit],
    vec_hits: Sequence[ScoredHit],
    k: int = 60,
    observed_at_by_item: Optional[Mapping[str, int]] = None,
) -> list[SearchHit]:
    """Reciprocal Rank Fusion. score = Σ_legs 1/(k + rank_leg). Ties broken by
    observed_at DESC then item_id ASC."""
    observed_at_by_item = observed_at_by_item or {}

    fts_rank: dict[str, int] = {h.item_id: h.rank for h in fts_hits}
    # vec leg: assign ranks by descending score, 1-based. M-5: break score ties with a
    # DETERMINISTIC secondary key (observed_at DESC then item_id ASC) so ranks — and thus
    # RRF scores and why.vec_rank — are a pure function of the data (Rust-port parity),
    # not SQL scan / dict-insertion order.
    vec_sorted = sorted(vec_hits, key=lambda h: (-h.score, -h.observed_at, h.item_id))
    vec_rank: dict[str, int] = {}
    vec_obs: dict[str, int] = {}
    for i, h in enumerate(vec_sorted, start=1):
        if h.item_id not in vec_rank:
            vec_rank[h.item_id] = i
            vec_obs[h.item_id] = h.observed_at

    all_ids = set(fts_rank) | set(vec_rank)
    results: list[SearchHit] = []
    for item_id in all_ids:
        score = 0.0
        fr = fts_rank.get(item_id)
        vr = vec_rank.get(item_id)
        if fr is not None:
            score += 1.0 / (k + fr)
        if vr is not None:
            score += 1.0 / (k + vr)
        obs_at = observed_at_by_item.get(item_id, vec_obs.get(item_id, 0))
        results.append(SearchHit(item_id, score, obs_at, fr, vr))

    results.sort(key=lambda h: (-h.score, -h.observed_at, h.item_id))
    return results
