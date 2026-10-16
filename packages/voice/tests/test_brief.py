"""Brief mode: the model fills fields, code decides the layout, and the dictation is never lost."""

import json

import pytest
from contextpulse_voice.brief import BriefOutcome, make_brief, parse_brief_json, render_brief
from contextpulse_voice.brief_templates import (
    BRIEF_SYSTEM_PROMPT,
    DEFAULT_AUTONOMY,
    DEFAULT_REPORT,
    REPLY_SYSTEM_PROMPT,
)
from contextpulse_voice.llm_backend import LLMResult

CLEAR = {
    "goal_type": "delivery",
    "goal": "Make Fix-last record whether cleanup really ran.",
    "why": "The stored flag says True when the LLM call failed.",
    "scope": "The fix-last path in voice_module.py.",
    "non_goals": "Do not push. Do not touch the main dictation path.",
    "done_when": "A test that fails today passes: cleanup_applied is False when the call fails.",
    "anti_gaming": "Do not set the flag from the request; set it from the reply.",
    "autonomy": "",
    "report": "",
    "needs_clarification": [],
}

VAGUE = {
    "goal_type": "delivery",
    "goal": "[NEEDS CLARIFICATION: what outcome]",
    "why": "[NEEDS CLARIFICATION: why]",
    "scope": "[NEEDS CLARIFICATION: which files]",
    "non_goals": "",
    "done_when": "[NEEDS CLARIFICATION: a runnable check, such as a test that passes]",
    "anti_gaming": "",
    "autonomy": "Decide anything.",
    "report": "Just say done.",
    "needs_clarification": ["what outcome", "why", "which files", "a runnable check"],
}


def fake_complete(text="", ok=True, error=""):
    calls = []

    def _complete(system_prompt, user_text, **kwargs):
        calls.append({"system": system_prompt, "user": user_text, **kwargs})
        return LLMResult(ok=ok, text=text, backend="claude-cli", error=error)

    _complete.calls = calls
    return _complete


class TestParse:
    def test_plain_json(self):
        assert parse_brief_json(json.dumps(CLEAR))["goal"] == CLEAR["goal"]

    def test_fenced_json(self):
        raw = "```json\n" + json.dumps(CLEAR) + "\n```"
        assert parse_brief_json(raw)["scope"] == CLEAR["scope"]

    @pytest.mark.parametrize("raw", [
        "", "not json", "[1, 2]",
        json.dumps({k: v for k, v in CLEAR.items() if k != "done_when"}),
        json.dumps({**CLEAR, "goal": 5}),
        json.dumps({**CLEAR, "needs_clarification": "one string"}),
    ])
    def test_malformed_raises_value_error(self, raw):
        with pytest.raises(ValueError):
            parse_brief_json(raw)


class TestRender:
    def test_clear_brief_is_ready_and_carries_every_field(self):
        text, not_ready = render_brief(CLEAR)
        assert not_ready is False
        assert "NOT READY" not in text
        for key in ("goal", "why", "scope", "non_goals", "done_when", "anti_gaming"):
            assert CLEAR[key] in text, key
        assert text.splitlines()[0].startswith("Goal (delivery):")

    def test_unsaid_autonomy_and_report_are_filled_and_labelled_default(self):
        text, _ = render_brief(CLEAR)
        assert f"Autonomy (default): {DEFAULT_AUTONOMY}" in text
        assert f"Report (default): {DEFAULT_REPORT}" in text

    def test_spoken_autonomy_is_not_labelled_default(self):
        text, _ = render_brief(VAGUE)
        assert "Autonomy: Decide anything." in text
        assert "Autonomy (default)" not in text

    def test_brief_with_only_tags_in_core_fields_is_not_ready(self):
        text, not_ready = render_brief(VAGUE)
        assert not_ready is True
        assert text.splitlines()[0].startswith("NOT READY")

    @pytest.mark.parametrize("field", ["goal", "scope", "done_when"])
    def test_any_one_core_field_being_only_a_tag_makes_it_not_ready(self, field):
        _, not_ready = render_brief({**CLEAR, field: "[NEEDS CLARIFICATION: x]"})
        assert not_ready is True

    def test_tag_inside_real_text_is_still_ready(self):
        _, not_ready = render_brief({**CLEAR, "scope": "voice_module.py [NEEDS CLARIFICATION: also tests?]"})
        assert not_ready is False

    def test_guesses_are_listed(self):
        text, _ = render_brief(VAGUE)
        assert "- which files" in text

    def test_learning_brief_omits_anti_gaming(self):
        text, _ = render_brief({**CLEAR, "goal_type": "learning", "anti_gaming": ""})
        assert "Anti-gaming" not in text
        assert text.startswith("Goal (learning):")


class TestMakeBrief:
    DICTATION = "Fix the fix last flag so it says whether cleanup ran. Don't push."

    def test_brief_success_pastes_rendered_brief(self):
        fc = fake_complete(json.dumps(CLEAR))
        out = make_brief(self.DICTATION, "brief", complete_fn=fc)
        assert out.llm_ok is True and out.template == "brief" and out.reason == ""
        assert CLEAR["goal"] in out.text
        assert fc.calls[0]["system"] == BRIEF_SYSTEM_PROMPT
        assert fc.calls[0]["user"] == "Dictation:\n" + self.DICTATION

    def test_not_ready_is_reported_on_the_outcome(self):
        out = make_brief(self.DICTATION, "brief", complete_fn=fake_complete(json.dumps(VAGUE)))
        assert out.llm_ok is True and out.not_ready is True

    def test_reply_pastes_model_text(self):
        fc = fake_complete("Did you push? You should not have.")
        out = make_brief("did you push because you shouldnt have", "reply", complete_fn=fc)
        assert out.text == "Did you push? You should not have."
        assert fc.calls[0]["system"] == REPLY_SYSTEM_PROMPT

    def test_verbatim_makes_no_call(self):
        fc = fake_complete("SHOULD NOT BE USED")
        out = make_brief(self.DICTATION, "verbatim", complete_fn=fc)
        assert out == BriefOutcome(text=self.DICTATION, template="verbatim", llm_ok=False,
                                   reason="verbatim", not_ready=False)
        assert fc.calls == []

    @pytest.mark.parametrize("template,fc,fragment", [
        ("brief", fake_complete(ok=False, error="claude-cli timeout after 30s"), "timeout"),
        ("brief", fake_complete("not json at all"), "not a valid brief"),
        ("reply", fake_complete(""), "empty"),
        ("reply", fake_complete("I'd be happy to help with that!"), "conversational"),
        ("reply", fake_complete(ok=False, error="claude-cli exit 1"), "exit 1"),
    ])
    def test_every_failure_pastes_the_dictation_and_says_why(self, template, fc, fragment):
        out = make_brief(self.DICTATION, template, complete_fn=fc)
        assert out.text == self.DICTATION
        assert out.llm_ok is False
        assert fragment in out.reason

    def test_unknown_template_pastes_dictation_without_a_call(self):
        fc = fake_complete(json.dumps(CLEAR))
        out = make_brief(self.DICTATION, "sonnet-of-the-day", complete_fn=fc)
        assert out.text == self.DICTATION and out.llm_ok is False
        assert "unknown template" in out.reason
        assert fc.calls == []

    def test_dictation_with_a_secret_is_never_sent(self):
        fc = fake_complete(json.dumps(CLEAR))
        secret = "set the key to sk-ant-api03-" + "A" * 90 + " and deploy"
        out = make_brief(secret, "brief", complete_fn=fc)
        assert fc.calls == [], "a secret-bearing dictation must not leave the machine"
        assert out.text == secret and out.llm_ok is False
        assert "secret" in out.reason

    def test_backend_model_and_timeout_are_passed_through(self):
        fc = fake_complete(json.dumps(CLEAR))
        make_brief(self.DICTATION, "brief", complete_fn=fc, backend="claude-cli",
                   model="haiku", timeout=30.0, api_key="k")
        call = fc.calls[0]
        assert (call["backend"], call["model"], call["timeout"], call["api_key"]) == (
            "claude-cli", "haiku", 30.0, "k")

    def test_empty_dictation_makes_no_call(self):
        fc = fake_complete(json.dumps(CLEAR))
        out = make_brief("   ", "brief", complete_fn=fc)
        assert fc.calls == [] and out.llm_ok is False
