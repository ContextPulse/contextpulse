"""Ed25519 license verification with tiers, features, and expiration.

Key format: base64url(json_payload) + "." + base64url(ed25519_signature)
Payload: {"email": "...", "tier": "starter|pro", "features": [...], "exp": unix_ts, "ts": unix_ts}

Sight is always free. Licensing gates Pro features (search_all_events, get_event_timeline).
Expired license = warning + nag, NOT hard block.
30-day trial for Pro features on first use.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Ed25519 public key (hex) — used for offline license verification
# Private key lives in Lambda env var only
_PUBLIC_KEY_HEX = "6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec"

APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "ContextPulse"
LICENSE_FILE = APPDATA_DIR / "license.key"
TRIAL_FILE = APPDATA_DIR / "trial.json"

TRIAL_DAYS = 30
EXPIRY_GRACE_DAYS = 3  # Grace period after license expiration before hard block


def _get_public_key():
    """Load the Ed25519 public key from embedded hex."""
    if not _PUBLIC_KEY_HEX:
        logger.debug("No public key configured — licensing disabled")
        return None

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public_bytes = bytes.fromhex(_PUBLIC_KEY_HEX)
    return Ed25519PublicKey.from_public_bytes(public_bytes)


def verify_key(license_key: str) -> dict | None:
    """Verify a license key string. Returns payload dict if valid, None if invalid."""
    try:
        pub = _get_public_key()
        if pub is None:
            return None

        parts = license_key.strip().split(".")
        if len(parts) != 2:
            return None

        payload_b64, sig_b64 = parts
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        signature = base64.urlsafe_b64decode(sig_b64 + "==")

        pub.verify(signature, payload_bytes)

        payload = json.loads(payload_bytes)
        if "email" not in payload:
            return None

        return payload
    except Exception:
        logger.debug("License key verification failed", exc_info=True)
        return None


def is_licensed() -> bool:
    """Check if a valid license key is stored (includes grace period after expiry)."""
    payload = load_license()
    if payload is None:
        return False
    # Check expiration with grace period
    if "exp" in payload:
        grace_deadline = payload["exp"] + (EXPIRY_GRACE_DAYS * 86400)
        if grace_deadline < time.time():
            return False
    return True


def is_expired() -> bool:
    """Check if the stored license exists but has expired (past the nominal expiry date)."""
    payload = load_license()
    if payload is None:
        return False
    if "exp" not in payload:
        return False  # no expiration = perpetual
    return payload["exp"] < time.time()


def is_in_grace_period() -> bool:
    """Check if the license is expired but still within the grace period."""
    payload = load_license()
    if payload is None:
        return False
    if "exp" not in payload:
        return False
    now = time.time()
    return payload["exp"] < now <= payload["exp"] + (EXPIRY_GRACE_DAYS * 86400)


def get_license_tier() -> str:
    """Return the tier from stored license: 'starter', 'pro', or '' if unlicensed."""
    payload = load_license()
    if payload is None:
        return ""
    return payload.get("tier", "")


def load_license() -> dict | None:
    """Load and verify the stored license key. Returns payload or None."""
    if not LICENSE_FILE.exists():
        return None
    try:
        key_text = LICENSE_FILE.read_text(encoding="utf-8").strip()
        return verify_key(key_text)
    except Exception:
        logger.debug("Failed to read license file", exc_info=True)
        return None


def save_license(license_key: str) -> dict | None:
    """Verify and save a license key. Returns payload if valid, None if invalid."""
    payload = verify_key(license_key)
    if payload is None:
        return None

    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(license_key.strip(), encoding="utf-8")
    logger.info("License saved for %s (tier=%s)", payload.get("email", "?"), payload.get("tier", "?"))
    return payload


def get_license_email() -> str | None:
    """Get the email from the stored license, or None."""
    payload = load_license()
    return payload.get("email") if payload else None


# ── Trial system ─────────────────────────────────────────────────────


def _trial_hmac_key() -> bytes:
    """Derive a machine-specific key for trial HMAC.

    Uses a combination of the public key hex and a stable machine identifier
    to prevent simple copy-paste of trial files between machines.
    """
    import hashlib
    import uuid

    # Use the public key as a salt combined with a machine-specific value.
    # getnode() returns the MAC address as a 48-bit int — stable across reboots.
    machine_id = str(uuid.getnode())
    return hashlib.sha256(
        (_PUBLIC_KEY_HEX + ":" + machine_id).encode()
    ).digest()


def _compute_trial_hmac(data: dict) -> str:
    """Compute HMAC-SHA256 over the trial start timestamp."""
    import hashlib
    import hmac

    key = _trial_hmac_key()
    msg = str(data["start"]).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _read_trial() -> dict | None:
    """Read and verify the trial start timestamp file.

    Returns None if the file is missing, corrupt, or has been tampered with
    (HMAC mismatch). A tampered trial file is treated as expired to prevent
    trial extension attacks.
    """
    if not TRIAL_FILE.exists():
        return None
    try:
        data = json.loads(TRIAL_FILE.read_text(encoding="utf-8"))
        if "start" not in data:
            return None
        # Verify HMAC if present; if missing (legacy file), re-sign it
        stored_hmac = data.get("hmac")
        expected_hmac = _compute_trial_hmac(data)
        if stored_hmac is None:
            # Legacy trial file without HMAC — migrate by adding HMAC
            data["hmac"] = expected_hmac
            _write_trial(data)
            return data
        if stored_hmac != expected_hmac:
            logger.warning("Trial file HMAC mismatch — treating as expired")
            return {"start": 0}  # epoch = maximally expired
        return data
    except Exception:
        return None


def _write_trial(data: dict) -> None:
    """Write trial data to disk with HMAC tamper protection."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    data["hmac"] = _compute_trial_hmac(data)
    TRIAL_FILE.write_text(json.dumps(data), encoding="utf-8")


def get_trial_days_remaining() -> int:
    """Return days remaining in Pro trial. Starts trial on first call."""
    data = _read_trial()
    if data is None:
        data = {"start": int(time.time())}
        _write_trial(data)

    elapsed = time.time() - data["start"]
    remaining = TRIAL_DAYS - int(elapsed / 86400)
    return max(remaining, 0)


def is_trial_expired() -> bool:
    """Check if the 30-day Memory trial has expired."""
    return get_trial_days_remaining() <= 0


def has_memory_access() -> bool:
    """Check if user can use Memory features (licensed OR within trial)."""
    if is_licensed():
        tier = get_license_tier()
        return tier in ("starter", "pro")
    return not is_trial_expired()


# -- Feature-based access checks ---------------------------------------------

# Starter: basic memory persistence (store, recall, list, delete)
MEMORY_STARTER_FEATURES = frozenset({
    "memory_store",
    "memory_recall",
    "memory_list",
    "memory_forget",
})

# Pro: everything in Starter + semantic/hybrid search + cross-modal Sight tools
MEMORY_PRO_FEATURES = frozenset({
    *MEMORY_STARTER_FEATURES,
    "memory_search",
    "memory_semantic_search",
    "search_all_events",
    "get_event_timeline",
})

# Backwards-compat alias — Sight gating still uses this name
PRO_FEATURES = MEMORY_PRO_FEATURES

# Tier → canonical feature set (used for key generation and backwards compat)
_TIER_FEATURE_SETS: dict[str, frozenset[str]] = {
    "starter": MEMORY_STARTER_FEATURES,
    "pro": MEMORY_PRO_FEATURES,
}


def get_licensed_features() -> list[str]:
    """Return the list of features unlocked by the current license.

    Falls back to tier-based defaults if the license payload lacks an
    explicit 'features' list (backwards compat with older keys).
    """
    payload = load_license()
    if payload is None:
        return []

    # Check expiration (with grace period)
    if "exp" in payload:
        grace_deadline = payload["exp"] + (EXPIRY_GRACE_DAYS * 86400)
        if grace_deadline < time.time():
            return []

    # Prefer explicit feature list from newer license keys
    features = payload.get("features")
    if features:
        return list(features)

    # Backwards compat: derive features from tier
    tier = payload.get("tier", "")
    feature_set = _TIER_FEATURE_SETS.get(tier, frozenset())
    return list(feature_set)


def has_feature(feature_name: str) -> bool:
    """Check if a specific feature is unlocked (by license OR trial)."""
    if is_licensed():
        return feature_name in get_licensed_features()
    # Trial: all Pro features available during 30-day window
    if not is_trial_expired():
        return feature_name in MEMORY_PRO_FEATURES
    return False


def has_starter_access() -> bool:
    """Check if user can use Starter memory features (CRUD).

    True for: starter license, pro license, or active trial.
    """
    if is_licensed():
        return get_license_tier() in ("starter", "pro")
    return not is_trial_expired()


def has_pro_access() -> bool:
    """Check if user can use Pro features (semantic search, cross-modal).

    True for: pro license or active trial.
    This is the primary check used by _require_pro in MCP tools.
    """
    if is_licensed():
        return get_license_tier() == "pro"
    return not is_trial_expired()


def has_memory_access() -> bool:
    """Alias for has_starter_access() — kept for backwards compatibility."""
    return has_starter_access()
