# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Generate contextpulse_core/redact_vendor_rules.py from gitleaks' default config.

The vendored rules are DATA, not code: gitleaks (MIT) keeps ~220 vendor token
formats current, and ContextPulse runs them as a second redaction pass after
its own hand-written table (see contextpulse_core.redact). This script turns
``third_party/gitleaks/gitleaks.toml`` into a plain Python literal so the
runtime needs nothing beyond ``re``.

Go RE2 and Python ``re`` differ in three ways the config actually uses, and
each is translated mechanically here rather than by hand:

* ``\\z`` (end of text) is ``\\Z`` in Python.
* A mid-pattern ``(?i)`` switches case-folding on until the end of the
  enclosing group. Python only allows global flags at the very start, so the
  flag becomes a scoped ``(?i:...)`` group closed where the enclosing group
  closes, and closed and reopened around every ``|`` in that group so it
  never swallows the next alternative. That keeps the semantics exact;
  hoisting it to a compile flag would have made the prefix before it
  case-insensitive too.
* Go's ``\\b``, ``\\w``, ``\\d`` and ``\\s`` are ASCII-only. That is a
  compile flag, not syntax, so contextpulse_core.redact compiles every
  vendored pattern with ``re.ASCII``.
* POSIX classes inside a bracket (``[[:alnum:]]``) become explicit ranges.

A rule that still fails to compile is left out and named in the generated
module's EXCLUDED tuple, so a test can pin the count.

Usage:
    python scripts/gen_redact_vendor_rules.py          # write the module
    python scripts/gen_redact_vendor_rules.py --check  # exit 1 if stale
"""

from __future__ import annotations

import argparse
import pprint
import re
import sys
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "third_party" / "gitleaks" / "gitleaks.toml"
UPSTREAM = REPO / "third_party" / "gitleaks" / "UPSTREAM"
TARGET = REPO / "packages" / "core" / "src" / "contextpulse_core" / "redact_vendor_rules.py"

_POSIX = {
    "alnum": "a-zA-Z0-9",
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "lower": "a-z",
    "upper": "A-Z",
    "xdigit": "0-9A-Fa-f",
    "space": r"\s",
    "word": r"\w",
    "punct": r"!-/:-@\[-`{-~",
}


def _scope_inline_ignorecase(pattern: str) -> str:
    """Rewrite each non-leading ``(?i)`` as ``(?i:`` closed at its enclosing group's end."""
    out: list[str] = []
    depth = 0
    pending: list[int] = []  # depths at which a scoped (?i: must be closed
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            out.append(ch)
            i += 1
            continue
        if ch == "[":
            in_class = True
            out.append(ch)
            # a ']' straight after '[' or '[^' is literal
            if pattern[i + 1 : i + 2] == "^":
                out.append("^")
                i += 1
            if pattern[i + 1 : i + 2] == "]":
                out.append("]")
                i += 1
            i += 1
            continue
        if pattern.startswith("(?i)", i) and i > 0:
            out.append("(?i:")
            pending.append(depth)
            i += 4
            continue
        if ch == "(":
            depth += 1
            out.append(ch)
            i += 1
            continue
        if ch == ")":
            while pending and pending[-1] == depth:
                out.append(")")
                pending.pop()
            depth -= 1
            out.append(ch)
            i += 1
            continue
        if ch == "|":
            # The flag stays on across '|' in Go, but a scoped group must not
            # swallow the next alternative: close it before the bar and reopen
            # it after, so 'x"(?i)A"|'B'' keeps two alternatives, both folded.
            open_here = 0
            while pending and pending[-1] == depth:
                out.append(")")
                pending.pop()
                open_here += 1
            out.append("|")
            out.extend("(?i:" for _ in range(open_here))
            pending.extend(depth for _ in range(open_here))
            i += 1
            continue
        out.append(ch)
        i += 1
    out.extend(")" for _ in pending)
    return "".join(out)


def go_to_python(pattern: str) -> str:
    """Translate one gitleaks (Go RE2) pattern to Python ``re`` syntax."""
    pattern = re.sub(r"\[:([a-z]+):\]", lambda m: _POSIX[m.group(1)], pattern)
    pattern = pattern.replace(r"\z", r"\Z")
    return _scope_inline_ignorecase(pattern)


def _allowlists(raw: list[dict]) -> list[dict]:
    """Keep the parts of an allowlist that can apply to text with no file path."""
    kept = []
    for entry in raw:
        has_path = bool(entry.get("paths"))
        if entry.get("condition", "OR").upper() == "AND" and has_path:
            # AND with a path criterion can never be satisfied without a path.
            continue
        regexes = [go_to_python(r) for r in entry.get("regexes", [])]
        stopwords = [s.lower() for s in entry.get("stopwords", [])]
        if not regexes and not stopwords:
            continue
        kept.append(
            {
                "target": entry.get("regexTarget", "secret"),
                "regexes": regexes,
                "stopwords": stopwords,
            }
        )
    return kept


def build(config: dict) -> tuple[list[dict], dict, list[str]]:
    rules: list[dict] = []
    excluded: list[str] = []
    for rule in config["rules"]:
        rid = rule["id"]
        if "regex" not in rule:
            excluded.append(f"{rid}: no regex (path-only rule)")
            continue
        pattern = go_to_python(rule["regex"])
        try:
            re.compile(pattern)
        except re.error as exc:
            excluded.append(f"{rid}: {exc}")
            continue
        rules.append(
            {
                "id": rid,
                "regex": pattern,
                "keywords": [k.lower() for k in rule.get("keywords", [])],
                "entropy": float(rule.get("entropy", 0.0)),
                "secret_group": int(rule.get("secretGroup", 0)),
                "allowlists": _allowlists(rule.get("allowlists", [])),
            }
        )
    glob = config.get("allowlist", {})
    global_allow = {
        "regexes": [go_to_python(r) for r in glob.get("regexes", [])],
        "stopwords": [s.lower() for s in glob.get("stopwords", [])],
    }
    return rules, global_allow, excluded


def render(rules: list[dict], global_allow: dict, excluded: list[str], upstream: str) -> str:
    header = f'''# SPDX-License-Identifier: MIT
# Copyright (c) 2019 Zachary Rice (gitleaks)
# GENERATED by scripts/gen_redact_vendor_rules.py -- do not edit by hand.
# Source: third_party/gitleaks/gitleaks.toml ({upstream})  # gitleaks:allow
# Licence: MIT, full text in third_party/gitleaks/LICENSE.
"""Vendor token rules from gitleaks, translated to Python ``re`` syntax.

Consumed by contextpulse_core.redact as a second pass. Regenerate with
``python scripts/gen_redact_vendor_rules.py``; a test fails if this file and
the vendored TOML disagree.

Every line carrying a string is marked ``gitleaks:allow``. This file IS a
secret scanner's rule set: it holds the regexes themselves and the public
example keys gitleaks allowlists, which a scanner would otherwise flag.
"""

'''
    body = (
        f"UPSTREAM = {upstream!r}\n\n"
        f"EXCLUDED = {pprint.pformat(tuple(excluded), width=100)}\n\n"
        f"GLOBAL_ALLOWLIST = {pprint.pformat(global_allow, width=100, sort_dicts=True)}\n\n"
        f"RULES = {pprint.pformat(rules, width=100, sort_dicts=True)}\n"
    )
    marked = "\n".join(
        f"{line}  # gitleaks:allow" if "'" in line else line for line in body.split("\n")
    )
    return header + marked


def generate() -> str:
    config = tomllib.loads(SOURCE.read_text(encoding="utf-8"))
    upstream = UPSTREAM.read_text(encoding="utf-8").strip()
    rules, global_allow, excluded = build(config)
    return render(rules, global_allow, excluded, upstream)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the module is stale")
    args = parser.parse_args()
    text = generate()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"STALE: {TARGET} does not match {SOURCE}; regenerate.")
            return 1
        print("OK: generated module matches the vendored config.")
        return 0
    TARGET.write_bytes(text.encode("utf-8"))
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
