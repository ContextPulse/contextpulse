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
        (r"(a|(?i)b|c)d", r"(a|(?i:b|c))d"),
        (r"x[(?i)]y", r"x[(?i)]y"),
        (r"end\z", r"end\Z"),
        (r"[[:alnum:]]+", r"[a-zA-Z0-9]+"),
    ],
)
def test_go_regex_translation(go: str, python: str) -> None:
    assert _generator().go_to_python(go) == python
