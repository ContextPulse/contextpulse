# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Choose probe facts to hand a Claude Code session without being asked.

Why this exists: the 2026-10-24 usefulness review found that 0 of 32 sessions
holding a recall-shaped prompt called ``facts_about``, while 2,018 journal
queries ran in the same sessions. A tool the model must remember to call does
not get called. This module is the retrieval half of a two-week trial in which
a SessionStart and a UserPromptSubmit hook put matching facts into the session
instead (David, 2026-10-24, journal row 28650).

Everything here is pure: it takes fact rows and a prompt and returns rows and
text. Opening the database, deadlines, logging and the estate gates live in
the hook, so this module can be tested without any of them.

Matching is on WHOLE WORDS, never substrings. A substring match is what made
``facts_about("CLEAR")`` return 29 facts about things being "cleared" and none
about the CLEAR renewal. A term matches a fact when it equals one of the
fact's word tokens; an entity matches a prompt when the entity's token
sequence appears contiguously in the prompt's tokens.

Precision filter (all thresholds are keyword arguments, so they can be tuned
without a code change to the caller):

* a term scores ``ln(N / df)``, where ``df`` is how many facts contain it;
  stopwords and terms present in more than ``max_df_share`` of facts score 0;
* an entity named in the prompt adds ``entity_bonus``;
* a fact needs ``score >= min_score`` AND an anchor -- its entity named in the
  prompt, or at least ``min_terms`` matched words of which one is held by at
  most ``rare_df`` facts -- so a fact never qualifies on one shared word or on
  common words alone;
* facts below ``min_confidence`` are never offered.

Output is capped in estimated tokens (characters / 3.5, rounded up), header
included, and a fact already injected earlier in the session is skipped.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from contextpulse_core.redact import redact_sensitive

_TOKEN = re.compile(r"[a-z0-9]+")

# Common English plus words that occur in almost every agent prompt and so say
# nothing about which fact is wanted. Kept short on purpose: the df ceiling
# below removes whatever is common in THIS store, so this list only has to
# cover words that are common in prompts but rarer in facts.
STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing done down during
    each few for from further get got had has have having he her here hers him his how i
    if in into is it its itself just let me more most my no nor not now of off on once
    only or other our ours out over own same she should so some such than that the their
    theirs them then there these they this those through to too under until up very was
    we were what when where which while who whom why will with would you your yours
    yes ok okay please thanks thank need want make made like know think see look use used
    using one two new also still well way thing things something anything everything
    today yesterday tomorrow time times day days week weeks
    david claude session sessions agent agents file files work working run running ran
    say said says tell told ask asked going go goes gone come came take took give gave
    lets let's fix fixed check checked status left right last next first where land
    landed decide decided decision back again help find found show try tried keep
    start started stop stopped done doing good bad sure maybe really actually just
    able even much many every another same different part point case cases kind sort
    """.split()
)

BASE_HEADER = (
    "CONTEXTPULSE MEMORY -- auto-injected for a two-week trial. These are UNVERIFIED facts "
    "a model distilled from David's screen and voice capture; they may be stale or wrong "
    "(1 in 8 was wrong in a hand-graded sample). Check one against the journal or its "
    "source before acting on it. If a fact below helps, name it by its cp# id."
)

# The trial passes on saves logged with this script (scripts/injection_trial_report.py).
# Only the session that used a fact can log one, so the header has to say how; the
# path is resolved from this checkout rather than written into source.
SAVE_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "probe_save.py"


def save_hint(script: Path = SAVE_SCRIPT) -> str:
    """The sentence telling a session how to log a save; empty when the script is absent."""
    if not script.is_file():
        return ""
    return (
        " If one told you something the journal, memory files and CLAUDE.md did not, and you"
        f" used it, log the save once: python {script.as_posix()} --tool inject --fact-id N"
        ' --query "<the prompt>" --confirmed-novel --note "<what it changed>"'
    )


DEFAULT_HEADER = BASE_HEADER
# Once per session is enough, and the hint costs about 95 tokens: on the 600-token
# prompt block that would be one fewer fact on every prompt.
SESSION_HEADER = BASE_HEADER + save_hint()


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens. "cleared" and "clear" are different tokens."""
    return _TOKEN.findall((text or "").lower())


def estimate_tokens(text: str) -> int:
    """A conservative token estimate: 3.5 characters per token, rounded up."""
    return math.ceil(len(text) / 3.5) if text else 0


@dataclass(frozen=True)
class Fact:
    id: int
    entity: str
    fact: str
    valid_from: float | None
    confidence: float


@dataclass
class Scored:
    fact: Fact
    score: float
    entity_hit: bool
    matched: list[str] = field(default_factory=list)


class Index:
    """Token statistics over one snapshot of the fact table."""

    def __init__(self, facts: Sequence[Fact]) -> None:
        self.facts = list(facts)
        self.fact_tokens: dict[int, frozenset[str]] = {}
        self.entity_tokens: dict[int, tuple[str, ...]] = {}
        df: Counter[str] = Counter()
        for f in self.facts:
            ent = tuple(tokens(f.entity))
            toks = frozenset(tokens(f.fact)) | frozenset(ent)
            self.fact_tokens[f.id] = toks
            self.entity_tokens[f.id] = ent
            df.update(toks)
        self.df = df
        self.n = max(len(self.facts), 1)

    def idf(self, term: str) -> float:
        d = self.df.get(term, 0)
        return math.log(self.n / d) if d else 0.0


def _contains_run(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """True when ``needle`` occurs as a contiguous run of whole tokens."""
    k = len(needle)
    if k == 0 or k > len(haystack):
        return False
    first = needle[0]
    for i, tok in enumerate(haystack):
        if tok == first and tuple(haystack[i : i + k]) == tuple(needle):
            return True
    return False


def _usable_entity(ent: tuple[str, ...]) -> bool:
    """An entity is matchable when it has a non-stopword token of length 3+."""
    return any(len(t) >= 3 and t not in STOPWORDS and not t.isdigit() for t in ent)


def entity_named_in(entity: str, text: str) -> bool:
    """Whole-word, case-insensitive: is ``entity`` named in ``text``?"""
    ent = tokens(entity)
    return bool(ent) and _contains_run(tokens(text), ent)


def word_in(term: str, text: str) -> bool:
    """Whole-word, case-insensitive: does ``term`` (one or more words) occur in ``text``?"""
    return entity_named_in(term, text)


def score_facts(
    prompt: str,
    index: Index,
    *,
    min_score: float = 6.0,
    min_confidence: float = 0.6,
    entity_bonus: float = 3.0,
    rare_df: int = 20,
    max_df_share: float = 0.15,
    min_terms: int = 2,
) -> list[Scored]:
    """Facts that pass the precision filter for ``prompt``, best first."""
    prompt_toks = tokens(prompt)
    max_df = max(1, int(index.n * max_df_share))
    terms = {
        t
        for t in prompt_toks
        if len(t) >= 3 and t not in STOPWORDS and 0 < index.df.get(t, 0) <= max_df
    }
    out: list[Scored] = []
    for f in index.facts:
        if f.confidence < min_confidence:
            continue
        ent = index.entity_tokens[f.id]
        entity_hit = _usable_entity(ent) and _contains_run(prompt_toks, ent)
        matched = sorted(terms & index.fact_tokens[f.id])
        if not entity_hit and not matched:
            continue
        score = sum(index.idf(t) for t in matched) + (entity_bonus if entity_hit else 0.0)
        # One shared word is never enough on its own: replay against the live store
        # (2026-10-24) showed single matches on "renewal", "say" and "going" pulling
        # unrelated facts. Without the entity, two distinct words, one of them rare.
        anchored = entity_hit or (
            len(matched) >= min_terms and any(index.df[t] <= rare_df for t in matched)
        )
        if anchored and score >= min_score:
            out.append(Scored(f, score, entity_hit, matched))
    out.sort(key=lambda s: (-s.score, -(s.fact.valid_from or 0.0), -s.fact.confidence))
    return out


def recent_facts(
    facts: Iterable[Fact], now: float, *, hours: float = 72.0, min_confidence: float = 0.8
) -> list[Scored]:
    """High-confidence facts that became true in the last ``hours``, newest first."""
    floor = now - hours * 3600
    picked = [
        Scored(f, 0.0, False)
        for f in facts
        if f.confidence >= min_confidence and f.valid_from is not None and floor <= f.valid_from <= now
    ]
    picked.sort(key=lambda s: (-(s.fact.valid_from or 0.0), -s.fact.confidence))
    return picked


def entity_facts(facts: Iterable[Fact], name: str, *, min_confidence: float = 0.6) -> list[Scored]:
    """Facts whose entity names ``name`` (whole words), newest first."""
    want = tokens(name)
    if not want:
        return []
    picked = [
        Scored(f, 0.0, True)
        for f in facts
        if f.confidence >= min_confidence and _contains_run(tokens(f.entity), want)
    ]
    picked.sort(key=lambda s: (-(s.fact.valid_from or 0.0), -s.fact.confidence))
    return picked


def _age(valid_from: float | None, now: float) -> str:
    if valid_from is None:
        return "date unknown"
    days = (now - valid_from) / 86400
    if days < 1:
        return "today"
    return f"{int(days)}d ago"


def render_line(f: Fact, now: float) -> str:
    """One line per fact: id, date, age, confidence, entity and text, secrets scrubbed."""
    try:
        date = datetime.fromtimestamp(float(f.valid_from)).strftime("%Y-%m-%d") if f.valid_from else "????-??-??"
    except (ValueError, OSError, OverflowError):
        date = "????-??-??"
    entity = redact_sensitive(f.entity).replace("\n", " ")
    text = redact_sensitive(f.fact).replace("\n", " ")
    return f"- cp#{f.id} [{date}, {_age(f.valid_from, now)}, conf {f.confidence:.2f}] {entity}: {text}"


@dataclass
class Block:
    text: str
    fact_ids: list[int]
    tokens: int


def build_block(
    candidates: Sequence[Scored],
    *,
    now: float,
    token_cap: int,
    exclude_ids: Iterable[int] = (),
    max_facts: int = 12,
    header: str = DEFAULT_HEADER,
    subheading: str = "",
) -> Block | None:
    """Render up to ``max_facts`` new facts inside ``token_cap``; None when none fit.

    Candidates are taken in order and a fact whose line would push the block
    over the cap is skipped, so a long fact never crowds out shorter ones
    below it. The header counts against the cap.
    """
    excluded = set(exclude_ids)
    lines = [header] + ([subheading] if subheading else [])
    used = estimate_tokens("\n".join(lines))
    ids: list[int] = []
    for s in candidates:
        if len(ids) >= max_facts:
            break
        if s.fact.id in excluded or s.fact.id in ids:
            continue
        line = render_line(s.fact, now)
        cost = estimate_tokens("\n" + line)
        if used + cost > token_cap:
            continue
        lines.append(line)
        ids.append(s.fact.id)
        used += cost
    if not ids:
        return None
    text = "\n".join(lines)
    return Block(text=text, fact_ids=ids, tokens=estimate_tokens(text))


def load_facts(conn) -> list[Fact]:
    """Every fact row, as Fact. ``conn`` may be read-only."""
    rows = conn.execute("SELECT id, entity, fact, valid_from, confidence FROM facts").fetchall()
    out = []
    for r in rows:
        try:
            vf = float(r[3]) if r[3] is not None else None
        except (TypeError, ValueError):
            vf = None
        try:
            conf = float(r[4]) if r[4] is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        out.append(Fact(int(r[0]), str(r[1] or ""), str(r[2] or ""), vf, conf))
    return out
