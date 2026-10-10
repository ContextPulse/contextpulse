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

import hashlib
import math
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
    #
    # The 32-character bar alone was not enough. "task-oriented-dialogue-
    # system-evaluation" carries a 35-character tail of [A-Za-z0-9_-] after the
    # "sk-" inside "task-", so an OCR'd slug or job description was rewritten
    # mid-word as "ta[REDACTED:API_KEY]" (review S-6) -- and because the startup
    # sweep rewrites stored rows, that corruption is permanent.
    #
    # A real key also carries a digit AND an unbroken alphanumeric run; a
    # hyphenated English phrase carries neither (its longest unhyphenated
    # segment is a word). Both are now required. This narrows only the GLUED
    # rule: the anchored 20+ rule above is untouched, so the ordinary
    # whitespace-delimited case keeps its original sensitivity.
    (re.compile(
        r"sk-"
        r"(?=[A-Za-z0-9_-]{32,})"          # still 32+ characters
        r"(?=[A-Za-z0-9_-]*[0-9])"         # ... containing a digit
        r"(?=[A-Za-z0-9_-]*[A-Za-z0-9]{16})"  # ... and a 16-char unbroken run
        r"[A-Za-z0-9_-]{32,}"
    ), "[REDACTED:API_KEY]"),
    (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[REDACTED:API_KEY]"),

    # GitHub tokens
    (re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"), "[REDACTED:GH_TOKEN]"),
    (re.compile(r"gho_[a-zA-Z0-9]{36,}"), "[REDACTED:GH_TOKEN]"),

    # Generic "password", "secret", "token", "api_key" followed by value
    (re.compile(r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}"), "[REDACTED:CREDENTIAL]"),

    # JWT tokens (eyJ base64...). The per-segment floor is 8, not 20: what
    # makes this pattern specific is the SHAPE -- three dot-separated base64url
    # runs, two of them opening with `eyJ` (base64url for `{"`) -- and not the
    # length of any one of them. A 20-character floor silently excluded both
    # ends of the real range: `{"alg":"none"}` encodes to 16 characters after
    # the prefix and `{"sub":"1"}` to 12, so a subject-only token issued by an
    # internal service passed through capture verbatim. Found 2026-09-20 by
    # testing the 0.1.1 advisory's published claim against this table instead
    # of against the fixtures, all of which used long segments.
    (re.compile(r"eyJ[a-zA-Z0-9_-]{8,}\.eyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}"), "[REDACTED:JWT]"),

    # Credit card numbers live in _VALIDATED_PATTERNS below -- shape alone is
    # not enough to call a digit run a card.

    # SSN (XXX-XX-XXXX)
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "[REDACTED:SSN]"),

    # Private key blocks -- ANY armour type, not just RSA. OPENSSH and EC are
    # what `ssh-keygen` and `openssl ecparam` produce and are the likeliest
    # thing on a clipboard right after someone copies a key; the old pattern
    # allowed only "(RSA )?PRIVATE KEY" while the module docstring advertised
    # "BEGIN PRIVATE KEY blocks" generally.
    (re.compile(r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE\s+KEY-----[\s\S]*?-----END\s+[A-Z0-9 ]*PRIVATE\s+KEY-----"),
     "[REDACTED:PRIVATE_KEY]"),

    # The same armour with NO closing block. OCR of a scrolled terminal, a
    # clipboard cut at _MAX_LENGTH and a screenshot of the top half of a key all
    # produce a header plus body and no footer, and the paired rule above
    # requires the pair -- so nothing fired at all and the visible key material
    # was stored verbatim (review S-7).
    #
    # ORDER IS LOAD-BEARING: this runs after the paired rule, so a complete
    # block has already been replaced and still counts exactly once.
    #
    # The body is consumed only while it looks like base64 armour -- runs of 16+
    # base64 characters separated by whitespace -- so prose following a
    # truncated key is kept rather than swallowed to end-of-text. Consuming the
    # body matters: redacting the header alone would mark the secret and leave
    # it on disk.
    (re.compile(r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE\s+KEY-----(?:\s*[A-Za-z0-9+/=]{16,})*\s*"),
     "[REDACTED:PRIVATE_KEY]"),

    # Connection strings with passwords -- any scheme, not a fixed list of
    # four. https://admin:pw@host and ssh://user:pw@host leak the same way
    # postgres:// does. The userinfo character classes exclude "/" and "@" so
    # this cannot run across a path or a second URL.
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"), "[REDACTED:CONN_STRING]://***:***@"),

    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_.-]{20,}"), "[REDACTED:BEARER]"),
    # HTTP Basic -- the base64 decodes to user:password.
    #
    # The token has to LOOK like base64, not merely be 16+ letters: the first
    # version rewrote "Basic responsibilities of the role" (review S-6), which
    # is an ordinary sentence in any OCR'd job description. Base64 of a
    # "user:password" pair carries at least one of a digit, a "+"/"/" character,
    # or an internal lowercase->uppercase flip; an English word carries none of
    # the three. "basic" itself must also start a word, so "Nonbasic ..." does
    # not arm the rule.
    #
    # The case-flip test cannot live under a global (?i) -- that would make
    # [a-z][A-Z] match anything -- so the keyword carries a scoped (?i:...)
    # instead and the rest of the pattern stays case-sensitive.
    (re.compile(
        r"(?<![A-Za-z0-9])(?i:basic)\s+"
        r"(?=[A-Za-z0-9+/]*(?:[0-9+/]|[a-z][A-Z]))"
        r"[A-Za-z0-9+/]{16,}={0,2}"
    ), "[REDACTED:BASIC_AUTH]"),

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

]


# ── card numbers: shape is not enough ───────────────────────────────
#
# A 16-digit run is a build id, an order number, a concatenated date and
# counter, or a session token far more often than it is a card. The shape-only
# rule rewrote all of them as [REDACTED:CC], and because the startup sweep
# rewrites stored rows, that corruption is permanent.
#
# Every card network issues numbers that satisfy the Luhn check digit and begin
# with a registered issuer prefix (IIN). Requiring both is what Presidio's
# CreditCardRecognizer does, and a measured pass over six published network test
# PANs and four benign digit runs from our own review scored 10/10 at ~1.4 us
# per call.
#
# THE TRADE, stated rather than discovered later: a card whose digits were
# MISREAD by OCR now fails Luhn and is not redacted. Presidio makes the same
# trade. The alternative is scrubbing every invoice number that crosses the
# screen, which is the failure this module works hardest to avoid.


def _luhn_checksum(digits: str) -> int:
    """Luhn mod-10 checksum; 0 means valid.

    Ported from Microsoft Presidio's CreditCardRecognizer.__luhn_checksum
    (https://github.com/data-privacy-stack/presidio, MIT licence). Reimplemented
    here rather than imported: presidio-analyzer pulls 31 runtime dependencies
    including spaCy and onnxruntime, against the 3 this package has.
    """
    def digits_of(value: str) -> list[int]:
        return [int(d) for d in str(value)]

    parsed = digits_of(digits)
    odd_digits = parsed[-1::-2]
    even_digits = parsed[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(str(d * 2)))
    return checksum % 10


def _has_card_iin(digits: str) -> bool:
    """True when the number starts with a registered issuer prefix.

    Luhn alone is not enough -- roughly one in ten random digit runs passes it,
    so a valid-checksum accident like "2012345678901238" would still be
    scrubbed. The IIN check is what makes the pair precise.
    """
    two, three, four = digits[:2], digits[:3], digits[:4]
    if digits.startswith("4"):
        return True                                    # Visa
    if two in {"34", "37"}:
        return True                                    # American Express
    if two in {"51", "52", "53", "54", "55"}:
        return True                                    # Mastercard
    if len(four) == 4 and 2221 <= int(four) <= 2720:
        return True                                    # Mastercard 2-series
    if four == "6011" or two == "65" or (len(three) == 3 and 644 <= int(three) <= 649):
        return True                                    # Discover
    if two == "35":
        return True                                    # JCB
    if two in {"36", "38", "39"} or (len(three) == 3 and 300 <= int(three) <= 305):
        return True                                    # Diners Club
    if two == "62":
        return True                                    # UnionPay
    return False


def _is_card_number(matched: str) -> bool:
    digits = "".join(ch for ch in matched if ch.isdigit())
    if len(digits) not in (15, 16):
        return False
    return _has_card_iin(digits) and _luhn_checksum(digits) == 0


# Patterns whose match is only a CANDIDATE: the validator decides. A rejected
# candidate is left exactly as it was AND is not counted, so an audit's "rows
# with secrets" figure never counts a digit run that was never a card.
_VALIDATED_PATTERNS: list[tuple[re.Pattern, str, Any]] = [
    # 16 digits, with or without separators.
    (re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
     "[REDACTED:CC]", _is_card_number),
    # American Express is 15 digits, not 16, so the rule above misses it
    # entirely. The 34/37 prefix is re-checked by the validator.
    (re.compile(r"(?<!\d)3[47]\d{2}[\s-]?\d{6}[\s-]?\d{5}(?!\d)"),
     "[REDACTED:CC]", _is_card_number),
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


def _subn_validated(
    pattern: re.Pattern, replacement: str, is_valid: Any, text: str
) -> tuple[str, int]:
    """Substitute only the matches the validator accepts, and count only those."""
    hits = 0

    def _replace(match: re.Match) -> str:
        nonlocal hits
        if is_valid(match.group(0)):
            hits += 1
            return replacement
        return match.group(0)

    return pattern.sub(_replace, text), hits


# ── vendor token formats, from gitleaks ─────────────────────────────
#
# The table above wins on the families it covers (see
# .internal/audit-2026-09-19/redaction-prior-art.md: 27 of 34 probe families
# against gitleaks' 16). What it cannot do is keep up with ~220 vendors'
# token formats. gitleaks (MIT) does, so its default rule list is vendored as
# generated data (redact_vendor_rules.py) and run as a SECOND pass, after ours,
# so a family we already label keeps its own category.
#
# Three things make it safe to run over clipboard and OCR text:
#   * the keyword gate -- a rule runs only when one of its keywords is in the
#     text, which is also what keeps a 220-rule pass inside the per-call budget;
#   * only the SECRET is replaced, never the whole match. 165 of the rules
#     match surrounding context ("kucoin_key = ...") and capture the value in a
#     group; replacing group(0) would scrub the context this module exists to
#     keep;
#   * gitleaks' own false-positive machinery is honoured: entropy floors,
#     per-rule allowlists and stopwords, and the global allowlist.
#
# Rules listed in _VENDOR_SKIP are not run, each for a measured reason.
#
#   generic-api-key  Run read-only over the whole live activity.db on
#                    2026-10-10 it fired 14 times; 9 were not secrets
#                    (a card's account label like "visa-gold-0000" after a
#                    --card style flag, seven times; an age PUBLIC key after
#                    "# public key:"). Our own CREDENTIAL rule already covers a
#                    labelled password/secret/token/api_key value, so skipping
#                    it loses little and keeps account labels intact.
_VENDOR_REPLACEMENT = "[REDACTED:VENDOR_SECRET]"
_VENDOR_SKIP: frozenset[str] = frozenset({"generic-api-key"})

_vendor_compiled: list[dict[str, Any]] | None = None
_vendor_global: dict[str, Any] | None = None


def _load_vendor_rules() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compile the vendored rules once, on first use."""
    global _vendor_compiled, _vendor_global
    if _vendor_compiled is None or _vendor_global is None:
        from contextpulse_core import redact_vendor_rules as data

        compiled = []
        for rule in data.RULES:
            if rule["id"] in _VENDOR_SKIP:
                continue
            compiled.append(
                {
                    "id": rule["id"],
                    # re.ASCII: Go's \b \w \d \s are ASCII-only. Under Python's
                    # Unicode default a token touching CJK text or an accent
                    # had no word boundary and escaped (review, 2026-10-10).
                    "pattern": re.compile(rule["regex"], re.ASCII),
                    "keywords": tuple(rule["keywords"]),
                    "entropy": rule["entropy"],
                    "secret_group": rule["secret_group"],
                    "allowlists": [
                        {
                            "target": a["target"],
                            "regexes": [re.compile(r, re.ASCII) for r in a["regexes"]],
                            "stopwords": tuple(a["stopwords"]),
                        }
                        for a in rule["allowlists"]
                    ],
                }
            )
        _vendor_global = {
            "regexes": [re.compile(r, re.ASCII) for r in data.GLOBAL_ALLOWLIST["regexes"]],
            "stopwords": tuple(data.GLOBAL_ALLOWLIST["stopwords"]),
        }
        _vendor_compiled = compiled
    return _vendor_compiled, _vendor_global


def _shannon_entropy(value: str) -> float:
    """Bits per character, as gitleaks computes it."""
    if not value:
        return 0.0
    length = len(value)
    return -sum(
        (n / length) * math.log2(n / length) for n in (value.count(c) for c in set(value))
    )


def _secret_span(match: re.Match, secret_group: int) -> tuple[int, int]:
    """The span gitleaks treats as the secret: the named group, else the first non-empty one."""
    if secret_group and secret_group <= (match.re.groups or 0):
        return match.span(secret_group)
    for index in range(1, (match.re.groups or 0) + 1):
        if match.group(index):
            return match.span(index)
    return match.span(0)


def _line_of(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[line_start : len(text) if line_end == -1 else line_end]


def _vendor_allowed(
    text: str, match: re.Match, secret: str, start: int, end: int, rule: dict[str, Any], glob: dict[str, Any]
) -> bool:
    """True when gitleaks would allow (not report) this candidate."""
    lowered = secret.lower()
    if any(r.search(secret) for r in glob["regexes"]) or any(s in lowered for s in glob["stopwords"]):
        return True
    for allow in rule["allowlists"]:
        if allow["target"] == "match":
            subject = match.group(0)
        elif allow["target"] == "line":
            subject = _line_of(text, start, end)
        else:
            subject = secret
        if any(r.search(subject) for r in allow["regexes"]):
            return True
        if any(s in lowered for s in allow["stopwords"]):
            return True
    return False


# Long text is scanned in overlapping windows. Several vendored patterns are
# quadratic in the length of a keyword-bearing run under Python's backtracking
# engine (Go's RE2 is linear): a 300 KB single-line base64 paste took 3.7 s in
# one pass (review, 2026-10-10). Windows bound that to linear time. A match
# that touches a window's right edge is dropped unless the edge is the end of
# the text -- `$` and `\b` treat the edge as an end, so it may be a truncation
# -- and the overlap lets the next window see it whole, for any token shorter
# than the overlap. That covers every bounded rule (the longest,
# pypi-upload-token, is about 1,020 chars). It does NOT cover an UNBOUNDED rule
# whose token is longer than 4 KB and straddles an inner edge: a 5 KB JWT can be
# missed by this pass. Our own table redacts JWTs and PEM blocks before this
# pass runs, so that case is covered upstream, not here (review, 2026-10-10).
_VENDOR_WINDOW = 16_384
_VENDOR_OVERLAP = 4_096

# Python's ASCII-mode \s does not match non-ASCII whitespace, while most rules
# end with a [`'"\s;] delimiter. A token followed by a no-break space or an
# ideographic space (pasted from a web page) would no longer end. Mapping each
# such character to a plain space keeps every offset and restores the
# delimiter (review, 2026-10-10).
_UNICODE_SPACES = "\x85\xa0                　"
_SPACE_TO_ASCII = str.maketrans({ch: " " for ch in _UNICODE_SPACES})


def _vendor_windows(length: int) -> list[tuple[int, int]]:
    if length <= _VENDOR_WINDOW:
        return [(0, length)]
    step = _VENDOR_WINDOW - _VENDOR_OVERLAP
    windows = []
    start = 0
    while True:
        end = min(start + _VENDOR_WINDOW, length)
        windows.append((start, end))
        if end == length:
            return windows
        start += step


def vendor_findings(text: str) -> list[tuple[int, int, str]]:
    """Spans the vendored gitleaks rules would redact, as (start, end, rule_id).

    Exposed for auditing: it lets a measurement say WHICH rule fired without
    ever printing the value.
    """
    if not text:
        return []
    rules, glob = _load_vendor_rules()
    # Same length as the input, so every offset found in it is an offset in text.
    text = text.translate(_SPACE_TO_ASCII)
    length = len(text)
    findings: set[tuple[int, int, str]] = set()
    for win_start, win_end in _vendor_windows(length):
        # Lower-case the WINDOW, never slice a lowered copy of the whole text:
        # str.lower() can lengthen a string ("İ" becomes two characters),
        # which shifts every later offset (review, 2026-10-10).
        window_lowered = text[win_start:win_end].lower()
        for rule in rules:
            if rule["keywords"] and not any(k in window_lowered for k in rule["keywords"]):
                continue
            for match in rule["pattern"].finditer(text, win_start, win_end):
                if match.end() == win_end and win_end != length:
                    continue
                start, end = _secret_span(match, rule["secret_group"])
                if start < 0 or end <= start:
                    continue
                secret = text[start:end]
                if "[REDACTED:" in secret:
                    continue
                if rule["entropy"] and _shannon_entropy(secret) <= rule["entropy"]:
                    continue
                if _vendor_allowed(text, match, secret, start, end, rule, glob):
                    continue
                findings.add((start, end, rule["id"]))
    return sorted(findings)


def _redact_vendor(text: str) -> tuple[str, int]:
    """Replace every vendor finding's secret span; overlapping spans merge into one."""
    spans = sorted((s, e) for s, e, _rid in vendor_findings(text))
    if not spans:
        return text, 0
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        text = text[:start] + _VENDOR_REPLACEMENT + text[end:]
    return text, len(merged)


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

    # Validated families last. subn() would count every CANDIDATE, including the
    # ones the validator rejects, so these are substituted through a callable
    # that counts only what it actually replaced.
    for pattern, replacement, is_valid in _VALIDATED_PATTERNS:
        text, n = _subn_validated(pattern, replacement, is_valid, text)
        if n:
            category = category_of(replacement)
            counts[category] = counts.get(category, 0) + n

    # Vendor formats last, so anything our own table labels keeps its label.
    text, n = _redact_vendor(text)
    if n:
        category = category_of(_VENDOR_REPLACEMENT)
        counts[category] = counts.get(category, 0) + n
    return text, counts


def redact_sensitive(text: str) -> str:
    """Apply all redaction patterns to captured text. Returns cleaned text."""
    if not text:
        return text
    cleaned, _counts = redact_with_counts(text)
    return cleaned


def redacted_text_digest(text: str, length: int = 16) -> str:
    """A short digest that is safe to STORE, because it commits to redacted text.

    Used to correlate one piece of text across modalities -- a dictation and the
    paste it produced -- without keeping the text itself.

    HASHING THE RAW TEXT IS A PREIMAGE ORACLE, not a privacy measure. A dictated
    SSN has a search space of 10^9 and a card number 10^16; a 64-bit prefix of
    sha256("my social is 123-45-6789") inverts by brute force in seconds, and
    redact_payload leaves a digest alone because it is not a text field. So the
    digest is taken over the redacted rendering, which commits to everything the
    store is allowed to remember and to nothing it is not (review S-3).

    ONE DEFINITION ON PURPOSE. Both sides of a correlation must hash the same
    way or the match silently stops working for exactly the inputs that
    contained a secret -- which is the failure mode that made hashing the raw
    text look necessary in the first place. Redaction is deterministic, so two
    callers given the same text still agree.
    """
    return hashlib.sha256(redact_sensitive(text).encode()).hexdigest()[:length]


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
