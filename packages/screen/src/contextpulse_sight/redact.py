# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Redact sensitive patterns from OCR text before storage.

Detects and masks common secret patterns:
  - API keys (sk-..., AKIA..., ghp_..., gho_..., etc.)
  - Passwords in common formats (password: ..., pwd=...)
  - Credit card numbers (16 digits)
  - SSN patterns (XXX-XX-XXXX)
  - JWT tokens (eyJ...)
  - Private keys (BEGIN PRIVATE KEY blocks)
  - Connection strings with credentials

Redaction replaces the sensitive portion with [REDACTED] while preserving
surrounding context for legitimate OCR use.
"""

import re

# Each pattern is (compiled_regex, replacement)
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # AWS access keys (AKIA...)
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:AWS_KEY]"),
    # AWS secret keys (40-char base64-ish after = or :)
    (re.compile(r"(?i)(?:aws_secret|secret_access_key)\s*[:=]\s*\S{20,60}"), "[REDACTED:AWS_SECRET]"),

    # OpenAI / Anthropic API keys
    (re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"), "[REDACTED:API_KEY]"),
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"), "[REDACTED:API_KEY]"),

    # GitHub tokens
    (re.compile(r"\bgh[ps]_[a-zA-Z0-9]{36,}\b"), "[REDACTED:GH_TOKEN]"),
    (re.compile(r"\bgho_[a-zA-Z0-9]{36,}\b"), "[REDACTED:GH_TOKEN]"),

    # Generic "password", "secret", "token", "api_key" followed by value
    (re.compile(r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}"), "[REDACTED:CREDENTIAL]"),

    # JWT tokens (eyJ base64...)
    (re.compile(r"\beyJ[a-zA-Z0-9_-]{20,}\.eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\b"), "[REDACTED:JWT]"),

    # Credit card numbers (16 digits, with or without separators)
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[REDACTED:CC]"),

    # SSN (XXX-XX-XXXX)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED:SSN]"),

    # Private key blocks
    (re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
     "[REDACTED:PRIVATE_KEY]"),

    # Connection strings with passwords
    (re.compile(r"(?i)(?:mysql|postgres|mongodb|redis)://\S+:\S+@"), "[REDACTED:CONN_STRING]://***:***@"),

    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_.-]{20,}"), "[REDACTED:BEARER]"),
]


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
    for pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        if n:
            category = category_of(replacement)
            counts[category] = counts.get(category, 0) + n
    return text, counts


def redact_sensitive(text: str) -> str:
    """Apply all redaction patterns to OCR text. Returns cleaned text."""
    if not text:
        return text
    cleaned, _counts = redact_with_counts(text)
    return cleaned
