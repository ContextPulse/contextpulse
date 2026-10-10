# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The vendored gitleaks pass (cp-redact-vendor-gitleaks-rules-as-data).

Token values are BUILT at runtime from hashes, never written as literals, so
this file does not trip the repository's own secret scanner.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest
from contextpulse_core import redact, redact_vendor_rules
from contextpulse_core.redact import redact_sensitive, redact_with_counts, vendor_findings

REPO = Path(__file__).resolve().parents[3]
GENERATOR = REPO / "scripts" / "gen_redact_vendor_rules.py"
AGE_ALPHABET = "QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"


def _hex(seed: str, n: int) -> str:
    out = ""
    while len(out) < n:
        out += hashlib.sha256((seed + str(len(out))).encode()).hexdigest()
    return out[:n]


def _letters(seed: str, n: int) -> str:
    return "".join("abcdefghijklmnop"[int(c, 16)] for c in _hex(seed, n))


def _alnum(seed: str, n: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(alphabet[b % len(alphabet)] for b in bytes.fromhex(_hex(seed, 2 * n)))


def _age(seed: str) -> str:
    return "AGE-SECRET-KEY-1" + "".join(
        AGE_ALPHABET[b % 32] for b in bytes.fromhex(_hex(seed, 116))
    )


DO_TOKEN = "dop_v1_" + _hex("do", 64)
HF_TOKEN = "hf_" + _letters("hf", 34)
GITLAB_TOKEN = "glpat-" + _alnum("gl", 20)
PPLX_TOKEN = "pplx-" + _alnum("pp", 48)
AGE_KEY = _age("age")


# --- tokens our own table does not know ------------------------------------


@pytest.mark.parametrize("token", [DO_TOKEN, HF_TOKEN, GITLAB_TOKEN, PPLX_TOKEN, AGE_KEY])
def test_vendor_token_unknown_to_our_table_is_redacted(token: str) -> None:
    cleaned, counts = redact_with_counts(f"copied {token} from the dashboard")
    assert token not in cleaned
    assert cleaned == "copied [REDACTED:VENDOR_SECRET] from the dashboard"
    assert counts == {"VENDOR_SECRET": 1}


def test_vendor_rule_ids_fire_for_the_real_live_finds() -> None:
    """The three credentials found stored in activity.db on 2026-10-10 were these formats."""
    fired = {
        rid for text in (HF_TOKEN, PPLX_TOKEN, AGE_KEY) for _s, _e, rid in vendor_findings(text)
    }
    assert fired == {"huggingface-access-token", "perplexity-api-key", "age-secret-key"}


# --- only the secret goes, the context stays --------------------------------


def test_capture_group_rule_replaces_only_the_secret() -> None:
    """kucoin's rule matches 'kucoin_key = <hex>' but the secret is group 1."""
    value = _hex("kucoin", 24)
    text = f'config: kucoin_key = "{value}" # prod'
    cleaned = redact_sensitive(text)
    assert value not in cleaned
    assert cleaned == 'config: kucoin_key = "[REDACTED:VENDOR_SECRET]" # prod'


def test_trailing_delimiter_in_the_match_is_kept() -> None:
    cleaned = redact_sensitive(f"export DO={DO_TOKEN}; echo done")
    assert cleaned == "export DO=[REDACTED:VENDOR_SECRET]; echo done"


def test_two_tokens_in_one_text_are_counted_separately() -> None:
    cleaned, counts = redact_with_counts(f"{DO_TOKEN}\n{HF_TOKEN}\n")
    assert DO_TOKEN not in cleaned and HF_TOKEN not in cleaned
    assert counts == {"VENDOR_SECRET": 2}


# --- our own families keep their labels -------------------------------------


def test_family_our_table_covers_keeps_its_own_category() -> None:
    ghp = "ghp_" + _alnum("gh", 36)
    cleaned, counts = redact_with_counts(f"token {ghp}")
    assert counts == {"GH_TOKEN": 1}
    assert "VENDOR_SECRET" not in cleaned


def test_redaction_is_idempotent() -> None:
    text = f"a {DO_TOKEN} b {HF_TOKEN} c kucoin_key = {_hex('k2', 24)}"
    once = redact_sensitive(text)
    assert redact_sensitive(once) == once


# --- false-positive machinery -----------------------------------------------


BENIGN = [
    "commit 4f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 merged",  # a git SHA
    "Basic responsibilities of the role include ownership",
    "task-oriented-dialogue-system-evaluation",
    "build 2026091912345678 finished",
    "uuid 014df517-39d1-4453-b7b3-9930c563627c",
    "pay.py --card visa-gold-0000 --project home",
    "# public key: age1"
    + "".join(AGE_ALPHABET[b % 32].lower() for b in bytes.fromhex(_hex("pub", 116))),
    "https://github.com/ContextPulse/contextpulse/pull/25",
    "C:/Users/alice/Projects/ContextPulse/packages/core/src/contextpulse_core/redact.py",
    "hf_ is the HuggingFace token prefix; dop_v1_ is DigitalOcean's",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_is_untouched(text: str) -> None:
    assert redact_sensitive(text) == text


def test_entropy_floor_rejects_a_degenerate_candidate() -> None:
    """dop_v1_ followed by 64 zeros matches the shape but carries no entropy."""
    text = "dop_v1_" + "0" * 64
    assert vendor_findings(text) == []


def test_global_stopword_is_honoured() -> None:
    """gitleaks' global stopword (the alphabet run) marks a sample, not a key."""
    sample = "pplx-" + "abcdefghijklmnopqrstuvwxyz" + _alnum("sw", 22)
    real = "pplx-" + _alnum("sw2", 48)
    assert vendor_findings(sample) == []
    assert [rid for _s, _e, rid in vendor_findings(real)] == ["perplexity-api-key"]


def test_keyword_gate_skips_rules_without_their_keyword() -> None:
    """A 40-hex run is sourcegraph-shaped, but without 'sourcegraph' or 'sgp_' it never runs."""
    assert vendor_findings(_hex("sha", 40)) == []


def test_generic_api_key_is_not_run() -> None:
    rules, _glob = redact._load_vendor_rules()
    assert "generic-api-key" not in {r["id"] for r in rules}


def test_already_redacted_marker_is_not_redacted_again() -> None:
    text = "kucoin_key = [REDACTED:CREDENTIAL]"
    assert vendor_findings(text) == []


def test_empty_and_none_like_input() -> None:
    assert redact_with_counts("") == ("", {})
    assert vendor_findings("") == []


def test_benign_call_stays_inside_the_per_call_budget() -> None:
    text = " ".join(BENIGN) * 3
    redact_sensitive(text)  # warm the lazy compile
    start = time.perf_counter()
    for _ in range(100):
        redact_sensitive(text)
    per_call_ms = (time.perf_counter() - start) * 10
    assert per_call_ms < 5.0, per_call_ms


# --- the generated data -----------------------------------------------------


def test_every_vendored_rule_compiles_and_only_the_path_rule_is_excluded() -> None:
    assert redact_vendor_rules.EXCLUDED == ("pkcs12-file: no regex (path-only rule)",)
    assert len(redact_vendor_rules.RULES) == 221


def test_generated_module_matches_the_vendored_config() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _generator():
    spec = importlib.util.spec_from_file_location("gen_redact_vendor_rules", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("go", "python"),
    [
        (r"\b(p8e-(?i)[a-z0-9]{32})x", r"\b(p8e-(?i:[a-z0-9]{32}))x"),
        (r"dp\.pt\.(?i)[a-z0-9]{43}", r"dp\.pt\.(?i:[a-z0-9]{43})"),
        (r"(?i)abc", r"(?i)abc"),
        (r"(a|(?i)b|c)d", r"(a|(?i:b)|(?i:c))d"),
        (r"x[(?i)]y", r"x[(?i)]y"),
        (r"end\z", r"end\Z"),
        (r"[[:alnum:]]+", r"[a-zA-Z0-9]+"),
    ],
)
def test_go_regex_translation(go: str, python: str) -> None:
    assert _generator().go_to_python(go) == python


def test_inline_flag_does_not_swallow_the_next_alternative() -> None:
    """Go keeps (?i) on across '|' but the alternatives stay separate (review finding 1)."""
    import re

    translated = re.compile(_generator().go_to_python(r"""(?:"(?i)(?:auth:x)"|'(?i)(?:auth:y)')"""))
    assert translated.fullmatch('"AUTH:X"')
    assert translated.fullmatch("'AUTH:Y'")
    assert not translated.fullmatch("\"'auth:y'")


# --- review findings, 2026-10-10 --------------------------------------------


def test_single_quoted_curl_header_is_redacted() -> None:
    """The common shell form. It escaped while the (?i) scoper swallowed the '|' branch."""
    token = _alnum("curl", 30)
    text = f"curl --header 'Authorization: Token {token}' https://x.io"
    cleaned = redact_sensitive(text)
    assert token not in cleaned
    assert cleaned.startswith("curl --header 'Authorization: Token [REDACTED:")


@pytest.mark.parametrize("prefix", ["中文", "é"])
@pytest.mark.parametrize("token", [HF_TOKEN, "SG." + _alnum("sg1", 22) + "." + _alnum("sg2", 43)])
def test_token_after_non_ascii_text_is_redacted(prefix: str, token: str) -> None:
    """Go's \\b is ASCII-only; under Python's Unicode default these escaped (review finding 2).

    A token FOLLOWED directly by non-ASCII text is not covered, by gitleaks
    either: these rules end in a required delimiter group ([`'"\\s;] or end of
    text), and a CJK character or an accent is neither in Go or in Python.
    """
    cleaned = redact_sensitive(f"{prefix}{token} next")
    assert token not in cleaned
    assert cleaned == f"{prefix}[REDACTED:VENDOR_SECRET] next"


def _base64_blob(size: int) -> str:
    import base64

    raw = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(size // 32 + 1))
    return base64.b64encode(raw).decode()[:size]


def test_large_single_line_paste_is_linear_and_still_redacts() -> None:
    """300 KB of base64 took 3.7 s in one pass (review finding 3); windows bound it."""
    blob = _base64_blob(300_000)
    edge = redact._VENDOR_WINDOW - 20  # a token straddling the first window edge
    text = blob[:edge] + f" {DO_TOKEN} " + blob[edge:150_000] + f" {HF_TOKEN} " + blob[150_000:]
    start = time.perf_counter()
    cleaned = redact_sensitive(text)
    elapsed = time.perf_counter() - start
    assert DO_TOKEN not in cleaned and HF_TOKEN not in cleaned
    assert cleaned.count("[REDACTED:VENDOR_SECRET]") == 2
    assert elapsed < 1.5, elapsed


@pytest.mark.parametrize("space", [" ", "　", " ", " "])
def test_vendor_only_token_followed_by_unicode_space_is_redacted(space: str) -> None:
    """re.ASCII made \\s miss these, so the rules' trailing delimiter failed (re-review finding 2)."""
    cleaned = redact_sensitive(f"key{space}{HF_TOKEN}{space}copied")
    assert HF_TOKEN not in cleaned
    assert cleaned == f"key{space}[REDACTED:VENDOR_SECRET]{space}copied"


@pytest.mark.parametrize("count", [45, 10_000])
def test_lowercase_that_lengthens_text_does_not_shift_the_keyword_gate(count: int) -> None:
    """'\\u0130'.lower() is two characters; a lowered copy sliced by text offsets
    lost the tail, so a vendor-only token near the end was skipped (re-review finding 1)."""
    text = "İ" * count + " " + "z " * 2_500 + HF_TOKEN + " end"
    cleaned = redact_sensitive(text)
    assert HF_TOKEN not in cleaned
    assert cleaned.endswith("[REDACTED:VENDOR_SECRET] end")


def test_window_edge_does_not_invent_a_token() -> None:
    """`$` matches at a window's end, so a longer run cut there must not be reported.

    hf_ + 40 letters is not a HuggingFace token (the rule needs exactly 34 and a
    delimiter). Cut by the first window's edge after 34 letters, it looks like one.
    """
    run = "hf_" + _letters("edge", 40)
    start = redact._VENDOR_WINDOW - 37
    text = "x" * (start - 1) + " " + run + " " + "y " * 5_000
    assert text[start : start + 3] == "hf_"
    assert redact_sensitive(text) == text


def test_windows_cover_the_text_with_overlap() -> None:
    n = 100_000
    windows = redact._vendor_windows(n)
    assert windows[0][0] == 0 and windows[-1][1] == n
    for (s1, e1), (s2, _e2) in zip(windows, windows[1:]):
        assert e1 - s2 == redact._VENDOR_OVERLAP
    assert redact._vendor_windows(10) == [(0, 10)]
