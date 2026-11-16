# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse's own licence key must never be stored by ContextPulse.

Found 2026-11-15 on a clean Windows machine (installer smoke run 4): a buyer
copies the key from the receipt e-mail and pastes it into the tray's Enter
License Key dialog, which is the documented way to activate. The clipboard
monitor captured the copy, and get_clipboard_history returned the key's first
30 characters. Every buyer's key was being stored in their own activity.db.

The key is ``base64url(json payload) + "." + base64url(ed25519 signature)``
(ContextPulse-Backend lambda/license_webhook.py ``_generate_license_key``;
verified by contextpulse_core.license.verify_key and licensing._do_verify).
Two segments, so the three-segment JWT rule never matched it.

Every key below is generated here with a throwaway Ed25519 key, so it has the
exact shape of a real one and is accepted by the product's own verifier, but
no value is real.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time

import pytest
from contextpulse_core.redact import redact_sensitive, redact_with_counts
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CONTROL_WORD = "zqcontrol"
LABEL = "[REDACTED:CP_LICENSE_KEY]"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _make_key(email: str = "zq.buyer@example.invalid", tier: str = "pro",
              exp: int | None = None) -> tuple[str, Ed25519PrivateKey]:
    """Build a key the way the licence webhook does: sorted, compact JSON."""
    signer = Ed25519PrivateKey.generate()
    payload: dict = {"email": email, "tier": tier, "features": ["sight", "voice"],
                     "ts": int(time.time())}
    if exp is not None:
        payload["exp"] = exp
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64(body)}.{_b64(signer.sign(body))}", signer


def test_generated_key_is_accepted_by_the_products_verifier():
    """Positive control: the fixture is a real-shaped key, not a guess at one."""
    from contextpulse_core.licensing import _do_verify

    key, signer = _make_key()
    pem = signer.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    info = _do_verify(key, pem)
    assert info is not None and info.email == "zq.buyer@example.invalid"


@pytest.mark.parametrize("exp", [None, 1_900_000_000])
@pytest.mark.parametrize("tier", ["pro", "starter", "team"])
def test_licence_key_is_redacted_whole(tier, exp):
    key, _ = _make_key(tier=tier, exp=exp)
    cleaned, counts = redact_with_counts(f"{CONTROL_WORD} my key: {key} thanks")
    assert counts.get("CP_LICENSE_KEY") == 1
    assert LABEL in cleaned
    assert CONTROL_WORD in cleaned
    # The field finding was the HEAD surviving, so check the head and the tail
    # separately rather than only the whole string.
    assert key[:12] not in cleaned
    assert key[-12:] not in cleaned


@pytest.mark.parametrize("wrap", ["{k}", "'{k}'", '"{k}"', "<{k}>", "({k})", "{k}.", "key={k}\n",
                                  "{k}==", "{k}\r\n"])
def test_licence_key_redacted_in_common_surroundings(wrap):
    key, _ = _make_key()
    cleaned = redact_sensitive(f"{CONTROL_WORD} " + wrap.format(k=key))
    assert key[:12] not in cleaned and key[-12:] not in cleaned
    assert CONTROL_WORD in cleaned


def test_bare_paste_of_only_the_key_is_redacted():
    """What the clipboard actually holds at activation: the key and nothing else."""
    key, _ = _make_key()
    assert redact_sensitive(key) == LABEL


@pytest.mark.parametrize("benign", [
    "Meeting notes: ship 0.1.3 on Friday, then review the dialog layout.",
    "see eyJust.eyJoking around",
    "file.eyJson and other.txt",
    # A two-segment base64 string whose second half is NOT signature-length.
    "eyJmb28iOiJiYXIifQ.c2hvcnRzaWduYXR1cmU",
    # 86 base64url characters with no eyJ payload in front.
    "build." + "A" * 86,
])
def test_ordinary_text_is_unchanged(benign):
    assert redact_sensitive(benign) == benign


def test_a_jwt_keeps_its_jwt_label():
    jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ6cWp3dCJ9."
           "zqSIGNATUREabcdefghijk0123456789")
    cleaned = redact_sensitive(jwt)
    assert "[REDACTED:JWT]" in cleaned and LABEL not in cleaned


# ---------------------------------------------------------------- purge


def _store_raw_key(tmp_path, key: str):
    """Write a pre-fix clipboard row directly, as an older build did."""
    from contextpulse_core.spine import EventBus
    from contextpulse_sight.activity import ActivityDB

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    db.close()
    EventBus(db_path).close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO clipboard (timestamp, text) VALUES (?, ?)",
        (time.time(), f"{CONTROL_WORD} {key}"),
    )
    conn.execute(
        "INSERT INTO clipboard (timestamp, text) VALUES (?, ?)",
        (time.time(), f"{CONTROL_WORD} ordinary note about the trial line"),
    )
    conn.commit()
    conn.close()
    return db_path


def test_startup_sweep_purges_a_stored_key_on_a_store_marked_by_the_old_sweep(tmp_path):
    """An install that already ran secret-redaction-v2 must be swept once more.

    That is the whole reason MIGRATION_NAME moves with this pattern: the marker
    is per-name, so without the bump every existing buyer's stored key stays on
    disk forever.
    """
    from contextpulse_core import purge

    assert purge.MIGRATION_NAME != "secret-redaction-v2"
    key, _ = _make_key()
    db_path = _store_raw_key(tmp_path, key)
    conn = sqlite3.connect(str(db_path))
    conn.execute(purge._MIGRATIONS_DDL)
    conn.execute(
        "INSERT INTO cp_migrations (name, applied_at, rows_affected) VALUES (?, ?, 0)",
        ("secret-redaction-v2", time.time()),
    )
    conn.commit()
    conn.close()

    counts = purge.ensure_migrated(db_path)
    assert counts.get("CP_LICENSE_KEY") == 1

    conn = sqlite3.connect(str(db_path))
    texts = [r[0] for r in conn.execute("SELECT text FROM clipboard ORDER BY id")]
    conn.close()
    assert not any(key[:12] in t or key[-12:] in t for t in texts)
    assert f"{CONTROL_WORD} {LABEL}" == texts[0]
    # Ordinary rows are untouched by the sweep.
    assert texts[1] == f"{CONTROL_WORD} ordinary note about the trial line"
    assert key.encode()[:20] not in db_path.read_bytes()
