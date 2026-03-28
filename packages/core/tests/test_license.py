"""Tests for contextpulse_core.license — Ed25519 licensing with tiers and expiration."""

import base64
import json
import time

import pytest
from contextpulse_core.license import (
    _write_trial,
    get_license_email,
    get_license_tier,
    get_licensed_features,
    get_trial_days_remaining,
    has_feature,
    has_memory_access,
    has_pro_access,
    is_expired,
    is_in_grace_period,
    is_licensed,
    is_trial_expired,
    load_license,
    save_license,
    verify_key,
)

# ── Helpers ──────────────────────────────────────────────────────────

_TEST_PUBLIC_KEY_HEX = "920614822142ae511a5fcc1d7d20acdc0c8fec0049ce00af226cfa0d12665fc2"
_TEST_PRIVATE_KEY_HEX = "c10e1d9f6d57c919cae9955cd8a2060ad5c9aa667f5a821db0b8ed2a42fcfc72"


def _generate_test_key(
    email: str = "test@example.com",
    tier: str = "starter",
    exp: int | None = None,
) -> str:
    """Generate a valid license key using a TEST-ONLY keypair (not production)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    PRIVATE_KEY_HEX = _TEST_PRIVATE_KEY_HEX
    private_bytes = bytes.fromhex(PRIVATE_KEY_HEX)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

    now = int(time.time())
    payload = {"email": email, "tier": tier, "ts": now}
    if exp is not None:
        payload["exp"] = exp
    else:
        payload["exp"] = now + 365 * 86400  # 1 year

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


@pytest.fixture(autouse=True)
def isolated_license(tmp_path, monkeypatch):
    """Redirect license files to temp directory and swap in test keypair."""
    import contextpulse_core.license as lic_mod

    test_appdata = tmp_path / "ContextPulse"
    monkeypatch.setattr(lic_mod, "APPDATA_DIR", test_appdata)
    monkeypatch.setattr(lic_mod, "LICENSE_FILE", test_appdata / "license.key")
    monkeypatch.setattr(lic_mod, "TRIAL_FILE", test_appdata / "trial.json")
    # Use test-only public key (matches _TEST_PRIVATE_KEY_HEX above)
    monkeypatch.setattr(lic_mod, "_PUBLIC_KEY_HEX", _TEST_PUBLIC_KEY_HEX)

    yield test_appdata


# ── verify_key tests ─────────────────────────────────────────────────

class TestVerifyKey:
    def test_valid_key(self):
        key = _generate_test_key()
        payload = verify_key(key)
        assert payload is not None
        assert payload["email"] == "test@example.com"
        assert payload["tier"] == "starter"

    def test_invalid_key_returns_none(self):
        assert verify_key("garbage.data") is None

    def test_empty_string_returns_none(self):
        assert verify_key("") is None

    def test_tampered_payload_returns_none(self):
        key = _generate_test_key()
        # Flip a character in the payload portion
        parts = key.split(".")
        tampered = parts[0][:-1] + ("A" if parts[0][-1] != "A" else "B")
        assert verify_key(f"{tampered}.{parts[1]}") is None

    def test_pro_tier(self):
        key = _generate_test_key(tier="pro")
        payload = verify_key(key)
        assert payload["tier"] == "pro"


# ── save/load license tests ──────────────────────────────────────────

class TestSaveLoadLicense:
    def test_save_valid_key(self, isolated_license):
        key = _generate_test_key()
        payload = save_license(key)
        assert payload is not None
        assert payload["email"] == "test@example.com"

    def test_save_invalid_key_returns_none(self, isolated_license):
        assert save_license("invalid.key") is None

    def test_load_after_save(self, isolated_license):
        key = _generate_test_key(email="user@test.com", tier="pro")
        save_license(key)
        payload = load_license()
        assert payload["email"] == "user@test.com"
        assert payload["tier"] == "pro"

    def test_load_no_file_returns_none(self, isolated_license):
        assert load_license() is None


# ── is_licensed / is_expired tests ───────────────────────────────────

class TestLicenseStatus:
    def test_is_licensed_with_valid_key(self, isolated_license):
        key = _generate_test_key()
        save_license(key)
        assert is_licensed() is True

    def test_is_licensed_false_when_no_key(self, isolated_license):
        assert is_licensed() is False

    def test_is_licensed_true_during_grace_period(self, isolated_license):
        # Expired 1 hour ago, but within 3-day grace period
        key = _generate_test_key(exp=int(time.time()) - 3600)
        save_license(key)
        assert is_licensed() is True
        assert is_expired() is True
        assert is_in_grace_period() is True

    def test_is_licensed_false_past_grace_period(self, isolated_license):
        # Expired 4 days ago (past the 3-day grace period)
        key = _generate_test_key(exp=int(time.time()) - 4 * 86400)
        save_license(key)
        assert is_licensed() is False

    def test_is_expired_true(self, isolated_license):
        key = _generate_test_key(exp=int(time.time()) - 3600)
        save_license(key)
        assert is_expired() is True

    def test_is_expired_false_for_valid(self, isolated_license):
        key = _generate_test_key()
        save_license(key)
        assert is_expired() is False

    def test_get_license_tier(self, isolated_license):
        key = _generate_test_key(tier="pro")
        save_license(key)
        assert get_license_tier() == "pro"

    def test_get_license_email(self, isolated_license):
        key = _generate_test_key(email="alice@test.com")
        save_license(key)
        assert get_license_email() == "alice@test.com"


# ── Trial system tests ───────────────────────────────────────────────

class TestTrial:
    def test_trial_starts_at_7_days(self, isolated_license):
        days = get_trial_days_remaining()
        assert days == 7

    def test_trial_not_expired_initially(self, isolated_license):
        assert is_trial_expired() is False

    def test_trial_expired_after_time(self, isolated_license, monkeypatch):

        # Write trial start 8 days ago (using _write_trial for HMAC)
        _write_trial({"start": int(time.time()) - 8 * 86400})

        assert is_trial_expired() is True
        assert get_trial_days_remaining() == 0

    def test_trial_tampered_file_treated_as_expired(self, isolated_license):
        """Manually editing trial.json to reset start time is detected as tampering."""
        # Start a valid trial
        get_trial_days_remaining()

        # Tamper: rewrite the file with a fresh start but wrong HMAC
        appdata = isolated_license
        tampered = {"start": int(time.time()), "hmac": "deadbeef" * 8}
        (appdata / "trial.json").write_text(json.dumps(tampered))

        # Should be treated as expired (start=0 → maximally expired)
        assert is_trial_expired() is True
        assert get_trial_days_remaining() == 0


# ── has_memory_access tests ──────────────────────────────────────────

class TestMemoryAccess:
    def test_has_access_with_license(self, isolated_license):
        key = _generate_test_key(tier="starter")
        save_license(key)
        assert has_memory_access() is True

    def test_has_access_during_trial(self, isolated_license):
        assert has_memory_access() is True  # trial starts fresh

    def test_no_access_after_trial_expired(self, isolated_license):
        _write_trial({"start": int(time.time()) - 8 * 86400})
        assert has_memory_access() is False


# -- has_pro_access tests ----------------------------------------------------

class TestProAccess:
    def test_pro_access_with_pro_license(self, isolated_license):
        key = _generate_test_key(tier="pro")
        save_license(key)
        assert has_pro_access() is True

    def test_pro_access_with_starter_license(self, isolated_license):
        key = _generate_test_key(tier="starter")
        save_license(key)
        assert has_pro_access() is True

    def test_pro_access_during_trial(self, isolated_license):
        assert has_pro_access() is True  # trial starts fresh

    def test_no_pro_access_after_trial_expired(self, isolated_license):
        _write_trial({"start": int(time.time()) - 8 * 86400})
        assert has_pro_access() is False

    def test_no_pro_access_with_expired_license(self, isolated_license):
        # Expired 4 days ago (past 3-day grace period)
        key = _generate_test_key(tier="pro", exp=int(time.time()) - 4 * 86400)
        save_license(key)
        # Also expire the trial
        _write_trial({"start": int(time.time()) - 8 * 86400})
        assert has_pro_access() is False


# -- Feature-based access tests ----------------------------------------------

class TestFeatureAccess:
    def test_has_feature_with_license(self, isolated_license):
        key = _generate_test_key(tier="pro")
        save_license(key)
        assert has_feature("search_all_events") is True
        assert has_feature("get_event_timeline") is True

    def test_has_feature_during_trial(self, isolated_license):
        assert has_feature("search_all_events") is True
        assert has_feature("get_event_timeline") is True

    def test_no_feature_after_trial_expired(self, isolated_license):
        _write_trial({"start": int(time.time()) - 8 * 86400})
        assert has_feature("search_all_events") is False

    def test_unknown_feature_returns_false(self, isolated_license):
        key = _generate_test_key(tier="pro")
        save_license(key)
        assert has_feature("nonexistent_feature") is False

    def test_get_licensed_features_with_license(self, isolated_license):
        key = _generate_test_key(tier="starter")
        save_license(key)
        features = get_licensed_features()
        assert "search_all_events" in features
        assert "get_event_timeline" in features

    def test_get_licensed_features_no_license(self, isolated_license):
        assert get_licensed_features() == []

    def test_get_licensed_features_expired_past_grace(self, isolated_license):
        # Expired 4 days ago (past 3-day grace period)
        key = _generate_test_key(tier="pro", exp=int(time.time()) - 4 * 86400)
        save_license(key)
        assert get_licensed_features() == []

    def test_get_licensed_features_in_grace_period(self, isolated_license):
        # Expired 1 hour ago, within grace period -- features still available
        key = _generate_test_key(tier="pro", exp=int(time.time()) - 3600)
        save_license(key)
        features = get_licensed_features()
        assert "search_all_events" in features
