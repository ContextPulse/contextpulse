# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Pattern-level tests for the shared redaction engine.

Two properties, tested separately because they fail in opposite directions:

BARE      a secret surrounded by whitespace must be redacted. This is what the
          original \\b-anchored patterns already did, and these cases exist to
          prove the anchoring change did not cost any existing coverage.

GLUED     the same secret abutting a word character must ALSO be redacted.
          Every pattern used to be \\b-anchored on both ends, so "xghp_AAAA..."
          matched nothing while " ghp_AAAA..." matched -- and OCR routinely runs
          adjacent words together (cp-redact-patterns-word-boundary-gap).

NEGATIVE  and a third set: strings that must NOT be redacted, because the cure
          for an anchoring gap is over-redaction, and over-redaction on OCR text
          is how a product starts scrubbing the context it exists to keep.

Every value here is SYNTHETIC.
"""

import pytest
from contextpulse_core.redact import (
    _EXTRA_TEXT_PAYLOAD_KEYS,
    _redactable_payload_keys,
    redact_payload,
    redact_sensitive,
    redact_with_counts,
)
from contextpulse_core.spine.events import _TEXT_PAYLOAD_KEYS

# (family, the secret substring, expected category)
#
# Families whose pattern is keyword-anchored (CREDENTIAL, AWS_SECRET, BEARER,
# CONN_STRING, PRIVATE_KEY) are listed with the keyword included: the value
# alone was never a secret, so testing it bare would be testing a string that
# no pattern was ever meant to match.
SECRET_FAMILIES = [
    # A real AWS access key is AKIA + exactly 16 characters.
    ("aws_access_key", "AKIAZQGLUEDNEEDLE012", "AWS_KEY"),
    ("aws_secret_key", "aws_secret_access_key=zqawssecretglued0123456789abcd", "AWS_SECRET"),
    ("openai_style_key", "sk-zqopenaigluedneedle0123456789ABCDEFGHIJ", "API_KEY"),
    ("anthropic_style_key", "sk-ant-zqanthropicgluedneedle0123456789", "API_KEY"),
    ("github_ghp_token", "ghp_zqgithubgluedneedle0123456789abcdefghijklmn", "GH_TOKEN"),
    ("github_ghs_token", "ghs_zqgithubservergluedneedle0123456789abcdefgh", "GH_TOKEN"),
    ("github_gho_token", "gho_zqgithuboauthglued0123456789abcdefghijklmn", "GH_TOKEN"),
    ("password_colon", "password: zqpasswordglued99", "CREDENTIAL"),
    ("api_key_equals", "api_key=zqapikeyglued77", "CREDENTIAL"),
    (
        "jwt",
        "eyJzcXpxand0Z2x1ZWQwMTIzNDU.eyJzdWIiOiJ6cWp3dGJvZHlnbHVlZCJ9."
        "zqjwtsignatureglued0123456789",
        "JWT",
    ),
    ("bearer_token", "Bearer zqbearergluedneedle0123456789", "BEARER"),
    ("credit_card", "4111111111111111", "CC"),
    ("ssn", "987-65-4321", "SSN"),
    (
        "private_key",
        "-----BEGIN RSA PRIVATE KEY-----\nzqprivatekeyglued0123456789abcdef\n"
        "-----END RSA PRIVATE KEY-----",
        "PRIVATE_KEY",
    ),
    ("connection_string", "postgres://appuser:zqconnstrglued88@db.invalid:5432/app", "CONN_STRING"),
]

FAMILY_IDS = [f[0] for f in SECRET_FAMILIES]

# The needle inside each secret that must not survive. For keyword-anchored
# families the keyword itself is allowed to remain (the pattern replaces the
# whole match, so in practice it does not) -- what must vanish is the value.
NEEDLES = {
    "aws_access_key": "AKIAZQGLUEDNEEDLE012",
    "aws_secret_key": "zqawssecretglued0123456789abcd",
    "openai_style_key": "zqopenaigluedneedle0123456789ABCDEFGHIJ",
    "anthropic_style_key": "zqanthropicgluedneedle0123456789",
    "github_ghp_token": "zqgithubgluedneedle0123456789abcdefghijklmn",
    "github_ghs_token": "zqgithubservergluedneedle0123456789abcdefgh",
    "github_gho_token": "zqgithuboauthglued0123456789abcdefghijklmn",
    "password_colon": "zqpasswordglued99",
    "api_key_equals": "zqapikeyglued77",
    "jwt": "zqjwtsignatureglued0123456789",
    "bearer_token": "zqbearergluedneedle0123456789",
    "credit_card": "4111111111111111",
    "ssn": "987-65-4321",
    "private_key": "zqprivatekeyglued0123456789abcdef",
    "connection_string": "zqconnstrglued88",
}


class TestBareFormsAreRedacted:
    """Regression guard: the anchoring change must not lose existing coverage."""

    @pytest.mark.parametrize("family,secret,category", SECRET_FAMILIES, ids=FAMILY_IDS)
    def test_bare(self, family, secret, category):
        cleaned, counts = redact_with_counts(f"context before {secret} context after")
        assert NEEDLES[family] not in cleaned, f"{family}: bare form survived redaction"
        assert category in counts, f"{family}: matched, but under the wrong category: {counts}"
        assert "context before" in cleaned and "context after" in cleaned


class TestGluedFormsAreRedacted:
    """The finding: a secret abutting a word character used to match nothing."""

    # Families whose pattern is keyword-anchored cannot meaningfully be glued --
    # "xpassword: value" still contains "password:" and matched all along; the
    # gap was specific to the token families. They are covered by the bare set
    # and by the prefix-glue case below.
    TOKEN_FAMILIES = [
        f for f in SECRET_FAMILIES
        if f[0] in {
            "aws_access_key", "openai_style_key", "anthropic_style_key",
            "github_ghp_token", "github_ghs_token", "github_gho_token", "jwt",
        }
    ]
    TOKEN_IDS = [f[0] for f in TOKEN_FAMILIES]

    @pytest.mark.parametrize("family,secret,category", TOKEN_FAMILIES, ids=TOKEN_IDS)
    def test_glued_to_a_preceding_word_character(self, family, secret, category):
        # OCR shape: the previous word ran into the token with no space.
        cleaned = redact_sensitive(f"deploynotesx{secret} trailing")
        assert NEEDLES[family] not in cleaned, (
            f"{family}: token glued to a preceding word character survived -- "
            "this is the \\b-anchoring gap"
        )

    def test_numeric_families_glued_to_a_letter(self):
        # \b already blocked these: "order4111111111111111" is letter-then-digit,
        # which IS a boundary, so the card matched. The (?<!\d) form must keep it.
        assert "4111111111111111" not in redact_sensitive("order4111111111111111 shipped")
        assert "987-65-4321" not in redact_sensitive("ssn987-65-4321 filed")

    def test_an_aws_key_inside_a_longer_uppercase_run_is_redacted(self):
        # The old pattern was AKIA + exactly 16 + \b, so a 17th uppercase
        # character made the whole key invisible rather than partially matched.
        assert "AKIAZQLONGRUNNEEDLE01" not in redact_sensitive("AKIAZQLONGRUNNEEDLE01 x")

    def test_key_equals_token_is_redacted(self):
        # Cited in the finding. "=" is a non-word character so \b always allowed
        # this one; pinned so a future anchoring change cannot regress it.
        cleaned = redact_sensitive("key=sk-zqequalsneedle0123456789ABCDEFGH rest")
        assert "zqequalsneedle0123456789ABCDEFGH" not in cleaned


class TestBenignStringsAreNotRedacted:
    """Over-redaction is the failure mode the anchoring change could introduce."""

    @pytest.mark.parametrize(
        "benign",
        [
            # "sk-" lives inside ordinary hyphenated words. The bare sk- rule
            # keeps its anchor precisely so these survive.
            "task-assignment-notes",
            "risk-adjusted-return",
            "desk-setup-photos",
            "disk-usage-report",
            # A long digit run is an identifier, not a card number.
            "build 12345678901234567890 finished",
            # Ordinary prose that merely contains the letters.
            "the whisk-broom is in the basket",
        ],
    )
    def test_benign_survives(self, benign):
        assert redact_sensitive(benign) == benign, f"over-redacted: {benign!r}"

    def test_a_long_digit_run_is_not_a_card(self):
        # \b refused this and so must (?<!\d)/(?!\d) -- the guard against the
        # obvious "just drop the anchors" fix.
        _cleaned, counts = redact_with_counts("id 12345678901234567890")
        assert "CC" not in counts

    def test_task_prefixed_long_identifier_is_not_a_key(self):
        # The glued sk- rule needs 32+ characters. 31 must not fire.
        text = "task-" + "a" * 31
        assert redact_sensitive(text) == text


class TestPayloadKeyCoverage:
    """One declared key list, asserted against the spine's own tuple."""

    def test_covers_every_spine_text_key(self):
        keys = set(_redactable_payload_keys())
        missing = set(_TEXT_PAYLOAD_KEYS) - keys
        assert not missing, (
            f"spine text keys not covered by redaction: {sorted(missing)}. "
            "A key added to ContextEvent._TEXT_PAYLOAD_KEYS must be added to "
            "contextpulse_core.redact or it is stored unredacted."
        )

    def test_covers_the_non_indexed_text_keys(self):
        keys = set(_redactable_payload_keys())
        for key in _EXTRA_TEXT_PAYLOAD_KEYS:
            assert key in keys

    def test_redact_payload_scrubs_every_key(self):
        secret = "ghp_zqpayloadneedle0123456789abcdefghijklmn"
        payload = {key: f"zqcontrol {secret}" for key in _redactable_payload_keys()}
        payload["wpm"] = 65.0
        cleaned = redact_payload(payload)
        for key in _redactable_payload_keys():
            assert secret not in cleaned[key], f"{key} was not redacted"
            assert "zqcontrol" in cleaned[key], f"{key} lost its surrounding context"
        assert cleaned["wpm"] == 65.0

    def test_redact_payload_honours_extra_keys(self):
        secret = "sk-ant-zqextrakeyneedle0123456789"
        cleaned = redact_payload({"weird_field": secret}, extra_keys=("weird_field",))
        assert secret not in cleaned["weird_field"]

    def test_redact_payload_leaves_non_strings_alone(self):
        payload = {"transcript": ["not", "a", "string"], "count": 3}
        assert redact_payload(payload) == payload

    def test_redact_payload_does_not_mutate_the_input(self):
        secret = "AKIAZQNOMUTATENEEDLE1"
        original = {"transcript": secret}
        redact_payload(original)
        assert original["transcript"] == secret
