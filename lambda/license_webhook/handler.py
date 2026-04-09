"""AWS Lambda: Gumroad webhook -> Ed25519 license key -> SES + DynamoDB.

This is the entry point for the contextpulse-license-webhook Lambda function.
It is a thin re-export of the top-level lambda/license_webhook.py handler.

The canonical implementation lives at:
    lambda/license_webhook.py

This file exists to satisfy the lambda/license_webhook/ package layout expected
by some deployment tooling. For SAM-based deployments, use lambda/template.yaml
and lambda/deploy.sh directly.

Environment variables:
    PRIVATE_KEY_HEX        Ed25519 private key (hex, 64 chars) — from SSM
    SENDER_EMAIL           SES verified From address (license@contextpulse.ai)
    GUMROAD_PRODUCT_ID     Gumroad product ID for validation (optional)
    GUMROAD_WEBHOOK_SECRET HMAC-SHA256 signing secret (optional but recommended)

Flow:
    1. Gumroad POSTs form-encoded sale data to API Gateway
    2. Validate HMAC-SHA256 signature (if GUMROAD_WEBHOOK_SECRET is set)
    3. Extract buyer email, product tier, sale_id
    4. Idempotency check: skip if sale_id already processed
    5. Generate Ed25519-signed license key with tier + features + expiry
    6. Store in DynamoDB contextpulse-licenses table
    7. Email license key to buyer via SES
    8. Handle refunds: mark license as revoked
    9. Return 200 OK (or 4xx/5xx on error)

Key format:
    base64url(json_payload) + "." + base64url(ed25519_signature)

    Payload fields:
        email    Buyer email address
        tier     "pro" (all paid purchases are Pro)
        features List of unlocked MCP tool names
        ts       Unix timestamp of issuance
        exp      Unix timestamp of expiration (1 year from purchase)

Deployment:
    cd lambda/
    ./deploy.sh          # prod (uses SAM + SSM for secrets)
    ./deploy.sh staging  # staging

See lambda/DEPLOY_NOTES.md and MONETIZATION_README.md for full details.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

import boto3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ses = boto3.client("ses", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
licenses_table = dynamodb.Table("contextpulse-licenses")
usage_table = dynamodb.Table("contextpulse-usage")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRIVATE_KEY_HEX = os.environ["PRIVATE_KEY_HEX"]
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "license@contextpulse.ai")
GUMROAD_PRODUCT_ID = os.environ.get("GUMROAD_PRODUCT_ID", "")

# Tier mapping: Gumroad variant name fragment -> internal tier string
TIER_MAP = {
    "pro": "pro",
    "memory pro": "pro",
    "lifetime": "pro",
    "memory lifetime": "pro",
    "enterprise": "enterprise",
}

# Features unlocked per tier
TIER_FEATURES: dict[str, list[str]] = {
    "pro": [
        "memory_store",
        "memory_recall",
        "memory_list",
        "memory_forget",
        "memory_search",
        "memory_semantic_search",
        "search_all_events",
        "get_event_timeline",
    ],
    "enterprise": [
        "memory_store",
        "memory_recall",
        "memory_list",
        "memory_forget",
        "memory_search",
        "memory_semantic_search",
        "search_all_events",
        "get_event_timeline",
        # Enterprise-only (future):
        # "cloud_memory",
        # "team_sharing",
        # "audit_log",
    ],
}

LICENSE_DURATION_DAYS = 365


# ---------------------------------------------------------------------------
# Gumroad signature validation
# ---------------------------------------------------------------------------

def _verify_gumroad_signature(body_raw: str, signature_header: str | None) -> bool:
    """Verify Gumroad HMAC-SHA256 webhook signature.

    If GUMROAD_WEBHOOK_SECRET is not configured, skip validation (with warning).
    In production, always set the webhook secret.
    """
    webhook_secret = os.environ.get("GUMROAD_WEBHOOK_SECRET", "")

    if not webhook_secret:
        logger.warning("GUMROAD_WEBHOOK_SECRET not set; skipping signature validation")
        return True

    if not signature_header:
        logger.error("Missing Gumroad-Signature header but secret is configured")
        return False

    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        body_raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Tier detection
# ---------------------------------------------------------------------------

def _detect_tier(params: dict) -> str:
    """Detect license tier from Gumroad variant. Default: 'pro'."""
    variants = params.get("variants", [None])[0]
    if variants:
        variants_lower = variants.lower()
        for variant_name, tier_key in TIER_MAP.items():
            if variant_name in variants_lower:
                return tier_key
    # Any Gumroad sale defaults to Pro (Community is free, no paid Starter tier)
    return "pro"


# ---------------------------------------------------------------------------
# License key generation
# ---------------------------------------------------------------------------

def _generate_license_key(email: str, tier: str) -> str:
    """Generate an Ed25519-signed license key.

    Format: base64url(json_payload) + "." + base64url(ed25519_signature)
    The desktop client verifies using the embedded public key (offline).
    """
    private_bytes = bytes.fromhex(PRIVATE_KEY_HEX)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

    now = int(time.time())
    exp = now + (LICENSE_DURATION_DAYS * 86400)
    features = TIER_FEATURES.get(tier, TIER_FEATURES["pro"])

    payload = {
        "email": email,
        "tier": tier,
        "features": features,
        "ts": now,
        "exp": exp,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key.sign(payload_bytes)

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    return f"{payload_b64}.{sig_b64}"


# ---------------------------------------------------------------------------
# SES email delivery
# ---------------------------------------------------------------------------

def _send_license_email(email: str, license_key: str, tier: str) -> None:
    """Send the license key to the buyer via SES."""
    tier_display = "Pro" if tier == "pro" else tier.title()

    ses.send_email(
        Source=SENDER_EMAIL,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {
                "Data": f"Your ContextPulse {tier_display} License Key",
                "Charset": "UTF-8",
            },
            "Body": {
                "Html": {
                    "Charset": "UTF-8",
                    "Data": f"""<html>
<body style="font-family: 'Inter', 'Segoe UI', Arial, sans-serif; background: #0D1117; color: #E6EDF3; padding: 40px;">
<div style="max-width: 600px; margin: 0 auto;">
  <h1 style="color: #00E676; margin-bottom: 5px;">Thank you for purchasing ContextPulse {tier_display}!</h1>
  <p style="color: #8B949E;">Your license key is below. It unlocks semantic memory search,
  hybrid search, and cross-modal Sight analytics.</p>
  <div style="background: #161B22; padding: 16px 20px; border-radius: 8px;
       border: 1px solid #30363D; margin: 20px 0; word-break: break-all;">
    <code style="color: #00E676; font-size: 14px;">{license_key}</code>
  </div>
  <h3 style="color: #E6EDF3;">How to activate:</h3>
  <ol style="color: #8B949E; line-height: 1.8;">
    <li>Right-click the ContextPulse tray icon</li>
    <li>Select <strong>Settings</strong> &rarr; <strong>Enter License Key</strong></li>
    <li>Paste the key above and click <strong>Activate License</strong></li>
  </ol>
  <p style="color: #8B949E; margin-top: 20px;">
    Your license is valid for {LICENSE_DURATION_DAYS} days from purchase.<br>
    Core features (screen capture, dictation, basic memory) are free forever &mdash; no license needed.
  </p>
  <hr style="border: 1px solid #30363D; margin: 30px 0;">
  <p style="color: #8B949E; font-size: 12px;">
    Keep this email safe. Reply here if you need help.<br>
    ContextPulse &mdash; Always-on context for AI agents |
    <a href="https://contextpulse.ai" style="color: #00E676;">contextpulse.ai</a>
  </p>
</div>
</body>
</html>""",
                },
                "Text": {
                    "Charset": "UTF-8",
                    "Data": f"""Thank you for purchasing ContextPulse {tier_display}!

Your license key:
{license_key}

What's unlocked: semantic memory search, hybrid search, cross-modal Sight analytics.

How to activate:
1. Right-click the ContextPulse tray icon
2. Select Settings -> Enter License Key
3. Paste the key above and click "Activate License"

Your license is valid for {LICENSE_DURATION_DAYS} days.
Core features are free forever -- no license needed.

Keep this email safe. Reply here if you need help.
ContextPulse -- Always-on context for AI agents | https://contextpulse.ai
""",
                },
            },
        },
        ReplyToAddresses=[SENDER_EMAIL],
    )


# ---------------------------------------------------------------------------
# DynamoDB storage
# ---------------------------------------------------------------------------

def _store_license(
    email: str, tier: str, license_key: str, price_dollars: float, sale_id: str,
) -> None:
    """Store license record in DynamoDB (upsert by email)."""
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    exp_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + LICENSE_DURATION_DAYS * 86400),
    )

    licenses_table.put_item(Item={
        "email": email,
        "sale_id": sale_id,
        "tier": tier,
        "features": TIER_FEATURES.get(tier, []),
        "license_key_hash": hashlib.sha256(license_key.encode()).hexdigest(),
        "price_dollars": str(price_dollars),
        "purchased_at": now_iso,
        "expires_at": exp_iso,
        "source": "gumroad",
        "is_active": True,
        "email_sent": False,
    })
    logger.info(
        "License stored: email=%s tier=%s price=%.2f expires=%s sale_id=%s",
        email, tier, price_dollars, exp_iso, sale_id,
    )


def _store_usage_record(email: str, feature: str) -> None:
    """Initialize or update a usage record in contextpulse-usage table."""
    month_key = time.strftime("%Y-%m")
    sk = f"{feature}#{month_key}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        usage_table.update_item(
            Key={"email": email, "feature_month": sk},
            UpdateExpression=(
                "SET #cnt = if_not_exists(#cnt, :zero) + :one, "
                "#lim = if_not_exists(#lim, :default_limit), "
                "last_updated = :now"
            ),
            ExpressionAttributeNames={
                "#cnt": "count",
                "#lim": "limit",
            },
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
                ":default_limit": 10000,
                ":now": now_iso,
            },
        )
    except Exception:
        logger.debug("Failed to update usage record for %s/%s", email, sk, exc_info=True)


# ---------------------------------------------------------------------------
# Stripe stub (future)
# ---------------------------------------------------------------------------

def _handle_stripe_event(event_body: dict) -> dict:
    """Stub for Stripe webhook handling (not yet implemented).

    When Stripe is added as a payment processor:
    1. Verify stripe.Webhook.construct_event() signature
    2. Handle checkout.session.completed -> generate license
    3. Handle invoice.paid -> renew license
    4. Handle charge.refunded -> revoke license
    """
    logger.info("Stripe webhook received (not yet implemented): %s", event_body.get("type"))
    return {"statusCode": 200, "body": "Stripe not yet implemented"}


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """Handle Gumroad (and future Stripe) purchase webhooks.

    Gumroad POSTs form-encoded data on each sale. We:
      1. Validate webhook signature
      2. Extract email, tier, sale_id
      3. Idempotency: skip duplicate sale_ids
      4. Generate Ed25519 license key
      5. Store in DynamoDB
      6. Email to buyer
      7. Return 200 OK
    """
    logger.info("Webhook received: %s %s", event.get("httpMethod"), event.get("path"))

    # Route Stripe events (future) vs Gumroad (current)
    headers = event.get("headers", {}) or {}
    content_type = (
        headers.get("content-type")
        or headers.get("Content-Type")
        or ""
    ).lower()

    if "application/json" in content_type:
        # Likely Stripe (JSON body)
        try:
            body_parsed = json.loads(event.get("body", "{}"))
        except Exception:
            return {"statusCode": 400, "body": "Invalid JSON"}
        return _handle_stripe_event(body_parsed)

    # --- Gumroad: form-encoded body ---
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    # Validate Gumroad signature
    sig_header = headers.get("gumroad-signature") or headers.get("Gumroad-Signature")
    if not _verify_gumroad_signature(body, sig_header):
        logger.error("Webhook signature validation failed")
        return {"statusCode": 403, "body": "Invalid signature"}

    # Parse form-encoded body
    params = urllib.parse.parse_qs(body)

    # Extract and validate email
    email = params.get("email", [None])[0]
    if not email:
        logger.error("No email in webhook payload")
        return {"statusCode": 400, "body": "Missing email"}
    email = email.strip().lower()

    # Validate product ID if configured
    product_id = params.get("product_id", [None])[0]
    if GUMROAD_PRODUCT_ID and product_id != GUMROAD_PRODUCT_ID:
        logger.warning("Product ID mismatch: got=%s expected=%s", product_id, GUMROAD_PRODUCT_ID)
        return {"statusCode": 400, "body": "Invalid product"}

    # Handle refund/chargeback
    refunded = params.get("refunded", ["false"])[0]
    if refunded == "true":
        logger.info("Refund notification for %s", email)
        try:
            licenses_table.update_item(
                Key={"email": email},
                UpdateExpression="SET is_active = :f",
                ExpressionAttributeValues={":f": False},
            )
            logger.info("License deactivated for %s", email)
        except Exception:
            logger.exception("Failed to deactivate license for %s", email)
        return {"statusCode": 200, "body": "Refund acknowledged"}

    # Require sale_id for idempotency
    sale_id = params.get("sale_id", [None])[0]
    if not sale_id:
        logger.error("No sale_id in webhook payload")
        return {"statusCode": 400, "body": "Missing sale_id"}

    # Idempotency check: skip duplicate webhooks
    try:
        existing = licenses_table.get_item(Key={"email": email})
        if "Item" in existing and existing["Item"].get("sale_id") == sale_id:
            logger.info("Duplicate webhook: sale_id=%s email=%s -- skipping", sale_id, email)
            return {"statusCode": 200, "body": "Already processed"}
    except Exception:
        logger.debug("Idempotency check failed", exc_info=True)

    # Parse price
    price_cents = params.get("price", [None])[0]
    price_dollars = int(price_cents) / 100 if price_cents else 0.0

    # Detect tier
    tier = _detect_tier(params)
    logger.info("Processing purchase: email=%s tier=%s price=%.2f", email, tier, price_dollars)

    # Generate license key
    try:
        license_key = _generate_license_key(email, tier)
        logger.info("License key generated for %s (tier=%s)", email, tier)
    except Exception:
        logger.exception("Key generation failed for %s", email)
        return {"statusCode": 500, "body": "Key generation failed"}

    # Store in DynamoDB
    try:
        _store_license(email, tier, license_key, price_dollars, sale_id)
    except Exception:
        logger.exception("DynamoDB storage failed for %s", email)
        return {"statusCode": 500, "body": "Storage failed"}

    # Email to buyer (non-fatal — license is stored; manual re-send is possible)
    try:
        _send_license_email(email, license_key, tier)
        logger.info("License email sent to %s", email)

        # Mark email as sent
        licenses_table.update_item(
            Key={"email": email},
            UpdateExpression="SET email_sent = :t",
            ExpressionAttributeValues={":t": True},
        )
    except Exception:
        logger.exception(
            "Email delivery failed for %s (sale_id=%s) -- license IS stored",
            email, sale_id,
        )
        try:
            licenses_table.update_item(
                Key={"email": email},
                UpdateExpression="SET email_sent = :f, email_failed_at = :ts",
                ExpressionAttributeValues={
                    ":f": False,
                    ":ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
        except Exception:
            logger.exception("Failed to flag email failure in DynamoDB for %s", email)
        return {"statusCode": 200, "body": "License created; email delivery failed"}

    return {"statusCode": 200, "body": "OK"}
