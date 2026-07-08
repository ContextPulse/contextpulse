# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Phase 1 knowledge-graph MCP tools.

Fused (subject), temporal (context_at), historical (timeline), and full-text
(search) recall over the real bi-temporal knowledge graph in ``knowledge.db``.
The Phase-1 successor to the throwaway ``probe_mcp`` tools: same recall surface,
backed by the validated ``KnowledgeStore`` instead of the nightly probe.

Registered into the unified facade by ``mcp_unified._register_knowledge_tools``,
gated on ``config.knowledge_enabled`` (default false). When enabled, it REPLACES
the probe tools (both define ``facts_about`` / ``context_at``), so exactly one
provider serves those names.

The store is bi-temporal and works in **epoch milliseconds**; these tools accept
human times (unix seconds, ISO-ish strings, or "now") and convert. Each call
opens the store read-side and closes it — Phase-1 dogfood scale, mirrors probe.
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from contextpulse_knowledge.bridge import default_knowledge_db
from contextpulse_knowledge.cp_core import Fact
from contextpulse_knowledge.store_sqlite import KnowledgeStore

mcp_app = FastMCP("ContextPulse Knowledge")


# ── formatting helpers (store works in ms; humans read dates) ────────

def _fmt_ms(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "unknown time"
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return str(ts_ms)


def _fact_object(f: Fact) -> str:
    """The object side of a fact triple: entity id or literal value."""
    return f.object_entity_id or f.object_value or ""


def _fmt_fact(f: Fact) -> str:
    obj = _fact_object(f)
    valid = _fmt_ms(f.valid_from)
    if f.valid_to is not None:
        valid += f" -> {_fmt_ms(f.valid_to)}"
    return f"- ({valid}, conf {f.confidence:.2f}) {f.subject_id} {f.predicate} {obj}".rstrip()


def _parse_when_ms(when: str) -> int | None:
    """Accept 'now', a unix-seconds timestamp, or an ISO-ish datetime -> epoch ms."""
    when = (when or "").strip()
    if not when:
        return None
    if when.lower() == "now":
        return int(_time.time() * 1000)
    try:
        return int(float(when) * 1000)  # unix seconds -> ms
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
    ):
        try:
            return int(datetime.strptime(when, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _open_store() -> KnowledgeStore | None:
    """Open the knowledge store, or None if it hasn't been built yet.

    Returns None (rather than creating an empty db) when knowledge.db is absent,
    so tools give a clear "not built yet" message before the Phase-1 backfill has
    run instead of silently answering from an empty graph.
    """
    path = os.environ.get("CONTEXTPULSE_KNOWLEDGE_DB") or default_knowledge_db()
    if not os.path.exists(path):
        return None
    # Default IngestConfig (no project snapshot): the query path resolves subjects
    # from the entities/aliases tables; the project-registry fallback is only for
    # projects-as-subjects, which don't exist in Phase 1 (projects are fact
    # objects). Avoids rescanning the Projects dir on every tool call.
    return KnowledgeStore(path)


_NOT_BUILT = (
    "Knowledge graph not built yet (knowledge.db is absent). It is populated by "
    "the Phase-1 bridge backfill / live ingestor."
)


# ── tools ────────────────────────────────────────────────────────────

@mcp_app.tool()
def facts_about(subject: str) -> str:
    """Recall consolidated facts about a subject from the knowledge graph.

    Fused recall over the bi-temporal KG — a subject's current, non-retracted
    facts (states, relationships, decisions) that plain keyword search can't
    answer.

    NOTE: this is SUBJECT-scoped. Pass a canonical entity the subject side of a
    triple would use (a person, session, file, or topic). A project is usually a
    fact OBJECT in Phase 1, so recall for "what was I doing on project X" is via
    context_at, not facts_about('project:X').

    Args:
        subject: Canonical entity id or a surface form (person, file, topic).
    """
    store = _open_store()
    if store is None:
        return _NOT_BUILT
    try:
        facts = store.facts_about(subject)
    finally:
        store.close()
    if not facts:
        return f"No knowledge-graph facts about '{subject}'."
    body = "\n".join(_fmt_fact(f) for f in facts)
    return f"Facts about '{subject}':\n{body}"


@mcp_app.tool()
def context_at(when: str, window_minutes: int = 15) -> str:
    """Recall what was true/happening around a moment in time.

    Temporal recall over the KG: the active project, apps in use, and context
    facts valid at ``when``. This is the tool for "what was I working on around
    <time>".

    Args:
        when: "now", a unix timestamp, or a datetime ("2026-07-08 14:30").
        window_minutes: Provenance window half-width for corroborating
            observations (default 15).
    """
    t = _parse_when_ms(when)
    if t is None:
        return f"Could not parse time '{when}'. Use 'now', a unix timestamp, or 'YYYY-MM-DD HH:MM'."
    store = _open_store()
    if store is None:
        return _NOT_BUILT
    try:
        ctx = store.context_at(t, window_ms=window_minutes * 60_000)
    finally:
        store.close()

    facts = ctx.get("facts") or []
    if not facts:
        return f"No knowledge-graph context around {_fmt_ms(t)}."

    lines = [f"Context around {_fmt_ms(t)} (+/-{window_minutes}min):"]
    project = ctx.get("project")
    if project is not None:
        lines.append(f"Active project: {_fact_object(project)}")
    apps = ctx.get("apps") or []
    if apps:
        app_names = ", ".join(_fact_object(a) for a in apps if _fact_object(a))
        if app_names:
            lines.append(f"Apps: {app_names}")
    lines.append("Facts:")
    lines.extend(_fmt_fact(f) for f in facts)
    return "\n".join(lines)


@mcp_app.tool()
def kg_timeline(subject: str, days: int = 7) -> str:
    """Show the history of facts about a subject over recent days.

    Every fact whose validity overlaps the window, oldest first — how a subject's
    state evolved (e.g. a project's status changes across a week).

    Args:
        subject: Canonical entity id or surface form.
        days: How many days back the window starts (default 7).
    """
    store = _open_store()
    if store is None:
        return _NOT_BUILT
    try:
        since = int((_time.time() - days * 86400) * 1000)
        facts = store.timeline(subject, since=since)
    finally:
        store.close()
    if not facts:
        return f"No knowledge-graph timeline for '{subject}' in the last {days} days."
    body = "\n".join(_fmt_fact(f) for f in facts)
    return f"Timeline for '{subject}' (last {days} days):\n{body}"


@mcp_app.tool()
def search_knowledge(query: str, limit: int = 10) -> str:
    """Full-text search the knowledge graph's observations.

    Ranked full-text search over captured observation text (OCR, transcripts,
    typed bursts, clipboard). Returns matching snippets with their time; use
    facts_about / context_at for synthesized facts rather than raw text.

    Args:
        query: Search terms.
        limit: Max results (default 10).
    """
    store = _open_store()
    if store is None:
        return _NOT_BUILT
    try:
        # Vector half needs a query embedding (Phase-1 embedding path is stubbed),
        # so FTS is the working mode.
        hits = store.search(query, k=limit, mode="fts")
    finally:
        store.close()
    if not hits:
        return f"No knowledge-graph matches for '{query}'."
    lines = [f"Search results for '{query}':"]
    for h in hits:
        snippet = (h.get("snippet") or "").replace("\n", " ").strip()
        lines.append(f"- ({_fmt_ms(h.get('observed_at'))}) {snippet}")
    return "\n".join(lines)
