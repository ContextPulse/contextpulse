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

import logging
import time as _time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from contextpulse_core import probe
from contextpulse_core.redact import redact_sensitive

mcp_app = FastMCP("ContextPulse Probe")
logger = logging.getLogger(__name__)


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "unknown time"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _fmt_facts(hits: list[dict]) -> str:
    """Render probe facts for an MCP client, scrubbing secrets on the way out.

    facts_about and context_at are LIVE tools, and probe.db is a DERIVED store:
    facts written by consolidator runs before the capture-side fixes quote
    pre-fix event text, including clipboard text, and no purge of activity.db
    touches them. Going forward the consolidator's input is redacted at
    probe._extract_text, so this covers the backlog.
    """
    lines = []
    for h in hits:
        conf = h.get("confidence", 0.5)
        srcs = h.get("source_event_ids") or []
        src = f" [src:{len(srcs)}]" if srcs else ""
        entity = redact_sensitive(str(h.get("entity") or ""))
        fact = redact_sensitive(str(h.get("fact") or ""))
        lines.append(
            f"- ({_fmt_ts(h.get('valid_from'))}, conf {conf:.2f}) "
            f"{entity}: {fact}{src}"
        )
    return "\n".join(lines)


def _record_usage_safe(conn, tool: str, query: str, hit_count: int) -> None:
    """Log a real probe-tool call (cp-savegate-attribution-instrument).

    Automatic and unconditional — called on every real invocation regardless
    of hit_count, so a future save count is never ambiguous between "no
    value" and "nothing was watching". Instrumentation must never break the
    tool it observes: any failure here is logged and swallowed, not raised.
    """
    try:
        probe.record_usage(conn, tool, query, hit_count)
    except Exception:
        logger.exception("probe usage logging failed for tool=%s (recall unaffected)", tool)


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
    """Recall what was decided about a person, project, purchase, trip or tool, and why.

    Searches facts distilled every 6 hours from this computer's screen, voice and
    input: relationships, states and decisions that keyword search over the raw
    log cannot answer. If no fact names the subject, says whether the raw capture
    mentions it.

    Args:
        entity: Project, person, file, tool, or topic (whole words, case-insensitive).
    """
    conn = probe.connect_probe(probe.default_probe_db())
    try:
        hits = probe.query_facts_about(conn, entity)
        _record_usage_safe(conn, "facts_about", entity, len(hits))
        total = 0 if hits else probe.count_facts(conn)
    finally:
        conn.close()
    if not hits:
        if total == 0:
            miss = (
                f"No probe facts about '{entity}': the probe store holds no facts yet "
                "(the consolidator has not written any)."
            )
        else:
            miss = (
                f"No probe facts about '{entity}': none of the {total} consolidated facts "
                "names it as an entity or mentions it in its text."
            )
        return miss + "\n" + _raw_fallback(entity)
    return f"Facts about '{entity}':\n{_fmt_facts(hits)}"


def _raw_fallback(entity: str) -> str:
    """Say whether the raw capture log names ``entity``, labelled as raw.

    Action cp-facts-about-raw-fallback-word-match: half the real misses had the
    subject in the raw log. These rows are NOT counted as hits in tool_usage,
    which measures distilled facts; a raw mention is weaker evidence and the
    output says so.
    """
    try:
        raw = probe.query_raw_mentions(probe.default_activity_db(), entity)
    except Exception:
        logger.exception("raw fallback failed for %r (facts answer unaffected)", entity)
        raw = None
    if raw is None:
        return "Raw capture log not searched (activity.db unavailable)."
    if raw.count == 0:
        return f"Raw capture has no mention of '{entity}' either ({raw.examined} candidate events checked)."
    floor = " at least" if raw.capped else ""
    lines = [
        f"Raw capture mentions '{entity}' in{floor} {raw.count} events. These are raw "
        "screen/voice/clipboard records, not distilled facts; newest first:"
    ]
    for s in raw.samples:
        lines.append(f"- ({_fmt_ts(s['timestamp'])}, {s['modality']}, {s['app_name']}) {s['snippet']}")
    return "\n".join(lines)


def _fmt_span(first: float, last: float) -> str:
    """'2026-10-10 21:43 to 22:03', with the second date only when the day changes."""
    a, b = _fmt_ts(first), _fmt_ts(last)
    return f"{a} to {b[11:]}" if a[:10] == b[:10] else f"{a} to {b}"


def _raw_window_fallback(t: float, window_minutes: int) -> str:
    """Say what the raw capture saw around ``t``, labelled as raw.

    Both real context_at calls up to 2026-11-04 returned nothing, because facts
    are distilled every 6 hours and each carries one timestamp, while the raw log
    held events for those windows. These rows are NOT counted as hits in
    tool_usage, which measures distilled facts.
    """
    try:
        raw = probe.query_raw_window(probe.default_activity_db(), t, window_s=window_minutes * 60)
    except Exception:
        logger.exception("raw window fallback failed (context_at answer unaffected)")
        raw = None
    if raw is None:
        return "Raw capture log not searched (activity.db unavailable)."
    if raw.count == 0:
        return f"Raw capture holds nothing within {window_minutes}min of that time either."
    kinds = ", ".join(f"{name} {n}" for name, n in sorted(raw.by_modality.items()))
    lines = [f"Raw capture holds {raw.count} events in that window ({kinds}). These are raw records, not distilled facts."]
    if raw.windows:
        lines.append("Windows, most events first:")
        for w in raw.windows:
            lines.append(f"- {w['app']}: {w['title'][:120]} ({w['count']} events, {_fmt_span(w['first'], w['last'])})")
        hidden = raw.distinct_windows - len(raw.windows)
        if hidden > 0:
            lines.append(f"- and {hidden} more windows")
    if raw.dictation:
        lines.append("Dictated, newest first:")
        for d in raw.dictation:
            lines.append(f"- ({_fmt_ts(d['timestamp'])}) {d['text']}")
    return "\n".join(lines)


@mcp_app.tool()
def context_at(when: str, window_minutes: int = 30) -> str:
    """Recall what was happening on this computer around a moment in time.

    Returns facts distilled from that window. If none fall in it, returns the
    raw capture instead: the windows on screen, event counts and dictated words,
    labelled as raw.

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
        _record_usage_safe(conn, "context_at", f"{when} +/-{window_minutes}min", len(hits))
        distilled = None if hits else probe.last_distilled_at(conn)
    finally:
        conn.close()
    if not hits:
        lines = [f"No probe facts within {window_minutes}min of {_fmt_ts(t)}."]
        if distilled is not None and t + window_minutes * 60 > distilled:
            lines.append(f"Facts were last distilled at {_fmt_ts(distilled)}, so later events have none yet.")
        lines.append(_raw_window_fallback(t, window_minutes))
        return "\n".join(lines)
    return f"Context around {_fmt_ts(t)} (+/-{window_minutes}min):\n{_fmt_facts(hits)}"
