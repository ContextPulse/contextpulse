# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The screen context sent with a dictation must not carry a secret.

clean_with_llm sends two things: the dictation, and a profile_context built
from recent window titles and known terms. The dictation is guarded (a
secret-bearing dictation is not sent at all; see test_voice_llm_egress.py).
The context had no guard. A window title can hold anything on screen -- a
document name, a mail subject, a terminal command -- and the fix-last hotkey
sends it on every use.

Unlike the dictation, the context is never pasted back to the user, so
redact-then-send is the right handling here: the request goes out with the
secret replaced by a marker.

Every value below is SYNTHETIC.
"""

from unittest.mock import MagicMock, patch

import pytest

CONTROL_WORD = "zqprofilecontrol"
DICTATION = "please summarise the notes from this morning"

CONTEXTS = [
    ("password_phrase", "the password: zqprofileneedle77", "zqprofileneedle77"),
    ("ssn", "employee record 987-65-4321", "987-65-4321"),
]
IDS = [c[0] for c in CONTEXTS]


def _fake_anthropic(captured: list[dict]) -> MagicMock:
    """An anthropic module stand-in that records every outbound request."""
    module = MagicMock()

    def create(**kwargs: object) -> MagicMock:
        captured.append(kwargs)
        reply = MagicMock()
        reply.text = DICTATION.capitalize() + "."
        response = MagicMock()
        response.content = [reply]
        return response

    module.Anthropic.return_value.messages.create.side_effect = create
    return module


def _send(profile_context: str) -> tuple[list[dict], str]:
    import contextpulse_voice.cleanup as cleanup

    captured: list[dict] = []
    with (
        patch.dict("sys.modules", {"anthropic": _fake_anthropic(captured)}),
        patch("contextpulse_voice.config.get_api_key", return_value="zqfake-key-0123456789"),
    ):
        out = cleanup.clean_with_llm(DICTATION, profile_context=profile_context)
    return captured, out


@pytest.mark.parametrize("family,secret_text,needle", CONTEXTS, ids=IDS)
def test_profile_context_secret_is_redacted_before_sending(
    family: str, secret_text: str, needle: str
) -> None:
    context = f"Recent windows: {CONTROL_WORD} report, {secret_text}"
    captured, _ = _send(context)

    assert len(captured) == 1, f"{family}: expected one request, got {len(captured)}"
    body = str(captured[0]["messages"])
    assert needle not in body, f"{family}: the secret left the machine in the context"
    assert CONTROL_WORD in body, f"{family}: the context was dropped, not redacted"
    assert "[REDACTED" in body, f"{family}: no redaction marker in the request"


def test_ordinary_context_is_sent_unchanged() -> None:
    """The guard must not strip context that carries nothing sensitive."""
    context = f"Recent windows: {CONTROL_WORD} quarterly plan. Known terms: ContextPulse"
    captured, out = _send(context)

    assert len(captured) == 1
    assert context in str(captured[0]["messages"])
    assert out == DICTATION.capitalize() + "."
