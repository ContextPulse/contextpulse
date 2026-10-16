"""Opt-in live test: brief mode through the real Claude Code CLI on three dictations.

Runs only with CONTEXTPULSE_LIVE_BRIEF=1 and the `claude` CLI signed in. Uses the
user's plan through `claude -p` (no API key; the child environment has none).

The assertions are chosen to fail on a canned or echoed response: an echo of
the dictation is not valid brief JSON, and a model that ignored the dictation
would not carry its stated constraints into the non-goals.
"""

import os
import re

import pytest
from contextpulse_voice.brief import make_brief, parse_brief_json
from contextpulse_voice.brief_templates import BRIEF_SYSTEM_PROMPT
from contextpulse_voice.llm_backend import find_claude, run_claude_cli

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("CONTEXTPULSE_LIVE_BRIEF") != "1" or find_claude() is None,
        reason="live brief test is opt-in: set CONTEXTPULSE_LIVE_BRIEF=1 with the claude CLI signed in",
    ),
    pytest.mark.timeout(300),
]

DICTATIONS = {
    "d1_delivery": (
        "okay so um I want you to go into the ContextPulse repo and the thing is the fix hotkey, "
        "control shift space, when the API call fails it still says cleanup applied true in the "
        "event, which is wrong, so fix that so it records whether the LLM actually ran. Don't touch "
        "the main dictation path, just the fix last path. And uh there should be a test for it, the "
        "failure case, like mock the anthropic client to throw. Don't push anything, just commit "
        "locally. That's it, it's small."
    ),
    "d2_learning": (
        "so the daemon, the ContextPulse daemon, it's been using a lot of memory again I think, or "
        "maybe not, I saw it at like seven hundred megs this morning and I don't know if that's "
        "normal now after the dxcam fix. I want you to figure out whether that's actually growing or "
        "if it's stable, like look at it over a few hours or look at whatever logs we have, and tell "
        "me what's going on. Don't change any code yet, I just want to understand it."
    ),
    "d3_vague": (
        "alright I want to build that brief mode thing for dictation, you know, where I talk and it "
        "turns it into a prompt. Make it good. Use the templates we talked about."
    ),
}

_FILEISH = re.compile(r"[\w./-]+\.(?:py|md|json|log|txt|toml)\b|\btest_\w+")


@pytest.fixture(scope="module")
def raw_results():
    out = {}
    for key, text in DICTATIONS.items():
        r = run_claude_cli(BRIEF_SYSTEM_PROMPT, "Dictation:\n" + text, model="haiku", timeout=60)
        assert r.ok, f"{key}: {r.error}"
        out[key] = r
    return out


@pytest.mark.parametrize("key", list(DICTATIONS))
def test_every_field_present_and_reports_a_model(raw_results, key):
    r = raw_results[key]
    fields = parse_brief_json(r.text)
    assert isinstance(fields["needs_clarification"], list)
    assert "haiku" in r.model.lower(), r.model
    print(f"{key}: model={r.model} duration={r.duration_s:.1f}s")


@pytest.mark.parametrize("key", ["d1_delivery", "d2_learning"])
def test_clear_dictations_are_ready(raw_results, key):
    fields = parse_brief_json(raw_results[key].text)
    for core in ("goal", "scope", "done_when"):
        assert "NEEDS CLARIFICATION" not in fields[core].upper() or re.search(
            r"\w", re.sub(r"\[NEEDS CLARIFICATION:[^\]]*\]", "", fields[core], flags=re.I)
        ), f"{key}.{core} is only a tag: {fields[core]!r}"


def test_vague_dictation_is_labelled_not_ready():
    out = make_brief(DICTATIONS["d3_vague"], "brief", backend="claude-cli", timeout=60)
    assert out.llm_ok, out.reason
    assert out.not_ready is True
    assert out.text.startswith("NOT READY")


def test_stated_constraints_survive_into_non_goals(raw_results):
    non_goals = parse_brief_json(raw_results["d1_delivery"].text)["non_goals"].lower()
    assert "push" in non_goals
    assert "main dictation" in non_goals or "dictation path" in non_goals


def test_learning_dictation_is_classed_learning_and_keeps_no_code_change(raw_results):
    fields = parse_brief_json(raw_results["d2_learning"].text)
    assert fields["goal_type"] == "learning"
    assert "code" in fields["non_goals"].lower()


@pytest.mark.parametrize("key", list(DICTATIONS))
def test_no_invented_file_or_test_names_in_done_when(raw_results, key):
    done_when = parse_brief_json(raw_results[key].text)["done_when"]
    invented = [t for t in _FILEISH.findall(done_when) if t not in DICTATIONS[key]]
    assert invented == [], f"{key}: invented {invented} in {done_when!r}"
