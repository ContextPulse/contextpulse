"""Tests for contextpulse_core.license_cli -- headless licence activation.

The installed app exposes ``ContextPulseCLI.exe --activate-license <file>`` and
``--license-status``. Both must run through the SAME save_license / verify_key
path the tray "Enter License Key" dialog uses, so a clean machine can prove
activation without a GUI. The key text must never be printed.
"""

import base64
import json
import time

import pytest
from contextpulse_core import license_cli

_TEST_PUBLIC_KEY_HEX = "920614822142ae511a5fcc1d7d20acdc0c8fec0049ce00af226cfa0d12665fc2"
_TEST_PRIVATE_KEY_HEX = "c10e1d9f6d57c919cae9955cd8a2060ad5c9aa667f5a821db0b8ed2a42fcfc72"
# A different keypair: a key it signs must be refused by _TEST_PUBLIC_KEY_HEX.
_OTHER_PRIVATE_KEY_HEX = "11" * 32


def _key(tier: str = "pro", exp: int | None = None, private_hex: str = _TEST_PRIVATE_KEY_HEX) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    payload = {"email": "buyer@example.com", "tier": tier, "ts": int(time.time())}
    if exp is not None:
        payload["exp"] = exp
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = priv.sign(body)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"{enc(body)}.{enc(sig)}"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import contextpulse_core.license as lic

    appdata = tmp_path / "ContextPulse"
    monkeypatch.setattr(lic, "APPDATA_DIR", appdata)
    monkeypatch.setattr(lic, "LICENSE_FILE", appdata / "license.key")
    monkeypatch.setattr(lic, "TRIAL_FILE", appdata / "trial.json")
    monkeypatch.setattr(lic, "_PUBLIC_KEY_HEX", _TEST_PUBLIC_KEY_HEX)
    return appdata


def _write(tmp_path, text: str):
    p = tmp_path / "key.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ── status ───────────────────────────────────────────────────────────


def test_status_unlicensed_reports_no_tier_and_does_not_start_trial(isolated):
    st = license_cli.license_status()
    assert st["licensed"] is False
    assert st["tier"] == ""
    assert st["source"] == "trial"
    assert st["trial_days_remaining"] == 30
    # A status query must not start the 30-day trial clock.
    assert not (isolated / "trial.json").exists()


def test_status_after_activation_reports_pro(tmp_path):
    rc = license_cli.main(["--activate-license", str(_write(tmp_path, _key("pro")))])
    assert rc == 0
    st = license_cli.license_status()
    assert st["licensed"] is True
    assert st["tier"] == "pro"
    assert st["pro_access"] is True
    assert st["source"] == "license"
    assert st["expires"] == "never"


def test_status_expired_trial_and_no_licence_has_no_pro_access(isolated):
    from contextpulse_core.license import _write_trial

    _write_trial({"start": int(time.time()) - 40 * 86400})
    st = license_cli.license_status()
    assert st["pro_access"] is False
    assert st["source"] == "none"
    assert st["trial_days_remaining"] == 0


def test_status_starter_licence_is_not_pro(tmp_path):
    assert license_cli.main(["--activate-license", str(_write(tmp_path, _key("starter")))]) == 0
    st = license_cli.license_status()
    assert st["tier"] == "starter"
    assert st["pro_access"] is False


# ── activation ───────────────────────────────────────────────────────


def test_activate_writes_the_same_file_the_dialog_writes(tmp_path, isolated):
    key = _key("pro")
    assert license_cli.main(["--activate-license", str(_write(tmp_path, key + "\n"))]) == 0
    assert (isolated / "license.key").read_text(encoding="utf-8") == key


def test_activate_goes_through_save_license(tmp_path, monkeypatch):
    calls = []
    import contextpulse_core.license as lic

    real = lic.save_license

    def spy(k):
        calls.append(k)
        return real(k)

    monkeypatch.setattr(license_cli, "save_license", spy)
    license_cli.main(["--activate-license", str(_write(tmp_path, _key()))])
    assert len(calls) == 1


def test_activate_key_from_other_signer_is_refused(tmp_path, isolated, capsys):
    rc = license_cli.main(
        ["--activate-license", str(_write(tmp_path, _key(private_hex=_OTHER_PRIVATE_KEY_HEX)))]
    )
    assert rc == 2
    assert not (isolated / "license.key").exists()
    assert "invalid" in capsys.readouterr().err.lower()


def test_activate_invalid_does_not_overwrite_existing_licence(tmp_path, isolated):
    good = _key("pro")
    assert license_cli.main(["--activate-license", str(_write(tmp_path, good))]) == 0
    assert license_cli.main(["--activate-license", str(_write(tmp_path, "garbage.data"))]) == 2
    assert (isolated / "license.key").read_text(encoding="utf-8") == good


@pytest.mark.parametrize("content", ["", "   \n", "﻿"])
def test_activate_empty_file_is_refused(tmp_path, content):
    assert license_cli.main(["--activate-license", str(_write(tmp_path, content))]) == 2


def test_activate_tolerates_utf8_bom(tmp_path, isolated):
    key = _key("pro")
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbf" + key.encode())  # Notepad's "UTF-8 with BOM"
    assert license_cli.main(["--activate-license", str(p)]) == 0
    assert (isolated / "license.key").read_text(encoding="utf-8") == key


def test_activate_missing_file_returns_1(tmp_path):
    assert license_cli.main(["--activate-license", str(tmp_path / "nope.txt")]) == 1


def test_activate_without_path_is_usage_error(capsys):
    assert license_cli.main(["--activate-license"]) == 2


# ── never print the key ─────────────────────────────────────────────


@pytest.mark.parametrize("as_json", [False, True])
def test_output_never_contains_the_key(tmp_path, capsys, as_json):
    key = _key("pro")
    payload_part, sig_part = key.split(".")
    extra = ["--json"] if as_json else []
    assert license_cli.main(["--activate-license", str(_write(tmp_path, key)), *extra]) == 0
    assert license_cli.main(["--license-status", *extra]) == 0
    license_cli.main(["--activate-license", key])  # the key pasted where a path belongs
    out = capsys.readouterr()
    text = out.out + out.err
    assert payload_part not in text
    assert sig_part not in text
    assert "buyer@example.com" not in text  # email is masked


def test_status_json_is_parseable(capsys):
    assert license_cli.main(["--license-status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) >= {"licensed", "tier", "pro_access", "source", "trial_days_remaining"}


def test_status_text_lines(capsys):
    license_cli.main(["--license-status"])
    out = capsys.readouterr().out
    assert "tier=" in out and "pro_access=" in out


def test_mask_email():
    assert license_cli.mask_email("buyer@example.com") == "b***@example.com"
    assert license_cli.mask_email("") == ""
    assert license_cli.mask_email("noatsign") == "***"


def test_expired_key_is_refused_and_does_not_replace_a_good_one(tmp_path, isolated):
    good = _key("pro")
    assert license_cli.main(["--activate-license", str(_write(tmp_path, good))]) == 0
    old = _key("pro", exp=int(time.time()) - 30 * 86400)
    assert license_cli.main(["--activate-license", str(_write(tmp_path, old))]) == 2
    assert (isolated / "license.key").read_text(encoding="utf-8") == good


def test_handles_equals_form():
    assert license_cli.handles(["--activate-license=k.txt"]) is True


def test_handles_only_its_own_flags():
    assert license_cli.handles(["--license-status"]) is True
    assert license_cli.handles(["--activate-license", "x"]) is True
    assert license_cli.handles(["--status"]) is False
    assert license_cli.handles([]) is False
