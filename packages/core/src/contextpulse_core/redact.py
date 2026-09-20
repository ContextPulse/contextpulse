# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Redact sensitive patterns from any captured text before it is stored.

Detects and masks common secret patterns:
  - API keys (sk-..., AKIA..., ghp_..., gho_..., etc.)
  - Passwords in common formats (password: ..., pwd=...)
  - Credit card numbers (16 digits)
  - SSN patterns (XXX-XX-XXXX)
  - JWT tokens (eyJ...)
  - Private keys (BEGIN PRIVATE KEY blocks)
  - Connection strings with credentials

Redaction replaces the sensitive portion with [REDACTED] while preserving
surrounding context for legitimate use.

WHY THIS LIVES IN core AND NOT IN contextpulse_sight
----------------------------------------------------
It started in ``contextpulse_sight.redact`` with one caller (the OCR worker).
Every other modality that stores text -- voice transcripts, typed bursts and
corrections from touch, the memory store, the knowledge bridge -- depends on
``contextpulse-core`` and does NOT depend on ``contextpulse-screen``. Importing
the sight package from those would have been an undeclared cross-package
dependency, and the alternative (a second copy of the pattern table) is the
failure this module exists to prevent: an audit or a modality quietly matching
a different, older set of patterns than the one capture uses.

``contextpulse_sight.redact`` is now a thin re-export so every existing import
path, and the OCR behaviour gated on ``redact_ocr_text``, are unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Each pattern is (compiled_regex, replacement)
#
# ANCHORING -- read before editing.
#
# Every pattern here was originally \b-anchored on BOTH ends. \b is a boundary
# between a word and a non-word character, so a token glued to a word character
# was not matched at all: " ghp_AAAA..." redacted, "xghp_AAAA..." did not. That
# is not a hypothetical -- OCR routinely runs adjacent words together, and a
# test fixture in this repo tripped over it (cp-redact-patterns-word-boundary-gap).
#
# The fix is per-family, not a blanket removal:
#
#   * Prefix-keyed token families (AKIA, ghp_/ghs_/gho_, eyJ, sk-ant-) carry no
#     leading anchor. Those prefixes do not occur inside ordinary words, so
#     dropping the anchor costs nothing and closes the glued case.
#   * Bare "sk-" DOES keep its anchor, because "task-", "risk-" and "desk-" all
#     contain it. Glued coverage is added as a SECOND, stricter pattern requiring
#     32+ characters -- long enough that a hyphenated English word with that
#     tail is an identifier, not prose. The original anchored 20+ rule is left
#     exactly as it was, so this is purely additive.
#   * Numeric families (card, SSN) use (?<!\d)/(?!\d) rather than \b. That is
#     strictly better in both directions: it matches "order4111111111111111"
#     (which \b missed) and still refuses to fire inside a 20-digit run (which
#     \b also refused, and which an unanchored pattern would have broken).
#   * CREDENTIAL is deliberately NOT loosened. On the live database 58 of 60
#     flagged window titles were ordinary titles containing "password:" or
#     "token:"; widening it would scrub benign context at scale.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # AWS access keys (AKIA...). Greedy tail so a longer uppercase run is
    # consumed whole rather than skipped for want of a trailing boundary.
    (re.compile(r"AKIA[0-9A-Z]{16,}"), "[REDACTED:AWS_KEY]"),
    # AWS secret keys (40-char base64-ish after = or :)
    (re.compile(r"(?i)(?:aws_secret|secret_access_key)\s*[:=]\s*\S{20,60}"), "[REDACTED:AWS_SECRET]"),

    # OpenAI / Anthropic API keys
    (re.compile(r"(?<![A-Za-z0-9])sk-[a-zA-Z0-9_-]{20,}"), "[REDACTED:API_KEY]"),
    # Glued form: no leading anchor, higher length bar (see ANCHORING above).
    (re.compile(r"sk-[a-zA-Z0-9_-]{32,}"), "[REDACTED:API_KEY]"),
    (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[REDACTED:API_KEY]"),

    # GitHub tokens
    (re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"), "[REDACTED:GH_TOKEN]"),
    (re.compile(r"gho_[a-zA-Z0-9]{36,}"), "[REDACTED:GH_TOKEN]"),

    # Generic "password", "secret", "token", "api_key" followed by value
    (re.compile(r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}"), "[REDACTED:CREDENTIAL]"),

    # JWT tokens (eyJ base64...)
    (re.compile(r"eyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}"), "[REDACTED:JWT]"),

    # Credit card numbers (16 digits, with or without separators)
    (re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"), "[REDACTED:CC]"),

    # SSN (XXX-XX-XXXX)
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "[REDACTED:SSN]"),

    # Private key blocks -- ANY armour type, not just RSA. OPENSSH and EC are
    # what `ssh-keygen` and `openssl ecparam` produce and are the likeliest
    # thing on a clipboard right after someone copies a key; the old pattern
    # allowed only "(RSA )?PRIVATE KEY" while the module docstring advertised
    # "BEGIN PRIVATE KEY blocks" generally.
    (re.compile(r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE\s+KEY-----[\s\S]*?-----END\s+[A-Z0-9 ]*PRIVATE\s+KEY-----"),
     "[REDACTED:PRIVATE_KEY]"),

    # Connection strings with passwords -- any scheme, not a fixed list of
    # four. https://admin:pw@host and ssh://user:pw@host leak the same way
    # postgres:// does. The userinfo character classes exclude "/" and "@" so
    # this cannot run across a path or a second URL.
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"), "[REDACTED:CONN_STRING]://***:***@"),

    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_.-]{20,}"), "[REDACTED:BEARER]"),
    # HTTP Basic -- the base64 decodes to user:password.
    (re.compile(r"(?i)basic\s+[A-Za-z0-9+/]{16,}={0,2}"), "[REDACTED:BASIC_AUTH]"),

    # Vendor token prefixes. Each is distinctive enough to need no anchor,
    # which also gives the glued case for free.
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:GH_TOKEN]"),
    (re.compile(r"ghu_[a-zA-Z0-9]{36,}"), "[REDACTED:GH_TOKEN]"),
    (re.compile(r"xox[abpres]-[A-Za-z0-9-]{10,}"), "[REDACTED:SLACK_TOKEN]"),
    (re.compile(r"xapp-[0-9]-[A-Za-z0-9-]{10,}"), "[REDACTED:SLACK_TOKEN]"),
    # Stripe uses an UNDERSCORE, so the sk- pattern never matched it.
    (re.compile(r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"), "[REDACTED:STRIPE_KEY]"),
    (re.compile(r"AIza[A-Za-z0-9_-]{35}"), "[REDACTED:GOOGLE_KEY]"),
    (re.compile(r"npm_[A-Za-z0-9]{36}"), "[REDACTED:NPM_TOKEN]"),
    # ContextPulse's own MCP access token. It carries this prefix
    # (contextpulse_core.mcp_auth.TOKEN_PREFIX) for exactly one reason: so this
    # table can recognise it. The Settings dialog renders the bare value with
    # no "Bearer " in front of it, and without this pattern a screenshot of
    # that dialog stored the live token in activity.db, readable back out
    # through get_screen_text and search_history -- the product capturing the
    # credential to the product.
    (re.compile(r"cpmcp_[A-Za-z0-9_-]{20,}"), "[REDACTED:CP_MCP_TOKEN]"),
    # Twilio account SID: AC + exactly 32 hex. The hex requirement is what
    # keeps this off ordinary words beginning "AC".
    (re.compile(r"AC[0-9a-f]{32}"), "[REDACTED:TWILIO_SID]"),

    # American Express is 15 digits, not 16, so the card pattern above misses
    # it entirely. Amex always starts 34 or 37, which keeps this off arbitrary
    # 15-digit runs.
    (re.compile(r"(?<!\d)3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}(?!\d)"), "[REDACTED:CC]"),
]


# Patterns that only fire when something ELSE in the same text says they should.
#
# A bare AWS secret access key is 40 characters of base64 with no prefix and no
# label -- the shape of a hash, a git blob id, a base64 line, or half a JWT.
# Redacting every 40-character run would scrub OCR text wholesale, which is the
# over-redaction failure this file works hard to avoid. But the AWS console's
# copy button gives you the value ALONE, and it is almost always pasted next to
# the access key id it belongs with.
#
# So: match it only when an AKIA-shaped id appears in the same text. That is a
# real pairing in practice and essentially never a coincidence.
#
# Triggers are evaluated against the ORIGINAL text, before any substitution --
# otherwise the AKIA would have already been rewritten to [REDACTED:AWS_KEY]
# and the trigger would never fire.
_CONTEXTUAL_PATTERNS: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"AKIA[0-9A-Z]{16,}"),
        re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"),
        "[REDACTED:AWS_SECRET]",
    ),
]


# Payload keys that carry captured human text and must be redacted wherever an
# event payload is built or read back.
#
# The first five ARE contextpulse_core.spine.events._TEXT_PAYLOAD_KEYS -- the
# spine's own FTS-indexing tuple -- and a test asserts that stays true, so a key
# added to the event schema cannot silently escape redaction. The remainder are
# payload fields that carry captured text but are NOT FTS-indexed and were
# therefore invisible to anything reasoning from the spine tuple alone:
#   raw_transcript  what Whisper heard, before cleanup (voice)
#   original_text   what the user typed before a correction (touch)
#   corrected_text  what they replaced it with (touch)
_EXTRA_TEXT_PAYLOAD_KEYS: tuple[str, ...] = (
    "raw_transcript",
    "original_text",
    "corrected_text",
)


def _redactable_payload_keys() -> tuple[str, ...]:
    """The spine's text keys plus the non-indexed ones, in a stable order."""
    from contextpulse_core.spine.events import _TEXT_PAYLOAD_KEYS

    return tuple(_TEXT_PAYLOAD_KEYS) + _EXTRA_TEXT_PAYLOAD_KEYS


# Pulls "AWS_KEY" out of "[REDACTED:AWS_KEY]" so a category name is never a
# second hand-maintained list that can drift from the patterns above.
_CATEGORY_RE = re.compile(r"\[REDACTED:([A-Z_]+)\]")


def category_of(replacement: str) -> str:
    """Return the category label carried by a replacement string."""
    match = _CATEGORY_RE.search(replacement)
    if match is None:
        raise ValueError(f"replacement carries no [REDACTED:CATEGORY] label: {replacement!r}")
    return match.group(1)


def redact_with_counts(text: str) -> tuple[str, dict[str, int]]:
    """Redact, and report how many matches each category accounted for.

    Returns (cleaned_text, {category: match_count}). Categories with zero
    matches are omitted, so an empty dict means the text was clean.

    This is the counting entry point for offline auditing (see
    scripts/purge_clipboard_secrets.py), which must be able to report WHAT was
    found without ever handling or printing the value. Sharing the pattern
    table with redact_sensitive is the point: an audit run against its own
    copy of the patterns would silently stop matching whatever this file
    learns next.
    """
    if not text:
        return text, {}
    counts: dict[str, int] = {}

    # Evaluated against the ORIGINAL text: by the time the main table has run,
    # the trigger it looks for has itself been replaced.
    armed = [
        (pattern, replacement)
        for trigger, pattern, replacement in _CONTEXTUAL_PATTERNS
        if trigger.search(text)
    ]

    for pattern, replacement in [*_PATTERNS, *armed]:
        text, n = pattern.subn(replacement, text)
        if n:
            category = category_of(replacement)
            counts[category] = counts.get(category, 0) + n
    return text, counts


def redact_sensitive(text: str) -> str:
    """Apply all redaction patterns to captured text. Returns cleaned text."""
    if not text:
        return text
    cleaned, _counts = redact_with_counts(text)
    return cleaned


def redact_payload(payload: dict[str, Any], extra_keys: Iterable[str] = ()) -> dict[str, Any]:
    """Return a copy of an event payload with every captured-text field redacted.

    Non-text values are copied through untouched; a text key holding a non-string
    (someone stored a list) is also passed through rather than coerced, because
    silently str()-ing it would change the stored shape.

    Redaction is UNCONDITIONAL here on purpose. OCR redaction is gated on the
    ``redact_ocr_text`` setting at its own call site; there is no equivalent
    setting for transcripts, keystrokes or clipboard text and there should not
    be one -- "store my passwords verbatim" is not a feature.
    """
    if not payload:
        return payload
    keys = set(_redactable_payload_keys()) | set(extra_keys)
    out = dict(payload)
    for key in keys:
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = redact_sensitive(value)
    return out
