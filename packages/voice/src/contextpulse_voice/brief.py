# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Dictation brief mode: turn a long spoken request into text an agent can work from.

Templates:
- ``brief``: a session goal (goal, why, scope, non-goals, done-when, anti-gaming,
  autonomy, report, guesses). The model returns JSON fields; this module renders
  the layout, so the layout cannot drift with the model.
- ``reply``: the dictation tidied into a reply to an agent, order and questions kept.
- ``verbatim``: the dictation as dictation already produces it. No model call.

Rules this module keeps:
- **Never lose the dictation.** Every failure (no backend, timeout, bad JSON,
  conversational reply, unknown template) returns the dictation itself, with
  the reason.
- **Secrets stay local.** A dictation matching a secret pattern is not sent,
  the same rule as ``cleanup.clean_with_llm``.
- **Say what really ran.** ``BriefOutcome.llm_ok`` is True only when the model's
  output is what gets pasted.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from contextpulse_core.redact import redact_sensitive

from contextpulse_voice.brief_templates import (
    BRIEF_SYSTEM_PROMPT,
    DEFAULT_AUTONOMY,
    DEFAULT_REPORT,
    REPLY_SYSTEM_PROMPT,
)
from contextpulse_voice.cleanup import _is_hallucination
from contextpulse_voice.llm_backend import LLMResult, complete

logger = logging.getLogger(__name__)

TEMPLATES = ("brief", "reply", "verbatim")

_REQUIRED_STR = ("goal_type", "goal", "why", "scope", "non_goals", "done_when")
_OPTIONAL_STR = ("anti_gaming", "autonomy", "report")
# A brief whose goal, scope or done-when is nothing but a clarification tag
# cannot be worked from. The goal scorer cannot tell (a tag contains the words
# it looks for), so this label is the check.
_CORE_FIELDS = ("goal", "scope", "done_when")
_TAG = re.compile(r"\[NEEDS CLARIFICATION:[^\]]*\]", re.IGNORECASE)


@dataclass(frozen=True)
class BriefOutcome:
    text: str
    template: str
    llm_ok: bool
    reason: str = ""
    not_ready: bool = False


def parse_brief_json(raw: str) -> dict:
    """Parse the model's brief. Raises ValueError on anything but the expected shape."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    for key in _REQUIRED_STR:
        if not isinstance(data.get(key), str):
            raise ValueError(f"missing or non-string {key!r}")
    for key in _OPTIONAL_STR:
        if not isinstance(data.get(key, ""), str):
            raise ValueError(f"non-string {key!r}")
    guesses = data.get("needs_clarification", [])
    if not isinstance(guesses, list) or not all(isinstance(g, str) for g in guesses):
        raise ValueError("needs_clarification is not a list of strings")
    return data


def _only_a_tag(value: str) -> bool:
    return not re.search(r"\w", _TAG.sub("", value or ""))


def render_brief(fields: dict) -> tuple[str, bool]:
    """Lay out a parsed brief as paste text. Returns (text, not_ready)."""
    not_ready = any(_only_a_tag(fields.get(k, "")) for k in _CORE_FIELDS)
    goal_type = fields.get("goal_type", "").strip() or "delivery"
    lines: list[str] = []
    if not_ready:
        lines.append(
            "NOT READY: the goal, scope or done-when is only a guess. "
            "Answer the NEEDS CLARIFICATION items before starting."
        )
    lines.append(f"Goal ({goal_type}): {fields['goal'].strip()}")
    lines.append(f"Why: {fields['why'].strip()}")
    lines.append(f"Scope: {fields['scope'].strip()}")
    if fields["non_goals"].strip():
        lines.append(f"Non-goals: {fields['non_goals'].strip()}")
    lines.append(f"Done when: {fields['done_when'].strip()}")
    anti = fields.get("anti_gaming", "").strip()
    if anti and goal_type != "learning":
        lines.append(f"Anti-gaming: {anti}")
    for label, key, default in (("Autonomy", "autonomy", DEFAULT_AUTONOMY),
                                ("Report", "report", DEFAULT_REPORT)):
        said = fields.get(key, "").strip()
        lines.append(f"{label}: {said}" if said else f"{label} (default): {default}")
    guesses = [g.strip() for g in fields.get("needs_clarification", []) if g.strip()]
    if guesses:
        lines.append("Guesses to confirm:")
        lines.extend(f"- {g}" for g in guesses)
    return "\n".join(lines), not_ready


def make_brief(
    dictation: str,
    template: str = "brief",
    *,
    complete_fn: Callable[..., LLMResult] = complete,
    backend: str = "auto",
    model: str = "haiku",
    timeout: float = 30.0,
    api_key: str = "",
) -> BriefOutcome:
    """Turn a dictation into paste text by ``template``. Never raises, never drops the dictation."""

    def fallback(reason: str) -> BriefOutcome:
        logger.info("Brief fell back to the dictation (template=%s): %s", template, reason)
        return BriefOutcome(text=dictation, template=template, llm_ok=False, reason=reason)

    if template == "verbatim":
        return BriefOutcome(text=dictation, template="verbatim", llm_ok=False, reason="verbatim")
    if template not in TEMPLATES:
        return fallback(f"unknown template {template!r}")
    if not dictation.strip():
        return fallback("empty dictation")
    if redact_sensitive(dictation) != dictation:
        return fallback("dictation matches a secret pattern; not sent")

    system = BRIEF_SYSTEM_PROMPT if template == "brief" else REPLY_SYSTEM_PROMPT
    result = complete_fn(system, "Dictation:\n" + dictation, backend=backend, model=model,
                         timeout=timeout, api_key=api_key)
    if not result.ok:
        return fallback(result.error or "model call failed")

    if template == "reply":
        reply = result.text.strip()
        if not reply:
            return fallback("model returned empty reply")
        if _is_hallucination(reply):
            return fallback("model returned a conversational answer, not a rewrite")
        return BriefOutcome(text=reply, template="reply", llm_ok=True)

    try:
        fields = parse_brief_json(result.text)
    except ValueError as exc:
        return fallback(f"model output was not a valid brief ({exc})")
    text, not_ready = render_brief(fields)
    return BriefOutcome(text=text, template="brief", llm_ok=True, not_ready=not_ready)
