# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Public licensing API for ContextPulse.

This module provides the clean public interface for license verification and
Pro feature gating. It wraps the lower-level `license.py` with:

  - LicenseTier enum
  - LicenseInfo dataclass
  - verify_license() — offline Ed25519 verification with 1-hour in-memory cache
  - is_pro_feature_enabled() — config-driven YAML feature gate

Offline-first: no network call is required to verify a license. The Ed25519
public key is embedded in the binary. The private key lives in AWS SSM only.

Usage::

    from contextpulse_core.licensing import verify_license, is_pro_feature_enabled

    info = verify_license(license_key)
    if is_pro_feature_enabled("memory_search", info):
        # run semantic search
        ...

See MONETIZATION_README.md for key management, deployment, and testing.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config path — relative to this file so it works from editable install + EXE
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent / "config"
_PRO_FEATURES_YAML = _CONFIG_DIR / "pro_features.yaml"

# Fallback feature list if the YAML file is unavailable (e.g. inside EXE)
_FALLBACK_PRO_FEATURES: frozenset[str] = frozenset({
    "memory_search",
    "memory_semantic_search",
    "search_all_events",
    "get_event_timeline",
})

# Cache TTL: re-run crypto verification at most once per hour
_CACHE_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LicenseTier(str, Enum):
    """License tiers available for ContextPulse."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"

    @classmethod
    def from_payload(cls, tier_str: str) -> "LicenseTier":
        """Map a raw tier string from the license payload to a LicenseTier."""
        mapping = {
            "pro": cls.PRO,
            "enterprise": cls.ENTERPRISE,
        }
        return mapping.get((tier_str or "").lower(), cls.FREE)


@dataclass
class LicenseInfo:
    """Verified license information extracted from a signed license key."""

    email: str
    tier: LicenseTier
    expiry: int  # Unix timestamp; 0 means perpetual
    is_valid: bool  # True if signature is valid AND not expired (+ grace period)
    features: list[str] = field(default_factory=list)
    is_expired: bool = False
    is_in_grace_period: bool = False


# ---------------------------------------------------------------------------
# In-memory verification cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    info: LicenseInfo
    cached_at: float


_verify_cache: dict[str, _CacheEntry] = {}

_GRACE_DAYS = 3


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------

def _do_verify(license_key: str, public_key_pem: str) -> Optional[LicenseInfo]:
    """Perform Ed25519 signature verification. Returns LicenseInfo or None."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        pub = load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(pub, Ed25519PublicKey):
            logger.error("Public key is not an Ed25519 key")
            return None

        parts = license_key.strip().split(".")
        if len(parts) != 2:
            logger.debug("License key format invalid: expected 2 dot-separated parts")
            return None

        payload_b64, sig_b64 = parts
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        signature = base64.urlsafe_b64decode(sig_b64 + "==")

        pub.verify(signature, payload_bytes)

        payload = json.loads(payload_bytes)
        if "email" not in payload:
            logger.debug("License payload missing 'email' field")
            return None

        now = time.time()
        exp = payload.get("exp", 0)
        tier = LicenseTier.from_payload(payload.get("tier", ""))
        features = payload.get("features", [])

        is_expired = bool(exp and exp < now)
        grace_deadline = exp + (_GRACE_DAYS * 86400) if exp else 0
        is_in_grace = bool(exp and exp < now <= grace_deadline)
        is_valid = not is_expired or is_in_grace

        return LicenseInfo(
            email=payload["email"],
            tier=tier,
            expiry=exp,
            is_valid=is_valid,
            features=features,
            is_expired=is_expired,
            is_in_grace_period=is_in_grace,
        )

    except Exception:
        logger.debug("License verification failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_license(license_key: str, public_key_pem: str) -> Optional[LicenseInfo]:
    """Verify an Ed25519 license key offline. Returns LicenseInfo or None.

    Results are cached in memory for 1 hour to avoid repeated crypto ops.
    The cache key is the license key string itself (already contains the
    email + tier + expiry in its payload — collisions are not possible).

    Args:
        license_key: The base64url-encoded license key (payload.signature).
        public_key_pem: The Ed25519 public key in PEM format.

    Returns:
        LicenseInfo if the signature is valid, None otherwise.
        Note: a valid-signature expired license returns LicenseInfo with
        is_valid=False and is_expired=True (not None) so callers can
        distinguish "bad key" from "expired license".
    """
    now = time.monotonic()

    # Cache hit?
    cached = _verify_cache.get(license_key)
    if cached and (now - cached.cached_at) < _CACHE_TTL_SECONDS:
        logger.debug("License cache hit for cached entry")
        return cached.info

    # Cache miss — run crypto
    info = _do_verify(license_key, public_key_pem)
    if info is not None:
        _verify_cache[license_key] = _CacheEntry(info=info, cached_at=now)

    return info


def clear_license_cache() -> None:
    """Clear the in-memory verification cache (useful for testing)."""
    _verify_cache.clear()


def _load_pro_features() -> frozenset[str]:
    """Load the Pro feature list from config/pro_features.yaml.

    Falls back to the hardcoded fallback set if the YAML file is missing
    (e.g., running from a PyInstaller EXE without the config directory).
    """
    try:
        import yaml  # type: ignore[import]
        if _PRO_FEATURES_YAML.exists():
            data = yaml.safe_load(_PRO_FEATURES_YAML.read_text(encoding="utf-8"))
            features = data.get("pro_features", [])
            if features:
                return frozenset(features)
    except Exception:
        logger.debug("Failed to load pro_features.yaml, using fallback", exc_info=True)

    return _FALLBACK_PRO_FEATURES


def is_pro_feature_enabled(feature_name: str, license: Optional[LicenseInfo]) -> bool:
    """Check whether a Pro-gated feature is enabled for the given license.

    A feature is enabled if:
      1. It is NOT listed in pro_features.yaml (free feature — always enabled), OR
      2. The user has a valid Pro (or Enterprise) license.

    This is the single choke point for all Pro feature gating. Always call
    this rather than checking license.tier directly.

    Args:
        feature_name: The MCP tool or feature identifier (e.g. "memory_search").
        license: LicenseInfo from verify_license(), or None if no license loaded.

    Returns:
        True if the feature should be accessible, False if it should be blocked.
    """
    pro_features = _load_pro_features()

    if feature_name not in pro_features:
        # Free feature — no license required
        return True

    # Pro feature — requires valid Pro or Enterprise license
    if license is None or not license.is_valid:
        return False

    return license.tier in (LicenseTier.PRO, LicenseTier.ENTERPRISE)


# ---------------------------------------------------------------------------
# Convenience: verify from embedded hex public key (matches license.py)
# ---------------------------------------------------------------------------

# Embedded Ed25519 public key (hex) — matches private key in SSM
# Do NOT store the private key here. Update this when rotating keys.
_EMBEDDED_PUBLIC_KEY_HEX = "6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec"


def _hex_to_pem(public_key_hex: str) -> str:
    """Convert a raw Ed25519 public key hex string to PEM format."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    public_bytes = bytes.fromhex(public_key_hex)
    pub = Ed25519PublicKey.from_public_bytes(public_bytes)
    return pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("utf-8")


def verify_license_embedded(license_key: str) -> Optional[LicenseInfo]:
    """Verify a license against the embedded public key (standard client-side use).

    Convenience wrapper for the common case where the public key is the
    one embedded in the binary. For testing with custom keys, use verify_license()
    directly with a PEM string.
    """
    try:
        pem = _hex_to_pem(_EMBEDDED_PUBLIC_KEY_HEX)
        return verify_license(license_key, pem)
    except Exception:
        logger.debug("Failed to convert embedded public key to PEM", exc_info=True)
        return None
