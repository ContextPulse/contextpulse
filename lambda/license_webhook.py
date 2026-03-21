"""AWS Lambda: Gumroad webhook → generate Ed25519 license key → email via SES.

Adapted from Voiceasy's proven pattern, extended with tier + expiration support.

Environment variables:
    PRIVATE_KEY_HEX: Ed25519 private key (hex-encoded, 64 chars)
    SENDER_EMAIL: From address (e.g., license@contextpulse.ai)
    GUMROAD_PRODUCT_ID: Expected product ID for validation (optional)
"""

import base64
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
usage_table = dynamodb.Table("contextpulse-usage")

PRIVATE_KEY_HEX = os.environ["PRIVATE_KEY_HEX"]
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "license@contextpulse.ai")
GUMROAD_PRODUCT_ID = os.environ.get("GUMROAD_PRODUCT_ID", "")

# Tier mapping: Gumroad variant name → tier string
TIER_MAP = {
    "Memory Starter": "starter",
    "Memory Pro": "pro",
    "starter": "starter",
    "pro": "pro",
}

# Default license duration: 1 year from purchase
LICENSE_DURATION_DAYS = 365


def _detect_tier(params: dict) -> str:
    """Detect tier from Gumroad variant or price."""
    # Try variant name first
    variants = params.get("variants", [None])[0]
    if variants:
        for variant_name, tier_key in TIER_MAP.items():
            if variant_name.lower() in variants.lower():
                return tier_key

    # Fall back to price-based detection
    price_cents = params.get("price", [None])[0]
    price_dollars = int(price_cents) / 100 if price_cents else 0
    if price_dollars >= 49:
        return "pro"
    elif price_dollars >= 29:
        return "starter"
    return "starter"  # default


def _generate_license_key(email: str, tier: str) -> str:
    """Generate an Ed25519-signed license key with tier and expiration."""
    private_bytes = bytes.fromhex(PRIVATE_KEY_HEX)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

    now = int(time.time())
    exp = now + (LICENSE_DURATION_DAYS * 86400)

    payload = {
        "email": email,
        "tier": tier,
        "ts": now,
        "exp": exp,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    signature = private_key.sign(payload_bytes)

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def _send_license_email(email: str, license_key: str, tier: str) -> None:
    """Send the license key via SES."""
    tier_display = "Memory Starter" if tier == "starter" else "Memory Pro"

    ses.send_email(
        Source=SENDER_EMAIL,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": f"Your ContextPulse {tier_display} License Key", "Charset": "UTF-8"},
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
    ContextPulse — Always-on context for AI agents | <a href="https://contextpulse.ai" style="color: #00E676;">contextpulse.ai</a>
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
ContextPulse — Always-on context for AI agents | https://contextpulse.ai
""",
                },
            },
        },
        ReplyToAddresses=[SENDER_EMAIL],
    )


def _record_purchase(email: str, tier: str, price_dollars: float) -> None:
    """Record the purchase in DynamoDB for usage tracking."""
    usage_table.put_item(Item={
        "email": email,
        "tier": tier,
        "price_dollars": str(price_dollars),
        "purchased_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + LICENSE_DURATION_DAYS * 86400),
        ),
    })
    logger.info("Purchase recorded for %s: tier=%s, $%.2f", email, tier, price_dollars)


def lambda_handler(event, context):
    """Handle Gumroad webhook — initial purchase and recurring charges."""
    logger.info("Received webhook event")

    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    params = urllib.parse.parse_qs(body)

    email = params.get("email", [None])[0]
    if not email:
        logger.error("No email in webhook payload")
        return {"statusCode": 400, "body": "Missing email"}

    product_id = params.get("product_id", [None])[0]
    if GUMROAD_PRODUCT_ID and product_id != GUMROAD_PRODUCT_ID:
        logger.warning("Product ID mismatch: got %s, expected %s", product_id, GUMROAD_PRODUCT_ID)
        return {"statusCode": 400, "body": "Invalid product"}

    price_cents = params.get("price", [None])[0]
    price_dollars = int(price_cents) / 100 if price_cents else 0

    tier = _detect_tier(params)
    is_recurring = params.get("is_recurring_charge", ["false"])[0] == "true"

    try:
        if is_recurring:
            # Renewal — generate fresh key with new expiration
            license_key = _generate_license_key(email, tier)
            _send_license_email(email, license_key, tier)
            _record_purchase(email, tier, price_dollars)
            logger.info("Renewal processed for %s (tier=%s, $%.2f)", email, tier, price_dollars)
        else:
            # First purchase
            license_key = _generate_license_key(email, tier)
            logger.info("Generated license key for %s (tier=%s)", email, tier)
            _send_license_email(email, license_key, tier)
            _record_purchase(email, tier, price_dollars)

        return {"statusCode": 200, "body": "OK"}

    except Exception:
        logger.exception("Failed to process webhook for %s", email)
        return {"statusCode": 500, "body": "Internal error"}
