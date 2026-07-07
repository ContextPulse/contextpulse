# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Phase 0 wedge-probe MCP tools — facts_about / context_at. THROWAWAY.

Exposes fused (entity) and temporal recall over the throwaway ``probe.db`` so
the founder can dogfood the "save" hypothesis from Claude Code. Registered into
the unified facade (``mcp_unified._register_probe_tools``). Deleted at Phase 1
when the real ``packages/knowledge/`` query API replaces it.

See ``.internal/fable-redesign/cp-implementation-plan-FINAL.md`` §Phase 0.
"""

from __future__ import annotations

import time as _time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from contextpulse_core import probe

mcp_app = FastMCP("ContextPulse Probe")


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "unknown time"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _fmt_facts(hits: list[dict]) -> str:
    lines = []
    for h in hits:
        conf = h.get("confidence", 0.5)
        srcs = h.get("source_event_ids") or []
        src = f" [src:{len(srcs)}]" if srcs else ""
        lines.append(
            f"- ({_fmt_ts(h.get('valid_from'))}, conf {conf:.2f}) "
            f"{h.get('entity')}: {h.get('fact')}{src}"
        )
    return "\n".join(lines)


def _parse_when(when: str) -> float | None:
    """Accept a unix timestamp or an ISO-ish datetime string; None on failure."""
    when = (when or "").strip()
    if not when:
        return None
    try:
        return float(when)
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
            return datetime.strptime(when, fmt).timestamp()
        except ValueError:
            continue
    return None


@mcp_app.tool()
def facts_about(entity: str) -> str:
    """[Phase 0 probe] Recall consolidated facts about an entity.

    Fused recall over the nightly-distilled probe facts — relationships, states,
    and decisions that plain keyword search over the raw event log can't answer.

    Args:
        entity: Project, person, file, tool, or topic (partial, case-insensitive).
    """
    conn = probe.connect_probe(probe.default_probe_db())
    try:
        hits = probe.query_facts_about(conn, entity)
    finally:
        conn.close()
    if not hits:
        return f"No probe facts about '{entity}' yet. (Phase 0 probe DB may be empty until the nightly consolidator has run.)"
    return f"Facts about '{entity}':\n{_fmt_facts(hits)}"


@mcp_app.tool()
def context_at(when: str, window_minutes: int = 30) -> str:
    """[Phase 0 probe] Recall what was true/happening around a moment in time.

    Temporal recall over the probe facts within +/- window_minutes of ``when``.

    Args:
        when: A unix timestamp or datetime ("2026-07-07 14:30", "2026-07-07").
              Also accepts "now".
        window_minutes: Half-width of the time window (default 30).
    """
    t = _time.time() if when.strip().lower() == "now" else _parse_when(when)
    if t is None:
        return f"Could not parse time '{when}'. Use a unix timestamp or 'YYYY-MM-DD HH:MM'."
    conn = probe.connect_probe(probe.default_probe_db())
    try:
        hits = probe.query_context_at(conn, t, window_s=window_minutes * 60)
    finally:
        conn.close()
    if not hits:
        return f"No probe facts within {window_minutes}min of {_fmt_ts(t)}."
    return f"Context around {_fmt_ts(t)} (+/-{window_minutes}min):\n{_fmt_facts(hits)}"
