# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Window titles: redact real secrets, keep ordinary titles.

Measured 2026-11-16 on a live activity.db: titles held AWS_KEY 2 and
AWS_SECRET 2 matches, stored raw, because title redaction was skipped
wholesale -- the CREDENTIAL rule fires on ordinary titles like
"Change password: settings" (58 of 60 flagged titles). The fix redacts every
category EXCEPT CREDENTIAL in titles.

Every value below is SYNTHETIC.
"""

import pytest
from contextpulse_core.redact import (
    TITLE_SKIP_CATEGORIES,
    redact_title,
    redact_title_with_counts,
    redact_with_counts,
)

AWS_KEY = "AKIAZQTITLENEEDLE123"
AWS_SECRET = "zqTitleSecret0123456789abcdefghijKLMNOPQ"  # 40 chars
GH_TOKEN = "ghp_zqtitleneedle0123456789abcdefghijklm"
STRIPE = "sk_live_zqtitleneedle0123456789"


def test_fixture_secret_is_forty_characters():
    assert len(AWS_SECRET) == 40


@pytest.mark.parametrize(
    "title, secret, category",
    [
        (f"{AWS_KEY} - Notepad", AWS_KEY, "AWS_KEY"),
        (f"keys.txt {AWS_KEY} {AWS_SECRET} - Notepad", AWS_SECRET, "AWS_SECRET"),
        (f"{GH_TOKEN} - Windows Terminal", GH_TOKEN, "GH_TOKEN"),
        (f"Stripe {STRIPE} - Chrome", STRIPE, "STRIPE_KEY"),
    ],
)
def test_a_real_secret_in_a_title_is_redacted(title, secret, category):
    cleaned, counts = redact_title_with_counts(title)
    assert secret not in cleaned
    assert counts.get(category, 0) >= 1
    assert redact_title(title) == cleaned


@pytest.mark.parametrize(
    "title",
    [
        "Change password: settings - Google Chrome",
        "token: refresh flow notes.md - Visual Studio Code",
        "api_key: rotation runbook - Notion",
    ],
)
def test_an_ordinary_credential_word_title_is_kept(title):
    # Control: the full redaction DOES rewrite it, so the test is not vacuous.
    full, full_counts = redact_with_counts(title)
    assert "CREDENTIAL" in full_counts and full != title

    cleaned, counts = redact_title_with_counts(title)
    assert cleaned == title
    assert counts == {}


def test_the_rest_of_the_title_is_kept():
    cleaned = redact_title(f"zqcontrol {AWS_KEY} - Notepad")
    assert cleaned.startswith("zqcontrol ")
    assert cleaned.endswith(" - Notepad")


def test_counts_never_report_a_skipped_category():
    _, counts = redact_title_with_counts(f"password: zqsecretvalue {AWS_KEY}")
    assert set(counts).isdisjoint(TITLE_SKIP_CATEGORIES)
    assert counts.get("AWS_KEY") == 1


@pytest.mark.parametrize("value", ["", None])
def test_empty_titles_pass_through(value):
    assert redact_title(value) == value
    assert redact_title_with_counts(value) == (value, {})


def test_full_redaction_is_unchanged():
    cleaned, counts = redact_with_counts("password: zqsecretvalue")
    assert "zqsecretvalue" not in cleaned
    assert counts == {"CREDENTIAL": 1}


@pytest.mark.parametrize("value", [42, b"AKIAZQTITLENEEDLE123"])
def test_a_non_string_passes_through_rather_than_raising(value):
    assert redact_title(value) == value
