# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""1Password service-account tokens must not survive screen OCR.

The vendored gitleaks rule (``1password-service-account-token``) wants
``ops_eyJ`` followed by 250+ base64 characters with no break. Screen OCR does
not produce that: a terminal wraps a ~850-character token over several lines,
and OCR reads a character wrong now and then. On 2026-11-11 a token shown in
Windows Terminal reached activity.db as ``ops_ey.`` plus ~30 characters, which
no rule matched (cp-redactor-misses-1password-sa-token-ocr).

Every value here is SYNTHETIC, and the prefix is assembled at import time so no
committed blob carries a string a secret scanner would flag.
"""

import random
import string

import pytest
from contextpulse_core.redact import redact_sensitive, redact_with_counts

PREFIX = "ops" + "_" + "eyJ"
_B64 = string.ascii_letters + string.digits + "+/"


def _body(n: int, seed: int = 7) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(_B64) for _ in range(n))


TOKEN = PREFIX + _body(840)


def _wrapped(token: str, width: int) -> str:
    return "\n".join(token[i : i + width] for i in range(0, len(token), width))


def _no_token_material_left(out: str, token: str) -> bool:
    """No 12-character slice of the token survives anywhere in the output."""
    flat = out.replace("\n", "")
    return not any(token[i : i + 12] in flat for i in range(0, len(token) - 12, 6))


def test_whole_token_on_one_line_is_redacted():
    out = redact_sensitive(f"export OP_SERVICE_ACCOUNT_TOKEN_VALUE {TOKEN} done")
    assert "[REDACTED:OP_TOKEN]" in out
    assert _no_token_material_left(out, TOKEN)
    assert out.endswith(" done")


def test_token_glued_to_a_word_by_ocr_is_redacted():
    out = redact_sensitive(f"value:{TOKEN}")
    assert _no_token_material_left(out, TOKEN)


@pytest.mark.parametrize("width", [80, 120])
def test_token_wrapped_across_terminal_lines_is_redacted_in_full(width):
    text = f"PS C:\\> op whoami\n{_wrapped(TOKEN, width)}\nPS C:\\> next command"
    out = redact_sensitive(text)
    assert _no_token_material_left(out, TOKEN), out[:200]
    assert out.startswith("PS C:\\> op whoami\n")
    assert out.endswith("\nPS C:\\> next command")


def test_only_a_short_final_wrap_line_can_survive():
    # Known limit: a last wrapped line under 20 characters is not taken as part
    # of the token (a short line is too easily an ordinary word). With an
    # 847-character token at width 120 that leaves 7 characters, not a usable
    # credential. Pin the limit so widening or narrowing it is a decision.
    out = redact_sensitive(_wrapped(TOKEN, 120))
    survivors = [line for line in out.split("\n") if line and "[REDACTED" not in line]
    assert survivors == [TOKEN[-(len(TOKEN) % 120):]]
    assert len(survivors[0]) < 20


def test_ocr_misread_of_the_j_still_redacts():
    # The 2026-11-11 row: OCR read "eyJ" as "ey." and kept ~30 characters.
    misread = "ops" + "_ey." + "Jhb3Rh" + _body(26, seed=3) + ":"
    out = redact_sensitive(f"token {misread} expires")
    assert "ops_ey" not in out
    assert out.endswith(" expires")


def test_short_ocr_fragment_is_counted_under_its_own_category():
    _, counts = redact_with_counts(f"x {PREFIX}{_body(40)} y")
    assert counts.get("OP_TOKEN") == 1


@pytest.mark.parametrize(
    "text",
    [
        "devops_eyes_on_the_deployment_dashboard",  # identifier, no digits or capitals
        "stops_eye_tracking_calibration_routine",
        "ops_ey",  # bare prefix
        "ops_eyJ123",  # too short to be a token
        "see the ops_eyebrow_notes_for_tomorrow file",
    ],
)
def test_ordinary_words_are_left_alone(text):
    assert redact_sensitive(text) == text


def test_a_long_word_line_after_a_token_is_not_swallowed():
    text = f"{TOKEN}\nconfiguration_management_module_settings\n"
    out = redact_sensitive(text)
    assert "configuration_management_module_settings" in out
