"""AWS Lambda: Gumroad webhook -> generate Ed25519 license key -> email via SES.

Receives Gumroad sale (ping) webhook, validates the request, generates a signed
license key with tier/feature info, stores in DynamoDB, and emails the buyer.

Environment variables:
    PRIVATE_KEY_HEX: Ed25519 private key (hex-encoded, 64 chars)
    SENDER_EMAIL: From address (e.g., license@contextpulse.ai)
    GUMROAD_PRODUCT_ID: Expected product ID for validation (optional)
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

PRIVATE_KEY_HEX = os.environ["PRIVATE_KEY_HEX"]
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "license@contextpulse.ai")
GUMROAD_PRODUCT_ID = os.environ.get("GUMROAD_PRODUCT_ID", "")

# Tier mapping: Gumroad variant name -> tier string
TIER_MAP = {
    "pro": "pro",
    "memory pro": "pro",
    "starter": "starter",
    "memory starter": "starter",
}

# Features unlocked per tier
TIER_FEATURES = {
    "starter": ["search_all_events", "get_event_timeline"],
    "pro": ["search_all_events", "get_event_timeline"],
}

# Default license duration: 1 year from purchase
LICENSE_DURATION_DAYS = 365


# -- Gumroad webhook signature validation ------------------------------------

def _verify_gumroad_signature(body_raw: str, signature_header: str | None) -> bool:
    """Verify the Gumroad webhook signature if present.

    Gumroad signs webhooks with HMAC-SHA256 using the seller's API secret.
    If no signature header is present (older Gumroad config), we allow the
    request through but log a warning. In production, set GUMROAD_WEBHOOK_SECRET
    to enforce validation.
    """
    webhook_secret = os.environ.get("GUMROAD_WEBHOOK_SECRET", "")

    if not webhook_secret:
        # No secret configured -- skip validation but warn
        logger.warning("GUMROAD_WEBHOOK_SECRET not set; skipping signature check")
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


# -- Tier detection -----------------------------------------------------------

def _detect_tier(params: dict) -> str:
    """Detect tier from Gumroad variant or price."""
    # Try variant name first
    variants = params.get("variants", [None])[0]
    if variants:
        for variant_name, tier_key in TIER_MAP.items():
            if variant_name in variants.lower():
                return tier_key

    # Fall back to price-based detection
    price_cents = params.get("price", [None])[0]
    price_dollars = int(price_cents) / 100 if price_cents else 0
    if price_dollars >= 49:
        return "pro"
    elif price_dollars >= 29:
        return "starter"
    return "starter"  # default


# -- License key generation ---------------------------------------------------

def _generate_license_key(email: str, tier: str) -> str:
    """Generate an Ed25519-signed license key with tier, features, and expiration.

    Key format: base64url(json_payload) + "." + base64url(ed25519_signature)
    The desktop app verifies by checking the signature against the embedded public key.
    """
    private_bytes = bytes.fromhex(PRIVATE_KEY_HEX)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

    now = int(time.time())
    exp = now + (LICENSE_DURATION_DAYS * 86400)

    features = TIER_FEATURES.get(tier, [])

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


# -- Email delivery -----------------------------------------------------------

def _send_license_email(email: str, license_key: str, tier: str) -> None:
    """Send the license key via SES with branded HTML + plain text fallback."""
    tier_display = "Memory Starter" if tier == "starter" else "Memory Pro"

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
  <h1 style="color: #00E676; margin-bottom: 5px;">Thank you for purchasing ContextPulse!</h1>
  <p style="color: #8B949E;">Your <strong>{tier_display}</strong> license key is below.</p>
  <div style="background: #161B22; padding: 16px 20px; border-radius: 8px; border: 1px solid #30363D; margin: 20px 0; word-break: break-all;">
    <code style="color: #00E676; font-size: 14px;">{license_key}</code>
  </div>
  <h3 style="color: #E6EDF3;">How to activate:</h3>
  <ol style="color: #8B949E; line-height: 1.8;">
    <li>Right-click the ContextPulse tray icon</li>
    <li>Select <strong>Enter License Key</strong></li>
    <li>Paste the key above and click <strong>Activate License</strong></li>
  </ol>
  <p style="color: #8B949E; margin-top: 20px;">
    Your license is valid for {LICENSE_DURATION_DAYS} days from purchase.
    ContextPulse Sight (screen capture) remains free forever.
  </p>
  <hr style="border: 1px solid #30363D; margin: 30px 0;">
  <p style="color: #8B949E; font-size: 12px;">
    Keep this email safe. If you need help, reply to this email.<br>
    ContextPulse &mdash; Always-on context for AI agents |
    <a href="https://contextpulse.ai" style="color: #00E676;">contextpulse.ai</a>
  </p>
</div>
</body>
</html>""",
                },
                "Text": {
                    "Charset": "UTF-8",
                    "Data": f"""Thank you for purchasing ContextPulse!

Your {tier_display} license key:
{license_key}

How to activate:
1. Right-click the ContextPulse tray icon
2. Select "Enter License Key"
3. Paste the key above and click "Activate License"

Your license is valid for {LICENSE_DURATION_DAYS} days.
ContextPulse Sight (screen capture) remains free forever.

Keep this email safe. Reply to this email if you need help.
ContextPulse -- Always-on context for AI agents | https://contextpulse.ai
""",
                },
            },
        },
        ReplyToAddresses=[SENDER_EMAIL],
    )


# -- DynamoDB storage ---------------------------------------------------------

def _store_license(
    email: str, tier: str, license_key: str, price_dollars: float, sale_id: str,
) -> None:
    """Store the license record in DynamoDB for lookup and audit."""
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
        "status": "active",
    })
    logger.info(
        "License stored for %s: tier=%s, $%.2f, expires=%s, sale_id=%s",
        email, tier, price_dollars, exp_iso, sale_id,
    )


# -- Lambda handler -----------------------------------------------------------

def lambda_handler(event, context):
    """Handle Gumroad webhook -- initial purchase and recurring charges.

    Gumroad POSTs form-encoded data on each sale. We:
    1. Validate the webhook signature (if configured)
    2. Extract buyer email, product ID, tier
    3. Generate a signed Ed25519 license key
    4. Store in DynamoDB
    5. Email the key to the buyer
    6. Return 200 OK
    """
    logger.info("Received webhook event")

    # Decode body
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    # Validate Gumroad webhook signature
    headers = event.get("headers", {})
    # API Gateway v2 lowercases headers
    signature = headers.get("gumroad-signature") or headers.get("Gumroad-Signature")
    if not _verify_gumroad_signature(body, signature):
        logger.error("Webhook signature validation failed")
        return {"statusCode": 403, "body": "Invalid signature"}

    # Parse form-encoded body
    params = urllib.parse.parse_qs(body)

    # Extract and validate email
    email = params.get("email", [None])[0]
    if not email:
        logger.error("No email in webhook payload")
        return {"statusCode": 400, "body": "Missing email"}

    # Normalize email
    email = email.strip().lower()

    # Validate product ID if configured
    product_id = params.get("product_id", [None])[0]
    if GUMROAD_PRODUCT_ID and product_id != GUMROAD_PRODUCT_ID:
        logger.warning(
            "Product ID mismatch: got %s, expected %s",
            product_id, GUMROAD_PRODUCT_ID,
        )
        return {"statusCode": 400, "body": "Invalid product"}

    # Check for refund/chargeback (Gumroad sends these too)
    refunded = params.get("refunded", ["false"])[0]
    if refunded == "true":
        logger.info("Refund notification for %s -- skipping license generation", email)
        # Optionally mark license as revoked in DynamoDB
        try:
            licenses_table.update_item(
                Key={"email": email},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "revoked"},
            )
            logger.info("License revoked for %s", email)
        except Exception:
            logger.exception("Failed to revoke license for %s", email)
        return {"statusCode": 200, "body": "Refund acknowledged"}

    # Extract sale_id for idempotency
    sale_id = params.get("sale_id", [None])[0]
    if not sale_id:
        logger.error("No sale_id in webhook payload")
        return {"statusCode": 400, "body": "Missing sale_id"}

    # Idempotency check: skip if this sale was already processed
    try:
        existing = licenses_table.get_item(Key={"email": email})
        if "Item" in existing and existing["Item"].get("sale_id") == sale_id:
            logger.info("Duplicate webhook for sale_id=%s email=%s -- skipping", sale_id, email)
            return {"statusCode": 200, "body": "Already processed"}
    except Exception:
        logger.debug("Idempotency check failed (table may not exist yet)", exc_info=True)

    # Extract price
    price_cents = params.get("price", [None])[0]
    price_dollars = int(price_cents) / 100 if price_cents else 0

    # Detect tier
    tier = _detect_tier(params)

    # Generate license key
    try:
        license_key = _generate_license_key(email, tier)
        logger.info("Generated license key for %s (tier=%s)", email, tier)
    except Exception:
        logger.exception("Failed to generate license key for %s", email)
        return {"statusCode": 500, "body": "Key generation failed"}

    # Store in DynamoDB
    try:
        _store_license(email, tier, license_key, price_dollars, sale_id)
    except Exception:
        logger.exception("Failed to store license in DynamoDB for %s", email)
        return {"statusCode": 500, "body": "Storage failed"}

    # Email to buyer (non-fatal: license is stored, customer can request re-send)
    try:
        _send_license_email(email, license_key, tier)
        logger.info("License email sent to %s", email)
    except Exception:
        logger.exception(
            "License stored but email delivery failed for %s (sale_id=%s). "
            "Manual re-send required.",
            email, sale_id,
        )
        # Return 200 -- license IS created, email is a delivery concern
        return {"statusCode": 200, "body": "License created, email delivery failed"}

    return {"statusCode": 200, "body": "OK"}
