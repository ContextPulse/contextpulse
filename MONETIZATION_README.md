# ContextPulse Monetization Backend

End-to-end reference for the licensing system: architecture, key management,
deployment, and how to add new Pro features.

---

## Architecture Overview

```
  User purchases on Gumroad
         |
         | POST /webhook (HMAC-SHA256 signed)
         v
  API Gateway (REGIONAL, 5 req/s throttle)
         |
         v
  Lambda: contextpulse-license-webhook
         |
         |-- Validate Gumroad signature
         |-- Extract email + tier + sale_id
         |-- Idempotency check (DynamoDB: sale_id)
         |-- Generate Ed25519-signed license key
         |         (private key from SSM Parameter Store)
         |-- Store record in DynamoDB (contextpulse-licenses)
         |-- Email license key to buyer via SES
         |
         v
  Buyer receives email with license key
         |
         v
  Client app: Settings -> Enter License Key
         |
         | (offline — no network call)
         v
  contextpulse_core.licensing.verify_license()
         |
         |-- Decode base64url payload
         |-- Verify Ed25519 signature (embedded public key)
         |-- Check expiry + grace period
         |-- Cache result in memory (1 hour)
         v
  LicenseInfo { email, tier, expiry, is_valid, features }
         |
         v
  is_pro_feature_enabled("memory_search", license_info)
         |  reads config/pro_features.yaml
         v
  Feature gated (True = allowed, False = blocked)
```

**Key property: client-side verification is fully offline.**
No network call is needed to verify a license key. The Ed25519 public key is
embedded in the binary. Only the Lambda needs network access (to DynamoDB + SES).

---

## How to Add a New Pro Feature

### Step 1 — Add to `config/pro_features.yaml`

```yaml
pro_features:
  - memory_search
  - memory_semantic_search
  - search_all_events
  - get_event_timeline
  - your_new_feature     # add here
```

### Step 2 — Gate the feature in your code

```python
from contextpulse_core.licensing import verify_license_embedded, is_pro_feature_enabled
from contextpulse_core.license import load_license  # existing module

# In your MCP tool or feature entrypoint:
def _require_pro(feature_name: str) -> None:
    """Raise an error if the user doesn't have Pro access for this feature."""
    license_key_text = _read_license_key()  # however you load the stored key
    info = verify_license_embedded(license_key_text) if license_key_text else None

    if not is_pro_feature_enabled(feature_name, info):
        raise PermissionError(
            f"'{feature_name}' requires ContextPulse Pro. "
            "Purchase at https://contextpulse.ai or activate your license key."
        )

# Then in your feature:
def my_pro_tool(args):
    _require_pro("your_new_feature")
    # ... rest of implementation
```

### Step 3 — Add to Lambda TIER_FEATURES

In `lambda/license_webhook/handler.py` and `lambda/license_webhook.py`, add
your feature to the `TIER_FEATURES["pro"]` list so new license keys include it:

```python
TIER_FEATURES = {
    "pro": [
        ...
        "your_new_feature",
    ],
}
```

> Note: Existing license keys do NOT need to be regenerated. The `pro_features.yaml`
> check is what blocks/allows access at runtime. Old keys that lack the feature name
> in their `features` list will still pass if the tier is "pro" — the YAML is the
> authoritative gate for feature access, not the key's feature list.

---

## Key Management

### Where keys live

| Key | Location |
|-----|----------|
| Ed25519 private key | AWS SSM Parameter Store: `/contextpulse/license-private-key` (SecureString) |
| Ed25519 public key (hex) | Embedded in `packages/core/src/contextpulse_core/license.py` and `licensing.py` |
| Gumroad webhook secret | AWS SSM: `/contextpulse/gumroad-webhook-secret` (SecureString) |

### NEVER commit the private key. It must stay in SSM only.

### Generate a new key pair

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

# Generate
private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

# Export raw bytes as hex (32 bytes each)
private_hex = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
public_hex = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

print("Private (goes to SSM):", private_hex)
print("Public (embed in binary):", public_hex)
```

### Rotate keys

1. Generate a new key pair (above)
2. Store new private key in SSM: `aws ssm put-parameter --name /contextpulse/license-private-key --value <new_hex> --type SecureString --overwrite`
3. Update `_PUBLIC_KEY_HEX` in `license.py` and `licensing.py`
4. Redeploy Lambda: `cd lambda/ && ./deploy.sh`
5. Rebuild + re-release the client app (new public key embedded)
6. Existing license keys signed with the OLD private key will stop verifying after clients update
   -- batch re-issue new keys to existing customers before deprecating the old key

---

## Testing Locally

### Generate a test license key

```python
import base64, json, time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

# Use the actual private key hex from SSM (local testing only -- never commit)
PRIVATE_KEY_HEX = "<hex from SSM>"

private_bytes = bytes.fromhex(PRIVATE_KEY_HEX)
private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)

now = int(time.time())
exp = now + (365 * 86400)

payload = {
    "email": "test@example.com",
    "tier": "pro",
    "features": ["memory_search", "memory_semantic_search", "search_all_events", "get_event_timeline"],
    "ts": now,
    "exp": exp,
}
payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
signature = private_key.sign(payload_bytes)

key = (
    base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    + "."
    + base64.urlsafe_b64encode(signature).decode().rstrip("=")
)
print("Test license key:", key)
```

### Verify the test key

```python
from contextpulse_core.licensing import verify_license_embedded

info = verify_license_embedded(key)
print("Valid:", info.is_valid if info else False)
print("Tier:", info.tier if info else None)
print("Features:", info.features if info else [])
```

### Test Pro feature gating

```python
from contextpulse_core.licensing import is_pro_feature_enabled

# With valid Pro license
print(is_pro_feature_enabled("memory_search", info))     # True
print(is_pro_feature_enabled("get_screenshot", info))    # True (free feature)

# Without license
print(is_pro_feature_enabled("memory_search", None))     # False
print(is_pro_feature_enabled("get_screenshot", None))    # True (free feature)
```

### Invoke Lambda locally (SAM)

```bash
cd lambda/
sam local invoke LicenseWebhookFunction --event tests/test_event.json
```

---

## Deployment Checklist

### One-time setup

- [ ] Generate Ed25519 key pair (see Key Management above)
- [ ] Store private key in SSM:
  ```bash
  aws ssm put-parameter \
    --name /contextpulse/license-private-key \
    --value <private_key_hex> \
    --type SecureString \
    --region us-east-1
  ```
- [ ] Store Gumroad webhook secret in SSM:
  ```bash
  aws ssm put-parameter \
    --name /contextpulse/gumroad-webhook-secret \
    --value <webhook_secret> \
    --type SecureString \
    --region us-east-1
  ```
- [ ] Verify SES domain:
  ```bash
  aws ses verify-domain-identity --domain contextpulse.ai --region us-east-1
  ```
- [ ] Add DKIM records to Cloudflare DNS (returned by verify-domain-identity)
- [ ] Create S3 bucket for SAM artifacts:
  ```bash
  aws s3 mb s3://contextpulse-sam-artifacts --region us-east-1
  ```

### Deploy Lambda + DynamoDB + API Gateway

```bash
cd C:/Users/david/Projects/ContextPulse/lambda/
chmod +x deploy.sh
./deploy.sh         # prod
./deploy.sh staging # staging
```

The SAM deploy creates:
- DynamoDB table: `contextpulse-licenses` (PITR enabled)
- Lambda: `contextpulse-license-webhook` (Python 3.12, 128MB, 30s timeout)
- API Gateway: POST /webhook endpoint
- CloudWatch alarm: fires on any Lambda error

### Create usage table (manual — not in SAM template yet)

```bash
aws dynamodb create-table \
  --table-name contextpulse-usage \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions \
    AttributeName=email,AttributeType=S \
    AttributeName=feature_month,AttributeType=S \
  --key-schema \
    AttributeName=email,KeyType=HASH \
    AttributeName=feature_month,KeyType=RANGE \
  --region us-east-1
```

### Post-deploy verification

- [ ] Note the `WebhookUrl` from SAM output
- [ ] Configure Gumroad product webhook URL (see Gumroad Setup below)
- [ ] Send a test webhook (curl or Gumroad test purchase)
- [ ] Verify DynamoDB record created in `contextpulse-licenses`
- [ ] Verify license email received

---

## Gumroad Setup

### Products

| Product | Price | Gumroad Variant | Tier |
|---------|-------|-----------------|------|
| ContextPulse Pro (annual) | $49/yr | (default) | pro |
| ContextPulse Pro (lifetime) | $249 | "Lifetime" | pro |

### Webhook configuration

1. Log in to Gumroad dashboard
2. Go to Settings -> Advanced -> Webhooks
3. Add webhook URL: `https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/webhook`
   (use the `WebhookUrl` output from SAM deploy)
4. Copy the webhook signing secret to SSM (see one-time setup above)

### Product ID validation (optional)

Set `GUMROAD_PRODUCT_ID` Lambda env var to the Gumroad product permalink
(e.g. `contextpulse-pro`) to reject webhooks from other products.

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `packages/core/src/contextpulse_core/license.py` | Low-level Ed25519 verify + trial + feature checks (existing module) |
| `packages/core/src/contextpulse_core/licensing.py` | Public API: LicenseTier, LicenseInfo, verify_license(), is_pro_feature_enabled() |
| `config/pro_features.yaml` | Config-driven Pro feature gate list |
| `lambda/license_webhook.py` | Lambda handler (canonical, SAM-deployed) |
| `lambda/license_webhook/handler.py` | Lambda handler (subfolder layout, zip-deployable) |
| `lambda/template.yaml` | SAM template: Lambda + DynamoDB + API Gateway + CloudWatch |
| `lambda/deploy.sh` | SAM-based production deploy script |
| `lambda/license_webhook/deploy.sh` | Manual zip-based deploy script |
| `infra/dynamodb_schema.md` | DynamoDB table schemas + AWS CLI create commands |
