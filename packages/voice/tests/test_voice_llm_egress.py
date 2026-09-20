# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Captured text must not leave the machine carrying a secret.

Two paths in the voice package call the Anthropic API with the user's own
words: clean_with_llm (one dictation, to polish it) and analyze_with_llm (up
to 50 raw/cleaned transcript pairs, to learn mishearings). Storage redaction
says nothing about either -- they read the in-memory text before it is stored.

These tests intercept the client at the boundary and assert the planted secret
appears NOWHERE in the outbound request, then assert the positive control does
appear, so "the secret is absent" is a statement about redaction rather than
about a request that was never built.

Every value below is SYNTHETIC.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

CONTROL_WORD = "zqcontrol"

SECRETS = [
    ("password_phrase", f"{CONTROL_WORD} the password: zqegressneedle42 ok", "zqegressneedle42"),
    ("ssn", f"{CONTROL_WORD} social 987-65-4321 ok", "987-65-4321"),
    ("aws_key", f"{CONTROL_WORD} AKIAZQEGRESSNEEDLE01 ok", "AKIAZQEGRESSNEEDLE01"),
    ("stripe", f"{CONTROL_WORD} sk_live_zqegressstripe0123456789 ok", "sk_live_zqegressstripe0123456789"),
]

IDS = [s[0] for s in SECRETS]


def _fake_anthropic(captured):
    """An anthropic module stand-in that records every outbound request."""
    module = MagicMock()

    def create(**kwargs):
        captured.append(kwargs)
        reply = MagicMock()
        reply.text = "{}"
        response = MagicMock()
        response.content = [reply]
        return response

    module.Anthropic.return_value.messages.create.side_effect = create
    return module


class TestCleanWithLlmDoesNotSendSecrets:
    """A dictation carrying a secret is not sent at all.

    Redact-then-send is wrong HERE specifically: what this returns is pasted
    into the user's cursor, so sending the redacted copy would paste
    "[REDACTED:CREDENTIAL]" instead of what they said.
    """

    @pytest.mark.parametrize("family,text,needle", SECRETS, ids=IDS)
    def test_no_request_is_made(self, family, text, needle):
        import contextpulse_voice.cleanup as cleanup

        captured: list[dict] = []
        with (
            patch.dict("sys.modules", {"anthropic": _fake_anthropic(captured)}),
            patch("contextpulse_voice.config.get_api_key", return_value="sk-ant-zqfake0123456789abcd"),
        ):
            out = cleanup.clean_with_llm(text)

        assert captured == [], f"{family}: a request was sent carrying the dictation"
        # The user still gets their text -- rule-based cleanup, not a marker.
        assert needle in out, f"{family}: fell back but mangled the user's text: {out!r}"
        assert "[REDACTED" not in out, f"{family}: the pasted text was redacted"

    def test_an_ordinary_dictation_is_still_sent(self):
        """The guard must not disable the feature it protects."""
        import contextpulse_voice.cleanup as cleanup

        captured: list[dict] = []
        with (
            patch.dict("sys.modules", {"anthropic": _fake_anthropic(captured)}),
            patch("contextpulse_voice.config.get_api_key", return_value="sk-ant-zqfake0123456789abcd"),
        ):
            cleanup.clean_with_llm(f"{CONTROL_WORD} please fix this sentence")

        assert len(captured) == 1, "an ordinary dictation was not sent for cleanup"
        assert CONTROL_WORD in json.dumps(captured[0], default=str)


class TestAnalyzeWithLlmRedactsBeforeSending:
    """Here redact-then-send IS correct: the output is an analysis, not text
    that goes back to the user's cursor."""

    @pytest.mark.parametrize("family,text,needle", SECRETS, ids=IDS)
    def test_request_body_carries_no_secret(self, family, text, needle):
        from contextpulse_voice.analyzer import analyze_with_llm

        entries = [
            {"raw": f"{text} raw variant {i}", "cleaned": f"{text} cleaned variant {i}"}
            for i in range(12)
        ]

        captured: list[dict] = []
        with (
            patch.dict("sys.modules", {"anthropic": _fake_anthropic(captured)}),
            patch("contextpulse_voice.analyzer.get_api_key", return_value="sk-ant-zqfake0123456789abcd"),
        ):
            analyze_with_llm(entries)

        assert len(captured) == 1, f"{family}: expected exactly one request"
        body = json.dumps(captured[0], default=str)
        # Positive control: the transcripts really were included.
        assert CONTROL_WORD in body, f"{family}: nothing was sent -- assertion is vacuous"
        assert needle not in body, f"{family}: secret transmitted to the API"
        assert "[REDACTED" in body, f"{family}: nothing was redacted in the request"

    def test_redaction_precedes_the_200_char_slice(self):
        """A token the slice halves matches nothing; its head would be sent."""
        from contextpulse_voice.analyzer import analyze_with_llm

        secret = "ghp_zqegressboundary0123456789abcdefghijk"
        head = secret[:30]
        filler = "x" * (200 - len(CONTROL_WORD) - 2 - 30)
        text = f"{CONTROL_WORD} {filler} {secret} tail"

        entries = [
            {"raw": text, "cleaned": f"{text} differs {i}"} for i in range(12)
        ]
        captured: list[dict] = []
        with (
            patch.dict("sys.modules", {"anthropic": _fake_anthropic(captured)}),
            patch("contextpulse_voice.analyzer.get_api_key", return_value="sk-ant-zqfake0123456789abcd"),
        ):
            analyze_with_llm(entries)

        body = json.dumps(captured[0], default=str)
        assert CONTROL_WORD in body
        assert secret not in body
        assert head not in body, "the slice sent the leading half of a token"
