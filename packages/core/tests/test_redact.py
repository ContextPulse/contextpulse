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
    # Added after the adversarial review (S3): sixteen shapes the table missed,
    # two of which the module docstring already claimed to cover.
    (
        "openssh_private_key",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nzqopensshneedle0123456789abcdef\n"
        "-----END OPENSSH PRIVATE KEY-----",
        "PRIVATE_KEY",
    ),
    (
        "ec_private_key",
        "-----BEGIN EC PRIVATE KEY-----\nzqecneedle0123456789abcdef\n-----END EC PRIVATE KEY-----",
        "PRIVATE_KEY",
    ),
    (
        "generic_private_key",
        "-----BEGIN PRIVATE KEY-----\nzqgenericneedle0123456789abc\n-----END PRIVATE KEY-----",
        "PRIVATE_KEY",
    ),
    ("https_userinfo", "https://admin:zqhttpsneedle44@internal.invalid/panel", "CONN_STRING"),
    ("ssh_userinfo", "ssh://deploy:zqsshneedle55@host.invalid", "CONN_STRING"),
    ("http_basic", "Authorization: Basic enFiYXNpY25lZWRsZTAxMjM0NTY3", "BASIC_AUTH"),
    ("slack_bot_token", "xoxb-1234567890-zqslackneedle0123456789", "SLACK_TOKEN"),
    ("slack_app_token", "xapp-1-A0ZQSLACKAPP-zqslackappneedle0123", "SLACK_TOKEN"),
    ("stripe_live_key", "sk_live_zqstripeneedle0123456789", "STRIPE_KEY"),
    ("stripe_restricted_key", "rk_live_zqstriperestricted0123456", "STRIPE_KEY"),
    # AIza + exactly 35, npm_ + exactly 36 -- the real vendor lengths.
    ("google_api_key", "AIzaZqGoogleNeedle0123456789abcdefghijk", "GOOGLE_KEY"),
    ("npm_token", "npm_zqnpmneedle0123456789abcdefghijklmno", "NPM_TOKEN"),
    ("github_fine_grained_pat", "github_pat_zqfinegrainedneedle0123456789", "GH_TOKEN"),
    ("github_user_token", "ghu_zqgithubuserneedle0123456789abcdefgh", "GH_TOKEN"),
    ("twilio_sid", "AC0123456789abcdef0123456789abcdef", "TWILIO_SID"),
    ("amex_15_digit", "3782 822463 10005", "CC"),
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
    "openssh_private_key": "zqopensshneedle0123456789abcdef",
    "ec_private_key": "zqecneedle0123456789abcdef",
    "generic_private_key": "zqgenericneedle0123456789abc",
    "https_userinfo": "zqhttpsneedle44",
    "ssh_userinfo": "zqsshneedle55",
    "http_basic": "enFiYXNpY25lZWRsZTAxMjM0NTY3",
    "slack_bot_token": "zqslackneedle0123456789",
    "slack_app_token": "zqslackappneedle0123",
    "stripe_live_key": "sk_live_zqstripeneedle0123456789",
    "stripe_restricted_key": "rk_live_zqstriperestricted0123456",
    "google_api_key": "AIzaZqGoogleNeedle0123456789abcdefghijk",
    "npm_token": "npm_zqnpmneedle0123456789abcdefghijklmno",
    "github_fine_grained_pat": "github_pat_zqfinegrainedneedle0123456789",
    "github_user_token": "ghu_zqgithubuserneedle0123456789abcdefgh",
    "twilio_sid": "AC0123456789abcdef0123456789abcdef",
    "amex_15_digit": "3782 822463 10005",
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
            "slack_bot_token", "stripe_live_key", "google_api_key", "npm_token",
            "github_fine_grained_pat", "github_user_token", "twilio_sid",
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
            # Added with the S3 widening -- each new vendor pattern is a new
            # chance to over-match ordinary text.
            "ACCOUNTS RECEIVABLE summary",          # AC..., not 32 hex
            "the basic plan costs less",            # "basic" without base64
            "npm_modules is not a real directory",  # npm_ with a short tail
            "see https://docs.invalid/guide:2 now",  # colon in a path, no userinfo
            "run ssh://host.invalid/repo.git",      # scheme, no userinfo at all
            "invoice 378282246310 filed",           # 12 digits, not an Amex
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


class TestSecondReviewFalsePositives:
    """Two patterns rewrote ordinary English; both were reproduced by review S-6.

    "Basic responsibilities of the role" became "[REDACTED:BASIC_AUTH] of the
    role" because any 16+-letter word satisfied the base64 character class, and
    "task-oriented-dialogue-system-evaluation" became "ta[REDACTED:API_KEY]"
    because the glued sk- rule asked only for 32 characters of
    [A-Za-z0-9_-] -- which a hyphenated phrase supplies.

    Over-redaction is not cosmetic here: the startup sweep REWRITES the stored
    row, so a false positive is permanent.
    """

    @pytest.mark.parametrize(
        "benign",
        [
            # Reproduced verbatim from the review.
            "Basic responsibilities of the role",
            "the task-oriented-dialogue-system-evaluation suite",
            # Same shape, so a fix that special-cases the two reported strings
            # rather than the pattern fails here.
            "Basic Responsibilities Of The Role",
            "basic understanding of distributed systems",
            "Basic authentication documentation index",
            "risk-weighted-capital-adequacy-assessment",
            "desk-reservation-system-integration-guide",
            "disk-encryption-configuration-instructions",
        ],
    )
    def test_ordinary_text_survives(self, benign):
        assert redact_sensitive(benign) == benign, f"over-redacted: {benign!r}"

    def test_http_basic_still_matches(self):
        """The negative cases must not have been bought by disabling the rule."""
        cleaned, counts = redact_with_counts(
            "Authorization: Basic enFiYXNpY25lZWRsZTAxMjM0NTY3"
        )
        assert "enFiYXNpY25lZWRsZTAxMjM0NTY3" not in cleaned
        assert counts.get("BASIC_AUTH") == 1

    def test_http_basic_without_a_digit_still_matches(self):
        """Discriminator is base64 SHAPE, not the presence of a digit.

        "dXNlcjpwYXNzd29yZA==" is base64("user:password") and carries no digit
        at all; what marks it is the internal lowercase->uppercase flip and the
        "=" padding.
        """
        assert "dXNlcjpwYXNzd29yZA" not in redact_sensitive(
            "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        )

    def test_glued_sk_key_still_matches(self):
        secret = "sk-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
        assert secret[3:] not in redact_sensitive(f"deploynotesx{secret} trailing")


class TestCardNumbersAreLuhnChecked:
    """A 16-digit run is not a card number. A 16-digit run that passes Luhn is.

    The CC pattern was pure shape -- four groups of four digits -- so any long
    identifier or concatenated date was rewritten as [REDACTED:CC]. Every card
    network issues numbers with a Luhn check digit and a registered IIN prefix,
    which is what Presidio's CreditCardRecognizer validates and what the
    prior-art pass measured at 10/10 against six real test PANs and four benign
    digit runs.

    The trade, named so it is not a surprise: a card whose digits were MISREAD
    by OCR now fails Luhn and is no longer redacted. Presidio makes the same
    trade; the alternative is scrubbing every invoice number on screen.

    Every PAN below is a published network TEST number, not a real card.
    """

    VALID_PANS = [
        ("visa", "4111111111111111"),
        ("visa_spaced", "4111 1111 1111 1111"),
        ("visa_hyphenated", "4111-1111-1111-1111"),
        ("mastercard", "5555555555554444"),
        ("mastercard_2series", "2223003122003222"),
        ("amex", "378282246310005"),
        ("amex_spaced", "3782 822463 10005"),
        ("discover", "6011111111111117"),
    ]

    @pytest.mark.parametrize(
        "family,pan", VALID_PANS, ids=[p[0] for p in VALID_PANS]
    )
    def test_a_real_test_card_is_still_redacted(self, family, pan):
        cleaned, counts = redact_with_counts(f"card {pan} on file")
        assert pan not in cleaned, f"{family}: a valid PAN stopped being redacted"
        assert counts.get("CC") == 1

    @pytest.mark.parametrize(
        "benign",
        [
            # Reported by the coordinator: a concatenated date-plus-counter.
            "build 2026091912345678",
            # One digit changed from the Visa test PAN, so the IIN is still
            # valid and only the checksum fails -- this is the case a
            # prefix-only check would miss.
            "order4111111111111112",
            # Valid Luhn is not enough either -- no network issues a 9xxx IIN.
            # (Constructed from the Visa test PAN by changing the leading digit
            # and rebalancing the check digit, so the checksum really does pass;
            # the test below asserts that rather than assuming it.)
            "ref 9111111111111110 filed",
            # Ordinary 16-digit identifiers.
            "session 1234567890123456 expired",
        ],
    )
    def test_a_digit_run_that_is_not_a_card_survives(self, benign):
        assert redact_sensitive(benign) == benign, f"over-redacted: {benign!r}"

    def test_the_checksum_and_the_prefix_are_both_required(self):
        """Neither gate alone explains the negatives above, so both are pinned.

        "4111111111111112" has a valid Visa IIN and a broken checksum;
        "9111111111111110" has a valid checksum and no issuer prefix. If either
        check were dropped, one of these would start being redacted again.
        """
        from contextpulse_core.redact import _has_card_iin, _luhn_checksum

        # The mechanism, asserted rather than assumed: if "9111111111111110"
        # did not actually pass Luhn, this class would prove nothing about the
        # IIN check and the docstring above would be false.
        assert _has_card_iin("4111111111111112") is True
        assert _luhn_checksum("4111111111111112") != 0
        assert _luhn_checksum("9111111111111110") == 0
        assert _has_card_iin("9111111111111110") is False

        for not_a_card in ("4111111111111112", "9111111111111110"):
            assert not_a_card in redact_sensitive(f"id {not_a_card} here")


class TestUnterminatedPrivateKeyHeader:
    """Review S-7: a BEGIN armour with no END matched nothing at all.

    OCR of a scrolled terminal, a clipboard cut at the 10,000-character limit
    and a screenshot of the top half of a key all produce a header plus body
    and no footer. The paired pattern requires the footer, so the visible key
    material was stored verbatim.
    """

    # Synthetic: valid base64 characters, not a real key.
    UNTERMINATED = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz\n"
        "c3Fwcml2YXRlbmVlZGxlMDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3\n"
    )

    def test_header_without_end_armour_is_redacted(self):
        cleaned, counts = redact_with_counts("pasted:\n" + self.UNTERMINATED)
        assert "PRIVATE_KEY" in counts, "the truncated key matched nothing"
        assert "BEGIN OPENSSH PRIVATE KEY" not in cleaned
        assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" not in cleaned, (
            "the header was marked but the key material was left on disk"
        )
        assert "pasted:" in cleaned, "context before the key was destroyed"

    def test_bare_header_with_no_body_is_redacted(self):
        assert "PRIVATE KEY" not in redact_sensitive("-----BEGIN PRIVATE KEY-----")

    def test_prose_after_a_truncated_key_survives(self):
        cleaned = redact_sensitive(
            self.UNTERMINATED + "then I closed the terminal window."
        )
        assert "then I closed the terminal window." in cleaned, (
            "the unterminated rule swallowed everything to end-of-text"
        )

    def test_a_complete_block_still_counts_once(self):
        """The new rule runs after the paired one, so a normal key is not
        matched twice and its count stays honest."""
        cleaned, counts = redact_with_counts(
            "-----BEGIN RSA PRIVATE KEY-----\nzqpairedneedle0123456789abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        )
        assert counts["PRIVATE_KEY"] == 1
        assert "zqpairedneedle0123456789abcdef" not in cleaned


class TestBareAwsSecretIsContextual:
    """A bare AWS secret is 40 base64 characters with no prefix and no label.

    Redacting every 40-character run would scrub hashes, git object ids and
    base64 lines out of OCR text wholesale. The console's copy button gives you
    the value alone, but it is pasted next to the access key id it belongs to,
    so the pairing is the signal.
    """

    SECRET_40 = "zqAWSbareNeedle0123456789abcdefGHIJKLMNO"
    AKIA = "AKIAZQCONTEXTNEEDL01"

    def test_redacted_when_an_akia_is_present(self):
        text = f"{self.AKIA}\n{self.SECRET_40}\n"
        cleaned, counts = redact_with_counts(text)
        assert self.SECRET_40 not in cleaned, "bare secret survived next to its key id"
        assert counts.get("AWS_SECRET") == 1
        assert counts.get("AWS_KEY") == 1

    def test_not_redacted_on_its_own(self):
        # Exactly the same 40 characters, no AKIA anywhere. Must survive, or
        # the pattern is a general 40-character shredder.
        text = f"blob {self.SECRET_40} committed"
        assert redact_sensitive(text) == text

    def test_a_sha1_next_to_an_akia_is_collateral_and_accepted(self):
        # Honest about the cost: with an AKIA in scope, ANY 40-character
        # base64-ish run in the same text is redacted, including a git SHA-1.
        # Pinned so the trade-off is visible rather than discovered later.
        sha = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        cleaned = redact_sensitive(f"{self.AKIA} at commit {sha}")
        assert sha not in cleaned

    def test_the_same_sha1_alone_is_untouched(self):
        sha = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        assert redact_sensitive(f"at commit {sha}") == f"at commit {sha}"


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
