"""Ed25519 license verification with tiers, features, and expiration.

Key format: base64url(json_payload) + "." + base64url(ed25519_signature)
Payload: {"email": "...", "tier": "starter|pro", "features": [...], "exp": unix_ts, "ts": unix_ts}

Sight is always free. Licensing gates Pro features (search_all_events, get_event_timeline).
Expired license = warning + nag, NOT hard block.
7-day trial for Pro features on first use.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Ed25519 public key (hex) — generated for ContextPulse (NOT shared with Voiceasy)
# Private key lives in Lambda env var only
_PUBLIC_KEY_HEX = "6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec"

APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "ContextPulse"
LICENSE_FILE = APPDATA_DIR / "license.key"
TRIAL_FILE = APPDATA_DIR / "trial.json"

TRIAL_DAYS = 7


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
    """Check if a valid, non-expired license key is stored."""
    payload = load_license()
    if payload is None:
        return False
    # Check expiration if present
    if "exp" in payload and payload["exp"] < time.time():
        return False
    return True


def is_expired() -> bool:
    """Check if the stored license exists but has expired."""
    payload = load_license()
    if payload is None:
        return False
    if "exp" not in payload:
        return False  # no expiration = perpetual
    return payload["exp"] < time.time()


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

def _read_trial() -> dict | None:
    """Read the trial start timestamp file."""
    if not TRIAL_FILE.exists():
        return None
    try:
        return json.loads(TRIAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_trial(data: dict) -> None:
    """Write trial data to disk."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    TRIAL_FILE.write_text(json.dumps(data), encoding="utf-8")


def get_trial_days_remaining() -> int:
    """Return days remaining in Memory trial. Starts trial on first call."""
    data = _read_trial()
    if data is None:
        data = {"start": int(time.time())}
        _write_trial(data)

    elapsed = time.time() - data["start"]
    remaining = TRIAL_DAYS - int(elapsed / 86400)
    return max(remaining, 0)


def is_trial_expired() -> bool:
    """Check if the 7-day Memory trial has expired."""
    return get_trial_days_remaining() <= 0


def has_memory_access() -> bool:
    """Check if user can use Memory features (licensed OR within trial)."""
    if is_licensed():
        tier = get_license_tier()
        return tier in ("starter", "pro")
    return not is_trial_expired()


# -- Feature-based access checks ---------------------------------------------

# Known Pro features that require a license
PRO_FEATURES = frozenset({"search_all_events", "get_event_timeline"})


def get_licensed_features() -> list[str]:
    """Return the list of features unlocked by the current license.

    Falls back to tier-based defaults if the license payload lacks an
    explicit 'features' list (backwards compat with older keys).
    """
    payload = load_license()
    if payload is None:
        return []

    # Check expiration
    if "exp" in payload and payload["exp"] < time.time():
        return []

    # Prefer explicit feature list from newer license keys
    features = payload.get("features")
    if features:
        return list(features)

    # Backwards compat: derive features from tier
    tier = payload.get("tier", "")
    if tier in ("starter", "pro"):
        return list(PRO_FEATURES)
    return []


def has_feature(feature_name: str) -> bool:
    """Check if a specific feature is unlocked (by license OR trial)."""
    # Licensed users: check feature list
    if is_licensed():
        return feature_name in get_licensed_features()

    # Trial users: all Pro features are available during trial
    if feature_name in PRO_FEATURES and not is_trial_expired():
        return True

    return False


def has_pro_access() -> bool:
    """Check if user has Pro-tier access (licensed with pro tier OR within trial).

    This is the primary check used by _require_pro in MCP tools.
    """
    if is_licensed():
        tier = get_license_tier()
        if tier == "pro":
            return True
        # Starter tier also gets Pro tools (both tiers unlock the same features)
        if tier == "starter":
            return True
        return False

    # Trial: Pro features available during 7-day trial
    return not is_trial_expired()
